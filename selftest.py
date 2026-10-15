"""Check the checkers, without a model.

    python3 selftest.py

Three stand-in agents work on every mix, load and a spread of seeds:

  perfect    does every job right              -> every job done, no spill
  idle       changes nothing                   -> every job dropped (called-off jobs are right to leave alone)
  heedless   does each job as first stated,    -> the plain jobs done, every corrected
             ignores corrections and              and called-off job botched
             calling-off

If these come out any other way, a check or a solution is wrong, and a
model's score on it would mean nothing.
"""
import collections
import sys

import world

LOADS = [1, 5, 10, 25, 50, 100, 250, 500]


def main():
    bad = 0
    cases = [(mix, load, seed, 0) for mix in ("plain", "tangled") for load in LOADS for seed in range(1, 6)]
    # Long-context mode: padded files must leave every check exactly as it was.
    cases += [(mix, load, seed, pad) for mix in ("plain", "tangled") for load in (10, 100) for seed in (1, 2) for pad in (8000, 32000)]
    for mix, load, seed, pad in cases:
        if True:
            if True:
                files, jobs, asides = world.build(seed, load, mix, pad=pad)
                assert len(jobs) == load, (mix, load, seed, len(jobs))
                lines = world.said(jobs, asides, seed)
                for style in ("ramble", "list"):
                    assert world.brief(lines, seed, style)
                targets = [p for job in jobs for p in job.targets]
                clash = [p for p, n in collections.Counter(targets).items() if n > 1]
                perfect, spill = world.score(files, jobs, world.ideal(files, jobs))
                idle, _ = world.score(files, jobs, dict(files))
                heedless, _ = world.score(files, jobs, world.ideal(files, jobs, heed=False))
                problems = []
                if clash:
                    problems.append(f"two jobs share a file: {clash[:3]}")
                if set(perfect) != {"done"} or spill:
                    problems.append(f"perfect agent: {collections.Counter(perfect)} spill {spill[:3]} "
                                    f"{[j.kind for j, o in zip(jobs, perfect) if o != 'done'][:4]}")
                for job, o in zip(jobs, idle):
                    if o != ("done" if job.called_off else "dropped"):
                        problems.append(f"idle agent got {o} on {job.kind}")
                for job, o in zip(jobs, heedless):
                    want = "botched" if job.called_off or job.first else "done"
                    if o != want and job.group != "report at the end":
                        problems.append(f"heedless agent got {o}, expected {want}, on {job.kind} ({job.group})")
                if problems:
                    bad += 1
                    print(f"{mix} load {load} seed {seed} pad {pad}:", *problems[:4], sep="\n   ")
    groups = collections.Counter(j.group for j in world.build(1, 100, "tangled")[1])
    print("a tangled load of 100 is made of:", dict(groups))
    print("self-test:", "FAILED" if bad else "passed", f"({len(cases)} projects)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
