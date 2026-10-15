# entropybench

How many things can a model be handed at once before some go missing?

The habit this measures: instead of queueing follow-ups or steering an agent
one step at a time, you unload the whole backlog in one message and expect it
to keep track. entropybench hands a model N small, independent jobs in a
single message and checks every one of them afterwards.

## How it works

- A seeded generator (`world.py`) builds a small project (configs, notes,
  source files, tables) that grows with the load, and N jobs over it: set a
  value, rename a function, add a bullet, create or delete a file, fix one
  cell, count something.
- The backlog is written out as one message, run together the way a person
  talks through it (`--style ramble`) or as a numbered list (`--style list`).
- A minimal agent (`agent.py`) gives the model five file tools and nothing
  else: no plan tool, no reminders, no "are you done?" It stops when the
  model answers without calling a tool.
- Every job is then checked by code. No model judges anything.

Two mixes:

- `--mix plain`: every job stands alone and touches its own file. Measures
  sheer count.
- `--mix tangled` (default): the same jobs said the way a backlog really
  comes out. Of 100 jobs, about 10 are corrected later in the message, 5 are
  called off, 5 are said twice, 8 depend on a condition in the project, 4
  depend on another job's value, 3 span several files, and 2 ask for a
  report at the end. A few asides say what not to touch.

With `--drip K`, half of what is said is in the first message and the rest
arrives in K more messages while the model is already working.

## Do you still need a queue?

The claim behind the habit is that newer models no longer need queueing or
steering. Two delivery modes test it against the same backlog, word for word,
so each run can be paired with the one-message run of the same load and seed:

- `--queue K` is the old way. The backlog is said K items at a time, and the
  next K arrive only once the model says it is done. Corrections and call-offs
  then arrive later as their own items. `--queue auto` uses 1 at a time up to
  100 jobs and 10 at a time above that.
- `--steer` holds every correction and call-off back. Each one is said mid-run,
  as soon as the model has touched the job it is about, so the model has to go
  back and change what it did. A correction for a job never touched is said
  when the model stops.

`python3 report.py --variant queued` puts both side by side:

| | |
|---|---|
| queue gap | one-message entropy minus queued entropy, paired by load and seed. Near 0 means queueing would not have helped |
| tracking loss | the same gap counted on the stand-alone jobs only: what the model loses by keeping track itself. Linked jobs (corrections, call-offs, jobs leaning on another) are shown apart, since a queue hands them over before what changes them |
| no-queue limit | the largest load whose tracking loss is at most 2 points (or one job per seed), every smaller load included |

A trend across model generations shows as a rising no-queue limit.
`--variant steer` gives the same comparison for corrections said in the
message versus corrections said mid-run.

## What is reported

| | |
|---|---|
| done | the job's own check passes |
| dropped | the files it concerns were never changed |
| botched | it was attempted and came out wrong; this includes doing the first version of a job corrected later, and doing a job that was called off |
| spill | files no job is about that were changed, created or deleted |
| entropy | (dropped + botched + spill) / jobs at one load. 0 is perfect |
| load limit | the largest load with entropy at or under 10%, every smaller load included |
| clean | false when the run ended for a reason outside the model's control (a failed request); such runs are left out |

The run also prints the share done per kind of tracking (corrected later,
called off, depends on another job, and so on), which shows where a model
loses the thread.

## Long context

`--pad N` grows every config, note, source file and table to about N
characters with content no job is about: a change history in each config,
paragraphs in each note, extra helper functions, extra table rows. The jobs,
their wording and their checks stay exactly the same, so the only thing that
changes is how much context the model fills by reading the project. Each run
records its peak context (the largest single request).

```bash
python3 run.py ... --loads 50 --seeds 1,2,3 --pad 32000
python3 report.py --context            # entropy against peak context, by padding level
```

At 50 jobs, `--pad 8000` reaches roughly 170k tokens, `16000` 330k, `32000`
500–750k and `48000` 850–950k. The agent has no compaction. Near the end of the
window it asks for a shorter reply, as an agent harness would; once fewer than
8,192 tokens are left for a reply, the run ends as `context window full`. That
counts as a real result: jobs not done by then are dropped.

## Compaction

Without compaction, a run that fills the window ends there. `--compact` compacts
the context the way [Pi Fabric](https://github.com/fabric-runtime/pi-fabric)
does, so the run can keep going:

- **The compactor is Fabric's own** (`compileFabricSummary`, called by
  `compact.ts` through bun). It makes no model call: the same conversation always
  gives the same summary, byte for byte, so a compacted run can be repeated like
  any other. It rebuilds each summary from the raw log, never from the previous
  summary.
- **It fires when Pi's would:** once the context passes the window minus Pi's
  16,384-token reply reserve, or when a request is rejected as too long. It keeps a
  raw tail of about 20,000 tokens and fills at most 65% of the window afterwards,
  which are Pi's and Fabric's defaults. `--compact-at N` compacts earlier, like
  Fabric's per-model token threshold.
- **The model sees** Pi's wording, "The conversation history before this point
  was compacted into the following summary", then the summary, then the kept
  tail. Fabric's summary cites its sources by entry (`[entry m12]`); a `recall`
  tool returns an entry's exact text, as `memory.expand` does in a Pi Fabric
  session.

A long backlog is the hard case. The first message holds every job, and Fabric
keeps at most about 4 KB of each user message inline, so the rest of the list
survives only if the model recalls it. A step that reads many files at once can't
be split from its results, so it is summarised whole.

```bash
PI_FABRIC=../pi-fabric python3 run.py ... --loads 50 --pad 48000 --compact
python3 run.py ... --loads 50 --pad 16000 --compact --compact-at 150000
python3 report.py --context            # compacted rows sit next to the uncompacted ones
```

Each run records its compactions (step, why, context size, summary size, where the
kept tail starts) and how often the model used `recall`.

`--compact` needs [bun](https://bun.sh) and a checkout of pi-fabric (found at
`$PI_FABRIC`, else next to this repository): its npm package does not export the
compactor yet. The results so far were made with pi-fabric at commit `6dfa28c`;
every compacted run records the commit it used.

## Checking the checks

```bash
python3 selftest.py
```

Three stand-in agents run on every mix and load without a model: one does
every job right (must score all done), one changes nothing (must score all
dropped), and one does each job as first stated and ignores corrections and
call-offs (must be botched on exactly those). Run it after any change to
`world.py`.

## Run

```bash
python3 run.py --base-url https://api.provider.com/v1 --env-var PROVIDER_API_KEY --model some-model
python3 run.py ... --loads 100,250,500 --seeds 1,2,3 --style list
python3 run.py ... --mix plain
python3 run.py ... --drip 3
python3 run.py ... --queue auto        # the queued baseline
python3 run.py ... --steer             # corrections mid-run
python3 report.py --variant queued     # queue gap and no-queue limit
python3 run.py ... --pad 32000         # long context: same jobs, bigger files
python3 run.py ... --extra '{"reasoning_effort": "high"}'
```

Any OpenAI-compatible Chat Completions endpoint with tool calling works.
Python 3.11 or newer, no dependencies. The tools only see a temporary copy
of the generated project.

Every run records which code produced it (a hash of `world.py`, `agent.py`,
`run.py` and `compact.ts`) and the date.

## License

MIT.
