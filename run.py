"""entropybench: how many things can a model be handed at once before some go missing?

    python3 run.py --base-url https://api.example.com/v1 --env-var API_KEY --model some-model

For each load (10 to 500 jobs by default) a fresh copy of a generated project
is made, the whole backlog is given to the model in ONE message, and the
model works through it with file tools until it says it is done. Then every
job is checked by code; nothing is judged by a model.

  done      the job's own check passes
  dropped   the files it concerns were never changed: the model lost track of it
  botched   it was attempted and came out wrong, including doing the first
            version of something corrected later, or something called off
  spill     files no job is about that were changed, created or deleted

  entropy   (dropped + botched + spill) / jobs, per load: the share of the
            hand-over that did not come back in order. 0 is perfect.
  load limit  the largest load at which entropy is still at or under 10%,
            with every smaller load also under it.

--mix tangled (the default) says the backlog the way a person does: with
corrections, things called off, things said twice, jobs that depend on the
project or on each other, and a report at the end. --mix plain keeps every
job separate. With --drip K, half of what is said is in the first message
and the rest arrives in K more messages while the model is already working.

Run `python3 selftest.py` first: it checks every check without a model.
Results go to results/<model>/ and a table is printed.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

import agent
import world


def snapshot(root):
    files = {}
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            try:
                files[os.path.relpath(path, root)] = open(path).read()
            except (OSError, UnicodeDecodeError):
                files[os.path.relpath(path, root)] = "<unreadable>"
    return files


def read(root, path):
    try:
        return open(os.path.join(root, path)).read()
    except (OSError, UnicodeDecodeError):
        return None


def queue_size(queue, load):
    """--queue auto: one at a time up to 100 jobs, ten at a time above that (a thousand round trips is too slow)."""
    return (1 if load <= 100 else 10) if queue == "auto" else int(queue)


def delivery(args, load):
    if args.queue:
        return f"queued, {queue_size(args.queue, load)} at a time"
    if args.steer:
        return "corrections mid-run"
    return f"drip {args.drip}" if args.drip else "one message"


def provenance(args):
    """Which code made a result: a hash of the files that decide the jobs, the agent and the
    scoring, and with --compact the pi-fabric commit whose compactor ran."""
    here = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for name in ("world.py", "agent.py", "run.py", "compact.ts"):
        with open(os.path.join(here, name), "rb") as f:
            digest.update(f.read())
    out = {"code": digest.hexdigest()[:12], "date": datetime.date.today().isoformat()}
    if args.compact:
        fabric = os.environ.get("PI_FABRIC") or os.path.join(os.path.dirname(here), "pi-fabric")
        commit = subprocess.run(["git", "-C", fabric, "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True).stdout.strip()
        out["pi_fabric"] = commit or "unknown"
    return out


# Both CoralBricks models serve a 1,048,576-token window.
WINDOW = 1048576


def variant(args):
    v = f"-drip{args.drip}" if args.drip else "-queued" if args.queue else "-steer" if args.steer else ""
    v += f"-pad{args.pad}" if args.pad else ""
    if args.compact:
        v += "-compact" + (f"-at{args.compact_at // 1000}k" if args.compact_at else "") + (f"-window{args.window // 1000}k" if args.window != WINDOW else "")
    return v


def one(args, key, load, seed):
    files, jobs, asides = world.build(seed, load, args.mix, pad=args.pad)
    root = tempfile.mkdtemp(prefix="entropybench-")
    try:
        for path, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, path)) or root, exist_ok=True)
            with open(os.path.join(root, path), "w") as f:
                f.write(text)
        lines = world.said(jobs, asides, seed)
        deliver, extra_steps, steered = None, 0, None
        if args.queue:
            # The old way: one item (or a few) at a time, the next only once the model says it is done.
            size = queue_size(args.queue, load)
            batches = [lines[i:i + size] for i in range(0, len(lines), size)]
            request = world.brief(batches[0], seed, args.style)
            waiting = [world.brief(b, seed + i + 1, args.style, opening=False) for i, b in enumerate(batches[1:])]
            extra_steps = len(waiting)

            def deliver(step, idle):
                return [waiting.pop(0)] if idle and waiting else []
            later = []
        elif args.steer:
            # Corrections and call-offs are held back and said mid-run, once the model has touched the
            # job they are about (so it must go back and change what it did), or when it stops.
            tagged = world.said(jobs, asides, seed, tagged=True)
            request, later = world.brief([t for t, job in tagged if job is None], seed, args.style), []
            held = [(t, job) for t, job in tagged if job is not None]
            extra_steps = len(held)
            steered = {"corrections": len(held), "said_mid_run": 0, "said_after_it_stopped": 0}

            def touched(job):
                return any(read(root, p) != files.get(p) for p in job.targets)

            def deliver(step, idle):
                due = [h for h in held if idle or touched(h[1])]
                for h in due:
                    held.remove(h)
                steered["said_after_it_stopped" if idle else "said_mid_run"] += len(due)
                return [world.brief([t], seed, args.style, opening=False) for t, _ in due]
        elif args.drip and len(lines) > 2 * (args.drip + 1):
            # Half up front, the rest in `drip` further messages that land while the model is working.
            # The end-of-run reports stay with the last message.
            half = len(lines) // 2
            size = -(-(len(lines) - half) // args.drip)
            request = world.brief(lines[:half], seed, args.style)
            later = [(2 + 3 * i, "A few more while you're at it. " + world.brief(lines[half + i * size:half + (i + 1) * size], seed + i + 1, args.style, opening=False))
                     for i in range(args.drip) if lines[half + i * size:half + (i + 1) * size]]
        else:
            request, later = world.brief(lines, seed, args.style), []
        stats = agent.run(args.base_url, key, args.model, root, request, max_steps=6 * load + 30 + extra_steps,
                          extra=json.loads(args.extra) if args.extra else None, later=later, deliver=deliver,
                          tool_cap=max(20000, 2 * args.pad),
                          compactor=agent.Compactor(window=args.window, at=args.compact_at) if args.compact else None)
        after = snapshot(root)
        outcomes, spill = world.score(files, jobs, after)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    count = {o: outcomes.count(o) for o in ("done", "dropped", "botched")}
    groups = {}
    for job, o in zip(jobs, outcomes):
        groups.setdefault(job.group, {"jobs": 0, "done": 0})
        groups[job.group]["jobs"] += 1
        groups[job.group]["done"] += o == "done"
    return {"load": load, "seed": seed, "pad": args.pad, "delivery": delivery(args, load), **({"steered": steered} if steered else {}), **count, "spill": len(spill), "entropy": round((count["dropped"] + count["botched"] + len(spill)) / load, 4),
            "by_group": groups,
            # What a job that went wrong left behind, so a too-strict check can be told from a real miss.
            "not_done": [{"kind": job.kind, "group": job.group, "outcome": o, "ask": job.wordings[0], "later": job.follow or job.again,
                          "left": {p: after.get(p, "<no such file>")[:400] for p in job.targets}}
                         for job, o in zip(jobs, outcomes) if o != "done"],
            "spilled": spill, **stats,
            # A run that ended for a reason outside the model's control measures nothing.
            "clean": stats["ended"] in ("the model said it was done", "step limit", "context window full")}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--env-var", required=True, help="name of the environment variable holding the API key")
    parser.add_argument("--model", required=True)
    parser.add_argument("--mix", default="tangled", choices=["tangled", "plain"],
                        help="tangled: with corrections, call-offs, repeats, conditions and reports; plain: every job stands alone")
    parser.add_argument("--loads", default="10,25,50,100,250,500")
    parser.add_argument("--seeds", default="1", help="comma-separated; each seed is a different project and set of jobs")
    parser.add_argument("--style", default="ramble", choices=["ramble", "list"], help="ramble: run together like a spoken backlog; list: numbered")
    parser.add_argument("--drip", type=int, default=0, help="deliver half the jobs up front and the rest in this many later messages, mid-run")
    parser.add_argument("--queue", help="the old way, for comparison: say the backlog N items at a time, the next only once the model says it is done; "
                                         "'auto' = 1 at a time up to 100 jobs, 10 above")
    parser.add_argument("--steer", action="store_true",
                        help="hold corrections and call-offs back; say each mid-run once the model has touched that job (or when it stops)")
    parser.add_argument("--pad", type=int, default=0,
                        help="long-context mode: grow every file to about this many characters with content no job is about; "
                             "the jobs stay the same, reading them costs more context")
    parser.add_argument("--compact", action="store_true",
                        help="compact the context the way Pi Fabric does (deterministic, no model call) instead of ending the run when the window fills; "
                             "needs bun and pi-fabric (PI_FABRIC, else ../pi-fabric)")
    parser.add_argument("--compact-at", type=int, help="compact once the context passes this many tokens (default: the window minus Pi's 16,384-token reserve)")
    parser.add_argument("--window", type=int, default=WINDOW, help="the model's context window, in tokens")
    parser.add_argument("--extra", help="JSON merged into every request, e.g. '{\"reasoning_effort\": \"high\"}'")
    parser.add_argument("--out", default="results")
    args = parser.parse_args()
    if sum(map(bool, (args.drip, args.queue, args.steer))) > 1:
        parser.error("--drip, --queue and --steer are separate ways of delivering the backlog; pick one")
    key = os.environ[args.env_var]
    loads = [int(x) for x in args.loads.split(",")]
    runs = []
    for seed in [int(x) for x in args.seeds.split(",")]:
        for load in loads:
            r = one(args, key, load, seed)
            r["provenance"] = provenance(args)
            runs.append(r)
            print(f"load {load:3d} seed {seed}: done {r['done']:3d}  dropped {r['dropped']:3d}  botched {r['botched']:3d}  spill {r['spill']:2d}  "
                  f"entropy {r['entropy']:.2f}  | {r['steps']} steps, {r['tool_calls']} tool calls, peak context {r['peak_context'] // 1000}k, {r['seconds']:.0f}s, {f"{len(r['compactions'])} compactions, {r['recalls']} recalls, " if 'compactions' in r else ''}{r['ended']}{'' if r['clean'] else '  (NOT A CLEAN MEASUREMENT)'}", flush=True)
            folder = os.path.join(args.out, args.model.replace("/", "_"))
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{args.mix}-{args.style}{variant(args)}-seed{seed}.json")
            # Keep loads run earlier for this seed, so a run of one more load adds to the file instead of replacing it.
            mine = [x for x in runs if x["seed"] == seed]
            kept = [x for x in json.load(open(path))["runs"] if x["load"] not in {m["load"] for m in mine}] if os.path.exists(path) else []
            with open(path, "w") as f:
                json.dump({"model": args.model, "base_url": args.base_url, "mix": args.mix, "style": args.style, "drip": args.drip,
                           "queue": args.queue, "steer": args.steer, "pad": args.pad, "extra": args.extra,
                           "compact": {"at": args.compact_at, "window": args.window} if args.compact else None,
                           "runs": sorted(kept + mine, key=lambda x: x["load"])}, f, indent=1)
    limit = 0
    for load in loads:
        mean = sum(r["entropy"] for r in runs if r["load"] == load) / max(1, sum(r["load"] == load for r in runs))
        if mean > 0.10:
            break
        limit = load
    print(f"load limit (entropy at or under 10% up to this load): {limit}")
    groups = {}
    for r in runs:
        for name, g in r["by_group"].items():
            groups.setdefault(name, [0, 0])
            groups[name][0] += g["done"]
            groups[name][1] += g["jobs"]
    print("done, by what the job tests:")
    for name, (done, total) in sorted(groups.items(), key=lambda kv: kv[1][0] / kv[1][1]):
        print(f"  {name:32s} {done:4d} of {total:4d}  ({100 * done / total:.0f}%)")


if __name__ == "__main__":
    main()
