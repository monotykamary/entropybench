"""A generated project and a backlog of small jobs to do in it.

Everything comes from a seed, so a load of N jobs is the same for every model.
The project grows with the load, so there is no ceiling on N.

Two mixes:

  plain    every job stands alone and touches its own file. This measures
           sheer count: how many separate things can be held at once.
  tangled  the same jobs, said the way a person unloads a backlog. Some are
           corrected later in the message ("make that 7000, not 4500"), some
           are called off ("forget the delete, keep it"), some are mentioned
           twice, some depend on a condition in the project, some depend on
           another job's value, some span several files, a few things are
           explicitly not to be touched, and at the end the model is asked
           to report what it did. This measures keeping the backlog
           straight, which is where it comes apart.

Each job describes itself, knows the right result (`solve`), and checks a
result (`check`). Outcomes:

  done      the check passes
  dropped   the files the job is about are exactly as they were
  botched   they changed, but not into what was asked (this includes doing
            the first version of a job that was later corrected, and doing a
            job that was called off)
  spill     (per run) a file no job is about was changed, created or deleted
"""
import json
import random
import re

SERVICES = ["billing", "search", "ingest", "mailer", "gateway", "ledger", "catalog", "pricing", "reports", "uploads", "notify",
            "scheduler", "session", "profile", "exports", "imports", "metrics", "audit", "webhooks", "thumbnails", "geocode",
            "translate", "invoices", "refunds", "inventory", "shipping", "reviews", "tagging", "archive", "backup", "cache",
            "queue", "router", "tokens", "quotas", "feeds", "digest", "preview", "snapshots", "health"]
TOPICS = ["onboarding", "release-checklist", "incident-review", "capacity-plan", "style-guide", "api-versioning", "data-retention",
          "oncall-handbook", "testing-strategy", "dependency-policy", "logging-conventions", "feature-flags", "rollback-steps",
          "access-requests", "cost-review", "roadmap", "migration-plan", "naming-rules", "review-etiquette", "load-testing",
          "cache-strategy", "queue-tuning", "search-relevance", "billing-edge-cases", "export-formats", "timezone-handling",
          "error-budget", "alert-thresholds", "sandbox-setup", "handover"]
MODULES = ["parser", "formatter", "validator", "loader", "writer", "sampler", "merger", "splitter", "indexer", "ranker",
           "tokenizer", "resolver", "planner", "batcher", "encoder", "decoder", "limiter", "retrier", "tracker", "pruner",
           "mapper", "reducer", "scanner", "packer", "differ", "hasher", "dispatcher", "throttle", "pager", "cursor"]
TABLES = ["orders", "customers", "sessions", "tickets", "devices", "plans", "coupons", "regions", "vendors", "returns",
          "shipments", "payouts", "leads", "events", "surveys", "badges", "courses", "rooms", "assets", "licenses"]
VERBS = ["load", "parse", "format", "merge", "split", "rank", "scan", "pack", "hash", "trim", "fold", "sort"]
NOUNS = ["rows", "items", "records", "chunks", "entries", "tokens", "fields", "pages", "batches", "keys"]
REGIONS = ["us-east", "us-west", "eu-west", "ap-south"]
STATUSES = ["open", "pending", "closed", "held"]
# Lead-ins that read correctly before any kind of sentence.
CONNECT = ["Also,", "While you're in there,", "One more thing:", "Oh, and", "Separately,", "And", "Before I forget:", "Next,",
           "Then", "Another one:", "Small thing:", "On top of that,"]
BACK = ["Actually, about", "Coming back to", "Correction on", "I changed my mind on", "One fix to what I said about"]
UNDO = ["Actually,", "On second thought,", "Wait,", "Change of plan:"]


class Job:
    """One thing to do. `group` says which kind of tracking it tests."""

    def __init__(self, kind, ref, wordings, targets, check, solve, group="plain"):
        self.kind, self.ref, self.wordings, self.targets = kind, ref, wordings, targets
        self.check, self.solve, self.group = check, solve, group
        self.follow = None        # said later in the message: a correction or a calling-off
        self.again = None         # said a second time in other words
        self.called_off = False   # the right result is to leave everything as it was
        self.first = None         # solve() for the version before the correction
        self.last = False         # belongs at the very end of the message

    def outcome(self, before, after):
        untouched = all(before.get(p) == after.get(p) for p in self.targets)
        if self.called_off:
            return "done" if untouched else "botched"
        if untouched:
            return "dropped"
        try:
            return "done" if self.check(after, before) else "botched"
        except Exception:
            return "botched"


