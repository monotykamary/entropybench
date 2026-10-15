"""Put every saved run side by side: entropy per load, per model, over all seeds.

    python3 report.py [--mix tangled] [--style ramble] [--variant queued|steer | --context] [--json out.json]

Reads results/<model>/<mix>-<style>-seed<N>.json (drip runs are left out).
With --variant, the backlog delivered another way (queued: a few at a time, the
next once the model stops; steer: corrections said mid-run) is put next to the
same loads and seeds said in one message, paired. Two numbers, because a
queue does two different things:

  tracking loss  share of the stand-alone jobs missed when told at once, minus
                 the share missed when queued. What the model loses by keeping
                 track itself; near 0 means it no longer needs a queue.
  linked jobs    the same for corrections, call-offs and jobs that depend on
                 another: usually negative for a queue, which hands a job over
                 before the thing said later that changes it.

The no-queue limit is the largest load whose tracking loss is at most 2
points (or at most one job per seed, so one slip at a small load does not
decide it), with every smaller load also within it.
A load's entropy is the mean over the seeds that ran it; runs that ended for a
reason outside the model's control (a failed request, a reply cut off by the
output limit) are left out and counted separately. The load limit is the
largest load whose mean entropy is at or under 10%, with every smaller load
also under it.
"""
import argparse
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def clean(run):
    return run.get("clean", run.get("ended") in ("the model said it was done", "step limit", "context window full"))


GAP = 0.02
# Jobs that stand alone. On these a queue can only help (nothing it says later changes them), so the
# difference here is what the model loses by keeping track itself. Corrections, call-offs and jobs that
# lean on another job are different: a queue hands them over before the thing that changes them.
PLAIN = ("plain", "plain (another job hangs on it)")


def plain_jobs(run):
    return sum(v["jobs"] for k, v in run["by_group"].items() if k in PLAIN)


def missed(run, groups):
    g = run["by_group"]
    jobs = sum(g[k]["jobs"] for k in g if (k in PLAIN) == (groups == "plain"))
    done = sum(g[k]["done"] for k in g if (k in PLAIN) == (groups == "plain"))
    return (jobs - done) / jobs if jobs else 0.0


def gather(mix, style, variant=""):
    models = {}
    for path in sorted(glob.glob(os.path.join(HERE, "results", "*", f"{mix}-{style}{variant}-seed*.json"))):
        if not re.search(rf"{mix}-{style}{variant}-seed\d+\.json$", path):
            continue
        data = json.load(open(path))
        for run in data["runs"]:
            models.setdefault(data["model"], []).append(run)
    return models


