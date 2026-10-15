// Compact an entropybench conversation with Pi Fabric's deterministic compactor.
//
//     bun compact.ts < request.json > compaction.json
//
// The compactor is pi-fabric's `compileFabricSummary`: no model call, and the same
// conversation always gives the same summary byte for byte, so a compacted run is
// as repeatable as an uncompacted one. It is the engine Pi Fabric uses when Pi
// compacts a session; here it is called directly on the agent's history.
//
// The request is the whole raw history, never the compacted view, the way Pi keeps
// its session log: Fabric rebuilds every summary from the raw log, and earlier
// compactions are markers in it.
//
//   { "messages": [...],           OpenAI chat messages after the system prompt, each
//                                  assistant message carrying "usage": {input, output}
//     "compactions": [{"at": i, "summary": s, "kept": k, "tokens": t}, ...],
//                                  earlier compactions: made after message i, keeping
//                                  messages from k on ("" = none kept)
//     "tokens": n,                 the context size now
//     "window": w, "reserve": r, "keep": k, "ratio": x }
//
// The reply is {"summary": s, "kept": index of the first message kept, or null for
// none, "details": Fabric's own record of the compaction} or {"cancel": reason}.
//
// pi-fabric is found at $PI_FABRIC, else next to this repository.
import { dirname, join } from "node:path";

const root = process.env.PI_FABRIC ?? join(dirname(import.meta.dir), "pi-fabric");
const { compileFabricSummary } = await import(join(root, "src/compaction/hook.ts"));

// entropybench's tools under the names Pi gives the same operations, so Fabric
// tracks which files were read and changed the way it does in a Pi session.
const PI_NAMES: Record<string, string> = { list_dir: "ls", read_file: "read", write_file: "write", edit_file: "edit" };

type Chat = {
  role: string;
  content?: string | null;
  reasoning_content?: string;
  reasoning?: string;
  tool_calls?: { id: string; function: { name: string; arguments?: string } }[];
  tool_call_id?: string;
  name?: string;
  usage?: { input?: number; output?: number };
};
type Compaction = { at: number; summary: string; kept: number | null; tokens: number };

const request = await Bun.stdin.json() as {
  messages: Chat[]; compactions: Compaction[]; tokens: number;
  window: number; reserve: number; keep: number; ratio: number;
};

// Fixed timestamps: the summary carries the last one, and it must not depend on the clock.
const stamp = (i: number) => new Date(Date.UTC(2026, 0, 1) + i * 1000).toISOString();
const id = (i: number) => `m${i}`;

const callNames = new Map<string, string>();
const parse = (text?: string) => {
  try { const value = JSON.parse(text || "{}"); return value && typeof value === "object" ? value : {}; } catch { return {}; }
};

const toPi = (m: Chat, i: number) => {
  const timestamp = Date.parse(stamp(i));
  if (m.role === "assistant") {
    const content: unknown[] = [];
    const thinking = m.reasoning_content || m.reasoning;
    if (thinking) content.push({ type: "thinking", thinking });
    if (m.content) content.push({ type: "text", text: m.content });
    for (const call of m.tool_calls ?? []) {
      const name = PI_NAMES[call.function.name] ?? call.function.name;
      callNames.set(call.id, name);
      content.push({ type: "toolCall", id: call.id, name, arguments: parse(call.function.arguments) });
    }
    const input = m.usage?.input ?? 0, output = m.usage?.output ?? 0;
    return { role: "assistant", content, timestamp, stopReason: m.tool_calls?.length ? "toolUse" : "stop",
             usage: { input, output, cacheRead: 0, cacheWrite: 0, totalTokens: input + output } };
  }
  if (m.role === "tool") {
    const text = m.content ?? "";
    return { role: "toolResult", toolCallId: m.tool_call_id, toolName: callNames.get(m.tool_call_id ?? "") ?? "",
             content: [{ type: "text", text }], isError: text.startsWith("error:"), timestamp };
  }
  return { role: "user", content: [{ type: "text", text: m.content ?? "" }], timestamp };
};

// The raw log with each earlier compaction as a marker after the message it followed.
const entries: Record<string, unknown>[] = [];
let parent: string | null = null;
const push = (entry: Record<string, unknown>) => { entries.push({ ...entry, parentId: parent }); parent = entry.id as string; };
const markers = [...request.compactions].sort((a, b) => a.at - b.at);
let next = 0;
request.messages.forEach((m, i) => {
  push({ type: "message", id: id(i), timestamp: stamp(i), message: toPi(m, i) });
  while (next < markers.length && markers[next]!.at === i) {
    const c = markers[next]!;
    push({ type: "compaction", id: `c${next}`, timestamp: stamp(i), summary: c.summary,
           firstKeptEntryId: c.kept === null ? "" : id(c.kept), tokensBefore: c.tokens });
    next++;
  }
});

const result = compileFabricSummary(entries, request.tokens, undefined, undefined, {
  contextWindow: request.window, targetContextRatio: request.ratio,
  reserveTokens: request.reserve, keepRecentTokens: request.keep,
});
if ("cancel" in result) {
  console.log(JSON.stringify({ cancel: result.reason }));
} else {
  const kept = result.compaction.firstKeptEntryId;
  console.log(JSON.stringify({ summary: result.compaction.summary, kept: kept ? Number(kept.slice(1)) : null,
                               details: result.compaction.details }));
}