def named(base, count):
    """`count` distinct names: the base list, then the base list with -2, -3, ..."""
    return [b if k == 0 else f"{b}-{k + 1}" for k in range(-(-count // len(base))) for b in base][:count]


def dump(obj):
    return json.dumps(obj, indent=2) + "\n"


PAD_ID = 5000  # rows added by padding start here, so they are never mistaken for the project's own rows
FILLER = ["the", "team", "agreed", "to", "revisit", "this", "after", "the", "next", "release", "because", "load", "was", "uneven",
          "during", "peak", "hours", "and", "some", "requests", "waited", "longer", "than", "expected", "while", "a", "fix",
          "for", "the", "slow", "path", "was", "reviewed", "in", "two", "rounds", "with", "notes", "kept", "here", "for", "later"]


def _sentence(rng, n=14):
    words = [rng.choice(FILLER) for _ in range(n)]
    return " ".join(words).capitalize() + "."


def pad_files(files, seed, pad):
    """Grow every config, note, source file and table to about `pad` characters with content no job is about.

    Long-context mode: the jobs, their wording and their checks are the same as without padding,
    but every file the model reads now costs far more context. Nothing added here can satisfy or
    break a check: no bullet lines, no MAX_BATCH line, no function names a job uses, and table rows
    with ids from PAD_ID up (a job only ever picks one of the project's own rows).
    """
    rng = random.Random(seed * 1000003 + pad)
    for path in sorted(files):
        text = files[path]
        if path.startswith("config/"):
            cfg = json.loads(text)
            history = []
            while len(dump({**cfg, "history": history})) < pad:
                history.append(f"20{rng.randrange(19, 26)}-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}: {_sentence(rng)}")
            files[path] = dump({**cfg, "history": history})
        elif path.startswith("notes/"):
            title, rest = text.split("\n", 1)
            paragraphs = []
            while len(title) + sum(len(x) + 2 for x in paragraphs) + len(rest) < pad:
                paragraphs.append(" ".join(_sentence(rng) for _ in range(5)))
            files[path] = title + "\n\n" + "\n\n".join(paragraphs) + "\n" + rest
        elif path.startswith("src/"):
            extra, i = [], 0
            while len(text) + sum(len(x) for x in extra) < pad:
                extra.append(f"\n\n# {_sentence(rng)}\ndef audit_step_{i}(entries):\n    seen = set()\n"
                             f"    for entry in entries:\n        seen.add(entry)\n    return len(seen) + {i}\n")
                i += 1
            files[path] = text + "".join(extra)
        elif path.startswith("data/"):
            rows, i = [], 0
            while len(text) + sum(len(x) + 1 for x in rows) < pad:
                rows.append(f"{PAD_ID + i},{rng.choice(SERVICES)},{rng.choice(STATUSES)}")
                i += 1
            files[path] = text + "".join(r + "\n" for r in rows)
    return files


def build(seed, size, mix="tangled", pad=0):
    """(files, jobs, asides): the project as {path: text}, `size` jobs, and things said that are not jobs.

    pad > 0 is long-context mode: the same project and jobs, every file grown to about `pad` characters."""
    rng = random.Random(seed)
    files = {"README.md": "# Workshop\n\nA small project used to practise housekeeping.\n",
             "CHANGELOG.md": "# Changelog\n\n## 0.4.0\n- Initial layout.\n"}
    n_cfg, n_note, n_src, n_data, n_stale = (max(12, int(size * f) + 4) for f in (0.55, 0.30, 0.25, 0.25, 0.08))
    for s in named(SERVICES, n_cfg):
        files[f"config/{s}.json"] = dump({"name": s, "port": rng.randrange(3000, 9000), "timeout_ms": rng.choice([1000, 1500, 2000, 3000]),
                                          "retries": rng.randrange(1, 5), "enabled": rng.random() < 0.6, "region": rng.choice(REGIONS)})
    for t in named(TOPICS, n_note):
        bullets = "\n".join(f"- {rng.choice(VERBS).title()} the {rng.choice(NOUNS)} before the {rng.choice(NOUNS)}." for _ in range(4))
        files[f"notes/{t}.md"] = f"# {t.replace('-', ' ').title()}\n\n{bullets}\n"
    for m in named(MODULES, n_src):
        a, b = rng.sample(VERBS, 2)
        n = rng.choice(NOUNS)
        files[f"src/{m}.py"] = (f'"""Helpers for the {m}."""\n\nMAX_BATCH = {rng.choice([50, 100, 200])}\nDEFAULT_SEP = ","\n\n\n'
                                f"def {a}_{n}(values):\n    return [v for v in values if v]\n\n\n"
                                f"def {b}_{n}(values):\n    kept = {a}_{n}(values)\n    return kept[:MAX_BATCH]\n")
    for i, t in enumerate(named(TABLES, n_data) + [f"stale_{t}" for t in named(TABLES, n_stale)]):
        rows = "\n".join(f"{r},{rng.choice(SERVICES)},{rng.choice(STATUSES)}" for r in range(1, rng.randrange(6, 12)))
        files[f"data/{t}.csv"] = f"id,owner,status\n{rows}\n"

    if pad:
        pad_files(files, seed, pad)

    pools = {"config": sorted(p for p in files if p.startswith("config/")), "note": sorted(p for p in files if p.startswith("notes/")),
             "src": sorted(p for p in files if p.startswith("src/")),
             "data": sorted(p for p in files if p.startswith("data/") and "/stale_" not in p),
             "stale": sorted(p for p in files if p.startswith("data/stale_"))}
    for pool in pools.values():
        rng.shuffle(pool)

    plain = [set_timeout, toggle_service, add_owner, rename_function, change_constant, add_bullet, retitle_note,
             create_doc, delete_table, append_row, fix_status, count_rows, set_retries, add_docstring]
    jobs, asides, serial = [], [], 0

    def add(maker, **kw):
        nonlocal serial
        made = maker(rng, files, pools, serial, **kw)
        for job in made if isinstance(made, list) else [made] if made else []:
            jobs.append(job)
            serial += 1
        return bool(made)

    if mix == "tangled":
        # Shares of the load, fixed so the same load always has the same shape.
        share = lambda f: max(1, round(size * f))
        for _ in range(share(0.10)):
            add(rng.choice([set_timeout, change_constant, retitle_note, fix_status]), amend=True)
        for _ in range(share(0.08)):
            add(conditional_retries)
        for _ in range(share(0.04)):
            add(port_pair)
        for _ in range(share(0.03)):
            add(rename_service)
        called_off, twice = share(0.05), share(0.05)
        tail = [enabled_count, deleted_list] if size >= 10 else [enabled_count]
        turn = 0
        while len(jobs) < size - len(tail):
            if not add(plain[turn % len(plain)]):
                if turn > size * 40:
                    raise ValueError(f"the project is too small for {size} jobs")
            turn += 1
        del jobs[size - len(tail):]
        ordinary = [j for j in jobs if j.group == "plain" and j.kind != "count and record"]
        rng.shuffle(ordinary)
        for job in ordinary[:called_off]:
            job.called_off, job.group = True, "called off"
            job.follow = rng.choice([f"forget what I said about {job.ref}, leave it exactly as it is",
                                     f"scratch {job.ref}, I don't want that done after all"])
        for job in ordinary[called_off:called_off + twice]:
            job.group, job.again = "said twice", f"and don't lose {job.ref}: " + job.wordings[-1]
        for maker in tail:
            add(maker)
        for _ in range(share(0.05)):
            path = pools["config"].pop() if pools["config"] else None
            if path:
                name = json.loads(files[path])["name"]
                asides.append(rng.choice([f"I already sorted out the {name} service myself yesterday, so don't touch {path}",
                                          f"leave {path} alone, someone else is in the middle of changing it"]))
    else:
        turn = 0
        while len(jobs) < size:
            if not add(plain[turn % len(plain)]) and turn > size * 40:
                raise ValueError(f"the project is too small for {size} jobs")
            turn += 1
    return files, jobs[:size], asides


def take(pools, name):
    return pools[name].pop() if pools[name] else None


def set_key(path, key, value):
    def solve(state):
        state[path] = dump({**json.loads(state[path]), key: value})
    return solve


def others_same(path, key):
    return lambda a, b: {k: v for k, v in json.loads(a[path]).items() if k != key} == {k: v for k, v in json.loads(b[path]).items() if k != key}


def set_timeout(rng, files, pools, k, amend=False):
    path = take(pools, "config")
    if not path:
        return None
    name = json.loads(files[path])["name"]
    first, value = rng.sample([2500, 4500, 6000, 7500, 9000], 2)
    same = others_same(path, "timeout_ms")
    job = Job("set a config value", f"the {name} timeout", [
        f"set the timeout for the {name} service to {first if amend else value} ms (it's in {path})",
        f"{path} needs timeout_ms changed to {first if amend else value}",
    ], [path], lambda a, b: json.loads(a[path])["timeout_ms"] == value and same(a, b), set_key(path, "timeout_ms", value))
    if amend:
        job.group, job.first = "corrected later", set_key(path, "timeout_ms", first)
        job.follow = f"the {name} timeout: make it {value} ms, not {first}"
    return job


def toggle_service(rng, files, pools, k):
    path = take(pools, "config")
    if not path:
        return None
    cfg = json.loads(files[path])
    want, word = not cfg["enabled"], "disable" if cfg["enabled"] else "enable"
    same = others_same(path, "enabled")
    return Job("toggle a flag", f"{word[:-1]}ing the {cfg['name']} service", [
        f"{word} the {cfg['name']} service in its config",
        f"the {cfg['name']} service should be {word}d, flip `enabled` in {path}",
    ], [path], lambda a, b: json.loads(a[path])["enabled"] is want and same(a, b), set_key(path, "enabled", want))


def set_retries(rng, files, pools, k):
    path = take(pools, "config")
    if not path:
        return None
    cfg = json.loads(files[path])
    value = cfg["retries"] + rng.choice([2, 3, 4])
    same = others_same(path, "retries")
    return Job("set a config value", f"the {cfg['name']} retries", [
        f"give {cfg['name']} {value} retries instead of {cfg['retries']}",
        f"in {path} change retries to {value}",
    ], [path], lambda a, b: json.loads(a[path])["retries"] == value and same(a, b), set_key(path, "retries", value))


def add_owner(rng, files, pools, k):
    path = take(pools, "config")
    if not path:
        return None
    cfg = json.loads(files[path])
    team = rng.choice(["payments", "platform", "growth", "data", "support"])
    same = others_same(path, "owner_team")
    return Job("add a config key", f"the owner_team for {cfg['name']}", [
        f"add an `owner_team` key with the value \"{team}\" to {path}",
        f"{cfg['name']}'s config should record its owner_team as {team}",
    ], [path], lambda a, b: json.loads(a[path]).get("owner_team") == team and same(a, b), set_key(path, "owner_team", team))


def rename_function(rng, files, pools, k):
    path = take(pools, "src")
    if not path:
        return None
    old = re.findall(r"def (\w+)\(", files[path])[0]
    new = f"{rng.choice(['read', 'clean', 'keep', 'pick'])}_{old.split('_', 1)[1]}_{k}"

    def solve(state):
        state[path] = re.sub(rf"\b{old}\b", new, state[path])
    return Job("rename a function", f"renaming `{old}`", [
        f"rename the function `{old}` in {path} to `{new}`, including where it's called in that file",
        f"in {path}, `{old}` should be called `{new}` from now on (update its caller too)",
    ], [path], lambda a, b: f"def {new}(" in a[path] and not re.search(rf"\b{old}\b", a[path]) and a[path].count(new) >= 2, solve)


def change_constant(rng, files, pools, k, amend=False):
    path = take(pools, "src")
    if not path:
        return None
    first, value = rng.sample([25, 75, 150, 250, 400], 2)

    def setter(v):
        def solve(state):
            state[path] = re.sub(r"^MAX_BATCH = \d+", f"MAX_BATCH = {v}", state[path], flags=re.M)
        return solve
    job = Job("change a constant", f"the batch limit in {path}", [
        f"set MAX_BATCH to {first if amend else value} in {path}",
        f"the batch limit in {path} should be {first if amend else value}",
    ], [path], lambda a, b: re.search(rf"^MAX_BATCH = {value}\s*$", a[path], re.M) is not None and 'DEFAULT_SEP = ","' in a[path], setter(value))
    if amend:
        job.group, job.first = "corrected later", setter(first)
        job.follow = f"the batch limit in {path}: it should be {value}, I said {first} by mistake"
    return job


def add_docstring(rng, files, pools, k):
    path = take(pools, "src")
    if not path:
        return None
    second = re.findall(r"def (\w+)\(", files[path])[1]
    text = f"Keep at most MAX_BATCH values (rule {100 + k})."

    def solve(state):
        state[path] = state[path].replace(f"def {second}(values):\n", f'def {second}(values):\n    """{text}"""\n')
    return Job("add a docstring", f"the docstring for `{second}`", [
        f"give `{second}` in {path} a docstring that says exactly: {text}",
        f"add this docstring to the {second} function in {path}: \"{text}\"",
    # The sentence's full stop may or may not be read as part of the text: both count.
    ], [path], lambda a, b: text.rstrip(".") in a[path].split(f"def {second}(", 1)[1] and "return kept[:MAX_BATCH]" in a[path], solve)


def add_bullet(rng, files, pools, k):
    path = take(pools, "note")
    if not path:
        return None
    line = f"Review the {rng.choice(NOUNS)} every {rng.choice(['Monday', 'sprint', 'quarter', 'release'])} (item {200 + k})."
    count = files[path].count("\n- ")

    def solve(state):
        state[path] = state[path].rstrip("\n") + f"\n- {line}\n"
    return Job("add a bullet", f"the extra bullet in {path}", [
        f"add a bullet to {path} that reads: {line}",
        f"{path} is missing a point, please add \"{line}\" to its list",
    ], [path], lambda a, b: f"- {line.rstrip('.')}" in a[path] and a[path].count("\n- ") == count + 1, solve)


def retitle_note(rng, files, pools, k, amend=False):
    path = take(pools, "note")
    if not path:
        return None
    topic = path.split("/")[1][:-3].replace("-", " ")
    first, title = (f"{lead} {topic} ({300 + k})" for lead in rng.sample(["Guide to", "Notes on", "Working with", "Rules for"], 2))
    body = files[path].split("\n", 1)[1]

    def setter(t):
        def solve(state):
            state[path] = f"# {t}\n" + state[path].split("\n", 1)[1]
        return solve
    job = Job("change a title", f"the heading of {path}", [
        f"change the heading of {path} to \"{first if amend else title}\"",
        f"retitle {path}: the first line should be \"# {first if amend else title}\"",
    ], [path], lambda a, b: a[path].split("\n", 1)[0].strip() == f"# {title}" and a[path].split("\n", 1)[1].strip() == body.strip(), setter(title))
    if amend:
        job.group, job.first = "corrected later", setter(first)
        job.follow = f"the heading of {path}: use \"{title}\" instead"
    return job


def create_doc(rng, files, pools, k):
    name = f"docs/{rng.choice(['howto', 'faq', 'runbook', 'summary'])}-{400 + k}.md"
    title, phrase = f"{rng.choice(VERBS).title()} {rng.choice(NOUNS)} {400 + k}", f"owned by the {rng.choice(['platform', 'data', 'growth'])} group"

    def solve(state):
        state[name] = f"# {title}\n\nThis page is {phrase}.\n"
    return Job("create a file", f"the new file {name}", [
        f"create {name} with the heading \"# {title}\" and one sentence saying it is {phrase}",
        f"I need a new file {name}: title line \"# {title}\", then a line mentioning that it's {phrase}",
    ], [name], lambda a, b: a.get(name, "").lstrip().startswith(f"# {title}") and phrase in a[name].lower(), solve)


def delete_table(rng, files, pools, k):
    path = take(pools, "stale")
    if not path:
        return None
    return Job("delete a file", f"deleting {path}", [f"delete {path}, nobody uses it", f"{path} can go, remove it"],
               [path], lambda a, b: path not in a, lambda state: state.pop(path, None))


def append_row(rng, files, pools, k):
    path = take(pools, "data")
    if not path:
        return None
    row = f"{900 + k},{rng.choice(SERVICES)},{rng.choice(STATUSES)}"
    rows = files[path].strip().split("\n")

    def solve(state):
        state[path] = state[path].rstrip("\n") + f"\n{row}\n"
    return Job("append a row", f"the new row in {path}", [
        f"append the row {row} to {path}",
        f"add one more line at the end of {path}: {row}",
    ], [path], lambda a, b: a[path].strip().split("\n") == rows + [row], solve)


def fix_status(rng, files, pools, k, amend=False):
    path = take(pools, "data")
    if not path:
        return None
    rows = files[path].strip().split("\n")
    # Pick one of the project's own rows, never a padding row, so padding does not change which job this is.
    at = rng.randrange(1, 1 + sum(int(r.split(",")[0]) < PAD_ID for r in rows[1:]))
    rid, owner, status = rows[at].split(",")
    first, new = rng.sample([s for s in STATUSES if s != status], 2)

    def setter(s):
        def solve(state):
            state[path] = "\n".join(rows[:at] + [f"{rid},{owner},{s}"] + rows[at + 1:]) + "\n"
        return solve
    job = Job("edit one cell", f"id {rid} in {path}", [
        f"in {path}, the row with id {rid} should have status {first if amend else new}",
        f"mark id {rid} as {first if amend else new} in {path}",
    ], [path], lambda a, b: a[path].strip().split("\n") == rows[:at] + [f"{rid},{owner},{new}"] + rows[at + 1:], setter(new))
    if amend:
        job.group, job.first = "corrected later", setter(first)
        job.follow = f"id {rid} in {path}: that should be {new}, not {first}"
    return job


def count_rows(rng, files, pools, k):
    path = take(pools, "data")
    if not path:
        return None
    status = rng.choice(STATUSES)
    count = sum(line.endswith("," + status) for line in files[path].strip().split("\n")[1:])
    out = f"answers/{path.split('/')[1][:-4]}-{status}.txt"

    def solve(state):
        state[out] = f"{count}\n"
    return Job("count and record", f"the {status} count for {path}", [
        f"count how many rows in {path} have status {status} and write just that number to {out}",
        f"I need the number of {status} rows in {path} saved to {out} (only the number)",
    ], [out], lambda a, b: a.get(out, "").strip() == str(count) and a.get(path) == files[path], solve)


def conditional_retries(rng, files, pools, k):
    """What to do depends on something only the project can answer."""
    path, note = take(pools, "config"), take(pools, "note")
    if not path or not note:
        return None
    cfg = json.loads(files[path])
    value, line = cfg["retries"] + 5, f"The {cfg['name']} service is switched off (check {500 + k})."
    same = others_same(path, "retries")
    if cfg["enabled"]:
        check = lambda a, b: json.loads(a[path])["retries"] == value and same(a, b) and a[note] == b[note]
        solve = set_key(path, "retries", value)
    else:
        check = lambda a, b: a[path] == b[path] and f"- {line.rstrip('.')}" in a[note]

        def solve(state):
            state[note] = state[note].rstrip("\n") + f"\n- {line}\n"
    return Job("depends on a condition", f"the {cfg['name']} retries rule", [
        f"if the {cfg['name']} service is enabled, set its retries to {value}; if it isn't, leave {path} alone and add a bullet to {note} reading: {line}",
    ], [path, note], check, solve, group="depends on the project")


def port_pair(rng, files, pools, k):
    """The second job's value is only given in terms of the first's."""
    one, two = take(pools, "config"), take(pools, "config")
    if not one or not two:
        return None
    a_name, b_name = json.loads(files[one])["name"], json.loads(files[two])["name"]
    port = 10000 + 10 * k + rng.randrange(1, 9)
    same_one, same_two = others_same(one, "port"), others_same(two, "port")
    return [
        Job("set a config value", f"the {a_name} port", [f"move the {a_name} service to port {port}", f"{a_name} should listen on {port} from now on"],
            [one], lambda a, b: json.loads(a[one])["port"] == port and same_one(a, b), set_key(one, "port", port), group="plain (another job hangs on it)"),
        Job("depends on another job", f"the {b_name} port", [
            f"put the {b_name} service on the port right after the one {a_name} is moving to",
            f"{b_name} should take the next port up from {a_name}'s new one",
        ], [two], lambda a, b: json.loads(a[two])["port"] == port + 1 and same_two(a, b), set_key(two, "port", port + 1), group="depends on another job"),
    ]


def rename_service(rng, files, pools, k):
    """One job, three places."""
    path, note = take(pools, "config"), take(pools, "note")
    if not path or not note:
        return None
    cfg = json.loads(files[path])
    old, new = cfg["name"], f"{cfg['name']}-next{k}"
    files[note] = files[note].rstrip("\n") + f"\n- The {old} service owner signs off on changes here.\n"
    moved = f"config/{new}.json"

    def solve(state):
        state[moved] = dump({**json.loads(state.pop(path)), "name": new})
        state[note] = state[note].replace(f"The {old} service owner", f"The {new} service owner")
    return Job("rename across files", f"renaming the {old} service", [
        f"rename the {old} service to {new}: the config file itself, the name inside it, and the mention in {note}",
    ], [path, moved, note], lambda a, b: path not in a and json.loads(a[moved]) == {**json.loads(b[path]), "name": new}
        and f"The {new} service owner" in a[note] and f"The {old} service owner" not in a[note], solve, group="spans several files")


def enabled_count(rng, files, pools, k):
    """Reporting back. Graded against what the model actually left behind, so a miss elsewhere is not counted twice."""
    out = "answers/enabled-services.txt"
    count = lambda state: sum(json.loads(text).get("enabled") is True for p, text in state.items() if p.startswith("config/") and p.endswith(".json"))

    def solve(state):
        state[out] = f"{count(state)}\n"
    job = Job("report at the end", "the enabled-services count", [
        f"when all of that is done, count how many services are enabled across config/ as things stand then, and write just the number to {out}",
    ], [out], lambda a, b: a.get(out, "").strip() == str(count(a)), solve, group="report at the end")
    job.last = True
    return job


def deleted_list(rng, files, pools, k):
    out = "answers/deleted-files.txt"
    gone = lambda before, state: sorted(p for p in before if p not in state)

    def solve(state):
        state[out] = "".join(p + "\n" for p in gone(files, state))
    job = Job("report at the end", "the list of deleted files", [
        f"at the very end, list every file you deleted or renamed away during all this in {out}, one path per line",
    ], [out], lambda a, b: sorted(line.strip() for line in a.get(out, "").splitlines() if line.strip()) == gone(b, a), solve, group="report at the end")
    job.last = True
    return job


def said(jobs, asides, seed, tagged=False):
    """Everything that gets said, in order: each job, what is said about it later, and the asides.

    With tagged=True each line comes with the job it corrects or calls off, or None,
    so a run can hold corrections back and say them mid-run instead (--steer).
    The wording and order are the same either way.
    """
    rng = random.Random(seed * 7919 + len(jobs))
    order = [j for j in jobs if not j.last]
    rng.shuffle(order)
    lines = [(rng.choice(job.wordings), None) for job in order]
    later = [(i, rng.choice(UNDO if job.called_off else BACK) + " " + job.follow, job) for i, job in enumerate(order) if job.follow]
    later += [(i, job.again, None) for i, job in enumerate(order) if job.again]
    # A correction lands a few items after the thing it corrects, never before it.
    for at, text, job in sorted(later, key=lambda x: (x[0], x[1]), reverse=True):
        lines.insert(min(len(lines), at + rng.randrange(4, 12)), (text, job))
    for text in asides:
        lines.insert(rng.randrange(0, len(lines) + 1), (text, None))
    lines += [(job.wordings[0], None) for job in jobs if job.last]
    return lines if tagged else [text for text, _ in lines]


def brief(lines, seed, style="ramble", opening=True):
    """One message out of a run of things to say."""
    rng = random.Random(seed * 104729 + len(lines))
    tidy = lambda text: text[0].upper() + text[1:].rstrip(".") + "."
    if style == "list":
        return "Please do all of these:\n\n" + "\n".join(f"{i}. {tidy(line)}" for i, line in enumerate(lines, 1))
    # Run them together the way a person unloading a backlog would: no numbers, a few to a paragraph.
    paragraphs, at = [], 0
    while at < len(lines):
        size = rng.randrange(2, 6)
        parts = []
        for i, line in enumerate(lines[at:at + size]):
            starts_itself = line.startswith(tuple(BACK + UNDO)) or line.startswith(("and ", "when ", "at the very end"))
            lead = "" if (at == 0 and i == 0) or starts_itself else rng.choice(CONNECT) + " "
            parts.append(tidy(lead + line))
        paragraphs.append(" ".join(parts))
        at += size
    head = "I've got a backlog of small things for this project. " if opening and len(lines) > 1 else ""
    return head + "\n\n".join(paragraphs)


def score(files, jobs, after):
    """Outcome per job, plus the files touched that no job is about."""
    outcomes = [job.outcome(files, after) for job in jobs]
    about = {p for job in jobs for p in job.targets}
    spill = sorted(p for p in set(files) | set(after) if p not in about and files.get(p) != after.get(p))
    return outcomes, spill


def ideal(files, jobs, heed=True):
    """The project after every job is done right. With heed=False: after doing each job as first
    stated, corrections ignored and called-off jobs done anyway (what losing track looks like)."""
    state = dict(files)
    for job in sorted(jobs, key=lambda j: j.last):
        if job.called_off and heed:
            continue
        (job.solve if heed or not job.first else job.first)(state)
    return state