def summarise(runs):
    loads = sorted({r["load"] for r in runs})
    rows = []
    for load in loads:
        good = [r for r in runs if r["load"] == load and clean(r)]
        bad = [r for r in runs if r["load"] == load and not clean(r)]
        if not good:
            rows.append({"load": load, "seeds": 0, "unclean": len(bad)})
            continue
        n = len(good)
        rows.append({"load": load, "seeds": n, "unclean": len(bad),
                     "entropy": round(sum(r["entropy"] for r in good) / n, 4),
                     "worst": max(r["entropy"] for r in good),
                     "done": round(sum(r["done"] for r in good) / n, 1),
                     "dropped": round(sum(r["dropped"] for r in good) / n, 1),
                     "botched": round(sum(r["botched"] for r in good) / n, 1),
                     "seconds": round(sum(r["seconds"] for r in good) / n),
                     "input_tokens": round(sum(r["input_tokens"] for r in good) / n),
                     "output_tokens": round(sum(r["output_tokens"] for r in good) / n)})
    limit = 0
    for row in rows:
        if "entropy" not in row or row["entropy"] > 0.10:
            break
        limit = row["load"]
    groups = {}
    for r in runs:
        if clean(r):
            for name, g in r["by_group"].items():
                groups.setdefault(name, [0, 0])
                groups[name][0] += g["done"]
                groups[name][1] += g["jobs"]
    return {"rows": rows, "load_limit": limit, "by_group": {k: {"done": d, "jobs": j} for k, (d, j) in groups.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mix", default="tangled")
    parser.add_argument("--style", default="ramble")
    parser.add_argument("--variant", choices=["queued", "steer"], help="compare runs delivered this way with the one-message runs")
    parser.add_argument("--context", action="store_true", help="long-context mode: entropy against peak context, by padding level")
    parser.add_argument("--json", help="also write the summary here")
    args = parser.parse_args()
    if args.variant:
        return compare(args)
    if args.context:
        return context(args)
    out = {}
    for model, runs in gather(args.mix, args.style).items():
        s = out[model] = summarise(runs)
        print(f"\n{model}  ({args.mix}, {args.style})  load limit: {s['load_limit']}")
        print(f"  {'load':>5} {'seeds':>5} {'entropy':>8} {'worst':>6} {'done':>7} {'dropped':>8} {'botched':>8} {'time':>6}")
        for row in s["rows"]:
            if "entropy" not in row:
                print(f"  {row['load']:>5} {0:>5}   (only unclean runs: {row['unclean']})")
                continue
            note = f"  +{row['unclean']} unclean" if row["unclean"] else ""
            print(f"  {row['load']:>5} {row['seeds']:>5} {row['entropy']:>8.3f} {row['worst']:>6.2f} {row['done']:>7} {row['dropped']:>8} {row['botched']:>8} {row['seconds']:>5}s{note}")
        print("  done, by what the job tests:")
        for name, g in sorted(s["by_group"].items(), key=lambda kv: kv[1]["done"] / kv[1]["jobs"]):
            print(f"    {name:32s} {g['done']:5d} of {g['jobs']:5d}  ({100 * g['done'] / g['jobs']:.0f}%)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1)


def context(args):
    """Same load and jobs, files grown with --pad: does the model slip as the context fills,
    and what does compacting (--compact) cost or save?"""
    out = {}
    padded = {}
    for path in sorted(glob.glob(os.path.join(HERE, "results", "*", f"{args.mix}-{args.style}-pad*-seed*.json"))):
        data = json.load(open(path))
        c = data.get("compact")
        how = "none" if not c else f"at {c['at'] // 1000}k" if c.get("at") else "Pi default"
        padded.setdefault(data["model"], []).extend(dict(r, compact=how) for r in data["runs"])
    base = gather(args.mix, args.style)
    for model, runs in padded.items():
        loads = {r["load"] for r in runs}
        runs = [dict(r, compact="none") for r in base.get(model, []) if r["load"] in loads] + runs
        rows = []
        for load, pad, how in sorted({(r["load"], r.get("pad", 0), r["compact"]) for r in runs}, key=lambda k: (k[0], k[1], k[2] != "none", k[2])):
            group = [r for r in runs if r["load"] == load and r.get("pad", 0) == pad and r["compact"] == how]
            good = [r for r in group if clean(r)]
            row = {"load": load, "pad": pad, "compact": how, "seeds": len(good), "unclean": len(group) - len(good),
                   "ended": sorted({r["ended"][:60] for r in group if not clean(r)}),
                   "window_full": sum(r["ended"] == "context window full" for r in good)}
            peaks = [r.get("peak_context") for r in group if r.get("peak_context")]
            row["peak_context"] = round(sum(peaks) / len(peaks)) if peaks else None
            if good:
                n = len(good)
                row.update(entropy=round(sum(r["entropy"] for r in good) / n, 4), worst=max(r["entropy"] for r in good),
                           dropped=round(sum(r["dropped"] for r in good) / n, 1), botched=round(sum(r["botched"] for r in good) / n, 1),
                           seconds=round(sum(r["seconds"] for r in good) / n))
                if how != "none":
                    row.update(compactions=round(sum(len(r.get("compactions", [])) for r in good) / n, 1),
                               recalls=round(sum(r.get("recalls", 0) for r in good) / n, 1))
            rows.append(row)
        out[model] = rows
        print(f"\n{model}  ({args.mix}, {args.style})  long context: same jobs, files grown to `pad` characters")
        print(f"  {'load':>5} {'pad':>6} {'compaction':>11} {'peak ctx':>9} {'seeds':>5} {'entropy':>8} {'worst':>6} {'dropped':>8} {'botched':>8} {'time':>7}  ")
        for r in rows:
            peak = f"{r['peak_context'] // 1000}k" if r["peak_context"] else "n/a"
            if "entropy" in r:
                notes = []
                if "compactions" in r:
                    notes.append(f"{r['compactions']} compactions, {r['recalls']} recalls")
                if r["window_full"]:
                    notes.append(f"{r['window_full']} filled the window")
                if r["unclean"]:
                    notes.append(f"+{r['unclean']} unclean: {r['ended']}")
                print(f"  {r['load']:>5} {r['pad']:>6} {r['compact']:>11} {peak:>9} {r['seeds']:>5} {r['entropy']:>8.3f} {r['worst']:>6.2f} {r['dropped']:>8} {r['botched']:>8} {r['seconds']:>6}s"
                      + (f"  {'; '.join(notes)}" if notes else ""))
            else:
                print(f"  {r['load']:>5} {r['pad']:>6} {r['compact']:>11} {peak:>9}     0   only unclean runs: {r['ended']}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1)


def compare(args):
    """Each load and seed both ways, paired, so the gap is not a difference between projects."""
    base, other = gather(args.mix, args.style), gather(args.mix, args.style, "-" + args.variant)
    out = {}
    for model, runs in other.items():
        one = {(r["load"], r["seed"]): r for r in base.get(model, []) if clean(r)}
        rows = []
        for load in sorted({r["load"] for r in runs}):
            pairs = [(one[(r["load"], r["seed"])], r) for r in runs if r["load"] == load and clean(r) and (r["load"], r["seed"]) in one]
            if not pairs:
                continue
            n = len(pairs)
            mean = lambda xs: sum(xs) / n
            rows.append({"load": load, "seeds": n, "one_message": round(mean([a["entropy"] for a, _ in pairs]), 4),
                         args.variant: round(mean([b["entropy"] for _, b in pairs]), 4),
                         "gap": round(mean([a["entropy"] - b["entropy"] for a, b in pairs]), 4),
                         "tracking_loss": round(mean([missed(a, "plain") - missed(b, "plain") for a, b in pairs]), 4),
                         "linked_jobs": round(mean([missed(a, "linked") - missed(b, "linked") for a, b in pairs]), 4),
                         "plain_jobs": round(mean([plain_jobs(a) for a, _ in pairs]), 1),
                         "delivery": pairs[0][1].get("delivery"),
                         "seconds": [round(mean([a["seconds"] for a, _ in pairs])), round(mean([b["seconds"] for _, b in pairs]))],
                         "input_tokens": [round(mean([a["input_tokens"] for a, _ in pairs])), round(mean([b["input_tokens"] for _, b in pairs]))]})
        limit = 0
        for row in rows:
            # At small loads one missed job is several points; a load only fails on more than one job per seed.
            if row["tracking_loss"] > GAP and row["tracking_loss"] * row["plain_jobs"] > 1:
                break
            limit = row["load"]
        out[model] = {"rows": rows, "no_queue_limit" if args.variant == "queued" else "steer_limit": limit}
        label = "one message minus queued" if args.variant == "queued" else "corrections in the message minus corrections mid-run"
        print(f"\n{model}  ({args.mix}, {args.style})  gap = {label}")
        print(f"  {'load':>5} {'seeds':>5}  {'entropy: one msg':>16} {args.variant:>8} {'gap':>7}  {'tracking loss':>13} {'linked jobs':>11}   time (one msg / {args.variant})")
        for r in rows:
            print(f"  {r['load']:>5} {r['seeds']:>5}  {r['one_message']:>16.3f} {r[args.variant]:>8.3f} {r['gap']:>+7.3f}  {r['tracking_loss']:>+13.3f} {r['linked_jobs']:>+11.3f}"
                  f"   {r['seconds'][0]:>6}s / {r['seconds'][1]:>6}s  ({r['delivery']})")
        if args.variant == "queued":
            print(f"  no-queue limit (tracking loss at most {GAP:.0%}, or one job per seed, up to this load): {limit}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
