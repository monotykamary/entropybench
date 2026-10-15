"""The smallest agent that can do the jobs: a model, five file tools, a loop.

It talks to any OpenAI-compatible Chat Completions endpoint. The tools only
see one directory; there is no shell. The loop gives the model no help with
keeping track: no plan tool, no reminders, no "are you finished?" nudge. It
ends when the model answers without calling a tool, which is the model saying
it is done.
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

SYSTEM = ("You are working in a project directory through the tools provided. "
          "Make the changes the user asks for. When you have finished, reply with a short message and no tool call.")

TOOLS = [
    ("list_dir", "List the files and folders in a directory of the project.", {"path": "Directory path relative to the project root; \"\" for the root."}),
    ("read_file", "Read a file of the project.", {"path": "File path relative to the project root."}),
    ("write_file", "Create a file, or replace its whole content.", {"path": "File path relative to the project root.", "content": "The full new content."}),
    ("edit_file", "Replace one exact piece of text in a file. `old` must occur exactly once.", {"path": "File path relative to the project root.", "old": "The text to replace.", "new": "The text to put in its place."}),
    ("delete_file", "Delete a file of the project.", {"path": "File path relative to the project root."}),
]
TOOL_DEFS = [{"type": "function", "function": {"name": name, "description": about, "parameters": {
    "type": "object", "properties": {k: {"type": "string", "description": v} for k, v in args.items()},
    "required": list(args), "additionalProperties": False}}} for name, about, args in TOOLS]


# Per reply. High enough that a model which reasons at length about a thousand jobs, or
# calls dozens of tools in one go, is not cut short by the harness. GLM once spent all of
# 64k on reasoning before its first tool call at 1,000 jobs.
MAX_TOKENS = 131072
# A reply cut off by the output limit before any tool call is not the model saying it is done.
# It is asked again, unchanged, this many more times, the way a failed request is retried.
CUT_OFF_RETRIES = 2


class Workspace:
    def __init__(self, root):
        self.root = os.path.realpath(root)

    def path(self, relative):
        full = os.path.realpath(os.path.join(self.root, relative.lstrip("/")))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ValueError("that path is outside the project")
        return full

    def call(self, name, args):
        try:
            path = self.path(args.get("path", ""))
            if name == "list_dir":
                return "\n".join(sorted(e + ("/" if os.path.isdir(os.path.join(path, e)) else "") for e in os.listdir(path))) or "(empty)"
            if name == "read_file":
                return open(path).read()
            if name == "write_file":
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(args["content"])
                return "written"
            if name == "edit_file":
                text = open(path).read()
                if text.count(args["old"]) != 1:
                    return f"error: `old` occurs {text.count(args['old'])} times in the file, it must occur exactly once"
                with open(path, "w") as f:
                    f.write(text.replace(args["old"], args["new"]))
                return "edited"
            if name == "delete_file":
                os.remove(path)
                return "deleted"
            return f"error: no tool named {name}"
        except (OSError, KeyError, ValueError) as e:
            return f"error: {e}"


def post(base_url, key, body, tries=4):
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"authorization": "Bearer " + key, "content-type": "application/json", "user-agent": "entropybench/0"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code}: {detail}")
            last = f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = str(e)
        time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"gave up after {tries} tries: {last}")


# How OpenAI-compatible servers (vLLM, SGLang) say a request is too long for the window.
TOO_LONG = re.compile(r"maximum context length is (\d+) tokens.*?\((\d+) in the messages", re.S)
# ...and that the messages alone no longer fit.
FULL = re.compile(r"maximum context length is \d+ tokens.*?your messages resulted in (\d+) tokens", re.S)
# Below this much room for a reply, the window counts as full.
MIN_REPLY = 8192


# Pi's wrapping of a compaction summary in the model's context (pi-coding-agent, core/messages).
SUMMARY_PREFIX = "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
SUMMARY_SUFFIX = "\n</summary>"
BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compact.ts")
# Fabric's summary names its sources by entry ("[entry m12]"); in a Pi Fabric session the model
# reads them back exactly with memory.expand. This is that tool, over the agent's raw log.
RECALL = ("recall", "Read the exact original text of an earlier conversation entry that a compaction summary refers to.",
          {"entry": "The entry id the summary gives, such as m0."})
RECALL_DEF = {"type": "function", "function": {"name": RECALL[0], "description": RECALL[1], "parameters": {
    "type": "object", "properties": {k: {"type": "string", "description": v} for k, v in RECALL[2].items()},
    "required": list(RECALL[2]), "additionalProperties": False}}}


class WindowFull(Exception):
    pass


class Compactor:
    """Pi Fabric's deterministic compaction (compact.ts), applied the way Pi applies it.

    Pi compacts once the context passes the window minus its reply reserve, or when a
    request is rejected as too long. Fabric then summarises the raw log, keeps a recent
    raw tail of about `keep` tokens, and never fills more than `ratio` of the window.
    The defaults are Pi's and Fabric's own. `at` compacts earlier, as Fabric's
    per-model token threshold does.
    """

    def __init__(self, window=1048576, at=None, reserve=16384, keep=20000, ratio=0.65):
        self.window, self.reserve, self.keep, self.ratio = window, reserve, keep, ratio
        self.at = at or window - reserve
        self.done = []

    def view(self, log):
        """What the model sees: the latest summary, then the raw log from the kept entry on."""
        if not self.done:
            return log
        last = self.done[-1]
        start = last["at"] + 1 if last["kept"] is None else last["kept"]
        return [{"role": "user", "content": SUMMARY_PREFIX + last["summary"] + SUMMARY_SUFFIX}] + log[start:]

    def compact(self, log, usage, tokens):
        """Compact the log at `tokens` of context. Returns None, or why Fabric declined."""
        request = {"messages": [dict(m, usage=usage[i]) if i in usage else m for i, m in enumerate(log)],
                   "compactions": self.done, "tokens": tokens, "window": self.window,
                   "reserve": self.reserve, "keep": self.keep, "ratio": self.ratio}
        done = subprocess.run(["bun", BRIDGE], input=json.dumps(request), capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError("compaction failed: " + done.stderr[-300:])
        result = json.loads(done.stdout)
        if "cancel" in result:
            return result["cancel"]
        self.done.append({"at": len(log) - 1, "summary": result["summary"], "kept": result["kept"], "tokens": tokens})
        return None


def recall(log, entry):
    try:
        m = log[int(str(entry).strip().lstrip("m"))]
    except (ValueError, IndexError):
        return f"error: no entry {entry}"
    calls = "".join(f"\n[called {c['function']['name']} {c['function'].get('arguments') or ''}]" for c in m.get("tool_calls") or [])
    return f"[{m['role']}]\n" + (m.get("content") or "") + calls


def run(base_url, key, model, root, request, max_steps, extra=None, later=(), deliver=None, tool_cap=20000, compactor=None):
    """Work on `request` in `root` until the model stops. Returns what happened.

    `later` is more requests that arrive while the model is working: (step, text)
    pairs, each delivered as a new user message after that step's tool results,
    the way a person keeps adding to the pile without waiting for a pause.

    `deliver(step, idle)` is asked after every step for messages that are due:
    with idle=False while the model is still working (a steer lands mid-run), and
    with idle=True when it has stopped (the next item of a queue). The run ends
    only when the model stops and nothing more is due.

    With a `compactor`, the context is compacted the way Pi Fabric does it instead
    of the run ending when the window fills, and the model gets `recall`.
    """
    space = Workspace(root)
    later = sorted(later)
    system = {"role": "system", "content": SYSTEM}
    # The raw log, as Pi's session file keeps it. Without compaction the model sees all of it.
    log = [{"role": "user", "content": request}]
    usage_at = {}
    tools = TOOL_DEFS + ([RECALL_DEF] if compactor else [])
    stats = {"steps": 0, "cut_off_replies": 0, "tool_calls": 0, "tool_errors": 0, "input_tokens": 0, "output_tokens": 0, "ended": "", "seconds": 0.0, "last_message": "", "deliveries": 0, "peak_context": 0}
    if compactor:
        stats.update(compactions=[], recalls=0)
    started = time.time()
    cap = MAX_TOKENS
    last = {"tokens": 0, "at": 0}

    def compact(tokens, why):
        nonlocal cap
        reason = compactor.compact(log, usage_at, tokens)
        if reason is None:
            # The window has room again: full-length replies, as before it filled.
            cap = MAX_TOKENS
            c = compactor.done[-1]
            stats["compactions"].append({"step": stats["steps"], "why": why, "tokens": tokens, "summary_bytes": len(c["summary"].encode()),
                                         "kept_from": c["kept"], "log_length": len(log)})
            last["tokens"] = 0
        else:
            stats["compactions_declined"] = stats.get("compactions_declined", 0) + 1
        return reason

    def ask():
        nonlocal cap
        if compactor and last["tokens"]:
            # Pi's check: the last reply's context plus what has been added since.
            now = last["tokens"] + sum(len(m.get("content") or "") for m in log[last["at"]:]) // 4
            if now > compactor.at:
                compact(now, "threshold")
        overflows = 0
        while True:
            view = compactor.view(log) if compactor else log
            try:
                return post(base_url, key, {"model": model, "messages": [system] + view, "tools": tools, "max_tokens": cap, **(extra or {})})
            except RuntimeError as e:
                found, full = TOO_LONG.search(str(e)), FULL.search(str(e))
                if not found and not full:
                    raise
                used = int(full.group(1)) if full else int(found.group(2))
                room = 0 if full else int(found.group(1)) - used
                if room >= MIN_REPLY and room < cap:
                    # Near the end of the window, ask for a shorter reply, as an agent harness would.
                    cap = room
                    continue
                overflows += 1
                if compactor and overflows <= 3 and compact(used, "overflow") is None:
                    continue
                stats["rejected_context"] = used
                raise WindowFull()

    try:
        while stats["steps"] < max_steps:
            for attempt in range(CUT_OFF_RETRIES + 1):
                reply = ask()
                usage = reply.get("usage") or {}
                stats["input_tokens"] += usage.get("prompt_tokens") or 0
                stats["peak_context"] = max(stats["peak_context"], (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0))
                stats["output_tokens"] += usage.get("completion_tokens") or 0
                choice = reply["choices"][0]
                message = choice["message"]
                calls = message.get("tool_calls") or []
                if calls or choice.get("finish_reason") != "length":
                    break
                stats["cut_off_replies"] += 1
            stats["steps"] += 1
            # Hand back only what the API defines, so a provider's extra fields don't break the next request.
            kept = {"role": "assistant", "content": message.get("content") or ""}
            for field in ("reasoning_content", "reasoning"):
                if message.get(field):
                    kept[field] = message[field]
            if calls:
                kept["tool_calls"] = calls
            usage_at[len(log)] = {"input": usage.get("prompt_tokens") or 0, "output": usage.get("completion_tokens") or 0}
            log.append(kept)
            last.update(tokens=(usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0), at=len(log))
            if not calls:
                if later:
                    # It finished before the rest arrived. Hand over what is still waiting, all at once.
                    log.append({"role": "user", "content": "\n\n".join(text for _, text in later)})
                    later = []
                    continue
                due = deliver(stats["steps"], True) if deliver else []
                if due:
                    stats["deliveries"] += 1
                    log.append({"role": "user", "content": "\n\n".join(due)})
                    continue
                stats["ended"] = "the model said it was done" if choice.get("finish_reason") != "length" else "cut off by the output limit"
                stats["last_message"] = (message.get("content") or "")[:400]
                break
            for call in calls:
                stats["tool_calls"] += 1
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                    args = args if isinstance(args, dict) else {}
                    if compactor and call["function"]["name"] == RECALL[0]:
                        stats["recalls"] += 1
                        result = recall(log, args.get("entry", ""))
                    else:
                        result = space.call(call["function"]["name"], args)
                except json.JSONDecodeError:
                    result = "error: the arguments are not valid JSON"
                stats["tool_errors"] += result.startswith("error:")
                log.append({"role": "tool", "tool_call_id": call["id"], "content": result[:tool_cap]})
            while later and later[0][0] <= stats["steps"]:
                log.append({"role": "user", "content": later.pop(0)[1]})
            due = deliver(stats["steps"], False) if deliver else []
            if due:
                stats["deliveries"] += 1
                log.append({"role": "user", "content": "\n\n".join(due)})
        else:
            stats["ended"] = "step limit"
    except WindowFull:
        stats["ended"] = "context window full"
    except RuntimeError as e:
        stats["ended"] = "request failed: " + str(e)[:200]
    stats["seconds"] = round(time.time() - started, 1)
    return stats
