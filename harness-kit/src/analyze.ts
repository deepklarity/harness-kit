// `harnesskit prove analyze`: where does each run's time go. Reads the saved
// session logs (stream-json or Claude Code transcript) and writes analysis.json
// per scenario plus one analysis.md across scenarios.
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, basename } from "node:path";

// A file named in a shell command: agents often read with sed, cat or grep, and run scripts by path.
const COMMAND_FILE = /(?:^|[\s'"=(])((?:\.{0,2}\/)?[\w.-]+(?:\/[\w.-]+)*\.(?:py|md|rst|toml|cfg|ini|txt|json|ya?ml|sh|mjs|js|ts))(?=$|[\s'":;|)&>])/g;

/** The files or folders one tool call names, as written (not yet relative to a worktree). */
export function namedFiles(tool: string, input: any): string[] {
  const named =
    tool === "Read" || tool === "Edit" || tool === "Write" || tool === "MultiEdit" ? [input?.file_path]
    : tool === "Grep" || tool === "Glob" ? [input?.path]
    : tool === "Bash" && typeof input?.command === "string" ? [...input.command.matchAll(COMMAND_FILE)].map((m) => m[1])
    : [];
  return named.filter((p): p is string => typeof p === "string" && p !== "");
}

export const relativize = (p: string, cwd = ""): string => {
  let r = p.replace(/^.*\/\.harnesskit\/proof\/[^/]+\/(before|after)\//, "");
  if (cwd && r === cwd) r = ".";
  else if (cwd && r.startsWith(cwd + "/")) r = r.slice(cwd.length + 1);
  return r.replace(/^\.\//, "");
};

type Kind = "read" | "search" | "run" | "edit" | "subagent";
const KINDS: Kind[] = ["read", "search", "run", "edit", "subagent"];
// Time is tool time by kind, plus the model's own time before a call (generation, deliberation) and after the last one (final).
type TimeKey = Kind | "generation" | "deliberation" | "final";
const TIME_KEYS: TimeKey[] = [...KINDS, "generation", "deliberation", "final"];

/** Estimated generation speed. The log has no per-call generation time, so tokens_out / this is an estimate; the rest of the gap is deliberation. */
export const TOKENS_PER_SECOND = 80;

export type Cause = "environment" | "harness" | "task";
export type CallOut = { tool: string; target: string; kind: Kind; error: boolean; cause: Cause | null; bytes: number; s: number | null };
/** One assistant message: its tool calls run together, so think time and tool time belong to the message. */
export type Step = { i: number; t: number | null; tool: string; target: string; calls: CallOut[]; tokens_in: number; tokens_out: number; error: boolean; cause: Cause | null; bytes: number; kind: Kind; s: number | null; think_s: number | null; gen_s: number | null; delib_s: number | null };

export type Tokens = { cached: number; uncached: number; output: number };
export type TimeSplit = { waste: number; verification: number; work: number };

export type HalfAnalysis = {
  seconds: number | null;
  steps: Step[];
  tokens: Tokens;
  time_by_kind: Record<TimeKey, { seconds: number; share: number }> | null;
  time_split: TimeSplit | null;
  calls_by_kind: Record<Kind, number>;
  first_relevant: { step: number; seconds: number | null; file: string; via: "file" | "doc" } | null;
  waste: { failed: number; repeated_reads: string[]; repeated_commands: string[]; empty_searches: number; total: number };
  harness_used: string[];
  harness_missed: string[];
};

/** First real command word of a Bash command: skips `cd x &&`, env assignments, `sudo`. */
function commandHead(cmd: string): string {
  for (const seg of cmd.split(/&&|;|\n/)) {
    const words = seg.trim().split(/\s+/).filter((w) => w && !/^[A-Za-z_]+=/.test(w) && w !== "sudo" && w !== "time");
    if (!words.length || words[0] === "cd") continue;
    return words[0].replace(/^.*\//, "");
  }
  return "";
}

function kindOf(tool: string, input: any): Kind {
  if (tool === "Read") return "read";
  if (tool === "Grep" || tool === "Glob") return "search";
  if (tool === "Task" || tool === "Agent") return "subagent";
  if (tool === "Edit" || tool === "Write" || tool === "MultiEdit" || tool === "NotebookEdit") return "edit";
  if (tool === "Bash") {
    const h = commandHead(String(input?.command ?? ""));
    if (/^(cat|sed|head|tail|less|nl|bat|more)$/.test(h)) return "read";
    if (/^(grep|egrep|rg|ag|find|fd|ack)$/.test(h)) return "search";
  }
  return "run";
}

function targetOf(tool: string, input: any, cwd: string): string {
  const rel = (p: unknown) => relativize(String(p ?? ""), cwd);
  if (tool === "Read" || tool === "Edit" || tool === "Write" || tool === "MultiEdit") return rel(input?.file_path);
  if (tool === "Grep") return `${input?.pattern ?? ""}${input?.path ? ` in ${rel(input.path)}` : ""}`;
  if (tool === "Glob") return String(input?.pattern ?? "");
  if (tool === "Bash") return String(input?.command ?? "").replace(/\s+/g, " ").trim().slice(0, 120);
  return String(input?.description ?? input?.prompt ?? input?.command ?? "").replace(/\s+/g, " ").slice(0, 120);
}

const resultText = (c: unknown): string =>
  typeof c === "string" ? c : Array.isArray(c) ? c.map((b: any) => (typeof b === "string" ? b : b?.text ?? "")).join("\n") : "";

const NONZERO = /exit(?:ed)?(?: with)?(?: code)?:? ([1-9]\d*)\b/i;
const EMPTY = /^\s*(no (files|matches) found.*)?\s*$/i;
const ENVIRONMENT = /no such tool available|no matches found|command not found|is not recognized as/i;
const VERIFY = /(?:^|[\s;&|/])(?:pytest|py\.test|unittest|vitest|jest|tox|ruff|flake8|mypy|pylint|eslint|tsc|npm (?:run )?(?:test|verify|lint|check)|yarn (?:test|lint)|make (?:test|check|lint|verify)|verify\.sh|run_tests?|manage\.py test)|gate/i;

const sec = (ms: number) => Math.round(ms / 100) / 10;

type Call = { id: string; tool: string; target: string; kind: Kind; files: string[]; command: string; ts: number | null; end: number | null; error: boolean; bytes: number; text?: string; s: number | null };
type Raw = { step: Step; calls: Call[]; ts: number | null; think: number | null };

/** Fold one session log into steps (one per assistant message), tokens, and the counts that do not need the worktrees. Repeated tool_use and tool_result entries count once. */
export function parseRun(lines: string[]): { raws: Raw[]; seconds: number | null; hasTime: boolean; lastAssistantTs: number | null; tokens: Tokens } {
  const events: any[] = [];
  for (const l of lines) {
    try {
      events.push(JSON.parse(l));
    } catch {
      /* partial line */
    }
  }
  const stamp = (e: any) => {
    const t = Date.parse(e.timestamp ?? "");
    return Number.isNaN(t) ? null : t;
  };
  const times = events.map(stamp).filter((t): t is number => t !== null);
  const hasTime = times.length > 1;
  const start = hasTime ? Math.min(...times) : 0;
  const cwd = String(events.find((e) => typeof e.cwd === "string")?.cwd ?? "");
  const usage = new Map<string, { i: number; cached: number; o: number }>();
  for (const e of events) {
    if (e.type !== "assistant" || !e.message?.usage || !e.message.id) continue;
    const u = e.message.usage;
    const cur = usage.get(e.message.id) ?? { i: 0, cached: 0, o: 0 };
    usage.set(e.message.id, {
      i: Math.max(cur.i, (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0)),
      cached: Math.max(cur.cached, u.cache_read_input_tokens ?? 0),
      o: Math.max(cur.o, u.output_tokens ?? 0),
    });
  }
  const tokens: Tokens = { cached: 0, uncached: 0, output: 0 };
  for (const u of usage.values()) {
    tokens.cached += u.cached;
    tokens.uncached += u.i;
    tokens.output += u.o;
  }
  const raws: Raw[] = [];
  const byMessage = new Map<string, Raw>();
  const byUse = new Map<string, Call>();
  const answered = new Set<string>();
  let lastResult = hasTime ? start : null as number | null;
  let lastAssistantTs: number | null = null;
  for (const e of events) {
    const ts = stamp(e);
    if (e.type === "assistant" && Array.isArray(e.message?.content)) {
      if (ts !== null) lastAssistantTs = ts;
      for (const c of e.message.content) {
        if (c.type !== "tool_use" || byUse.has(c.id)) continue;
        const mid = e.message.id ? String(e.message.id) : `use:${c.id}`;
        let raw = byMessage.get(mid);
        if (!raw) {
          const u = usage.get(mid);
          const think = hasTime && ts !== null && lastResult !== null ? Math.max(0, ts - lastResult) : null;
          const step: Step = { i: raws.length + 1, t: hasTime && ts !== null ? sec(ts - start) : null, tool: "", target: "", calls: [], tokens_in: (u?.i ?? 0) + (u?.cached ?? 0), tokens_out: u?.o ?? 0, error: false, cause: null, bytes: 0, kind: "run", s: null, think_s: null, gen_s: null, delib_s: null };
          raw = { step, calls: [], ts, think };
          raws.push(raw);
          byMessage.set(mid, raw);
        }
        const call: Call = {
          id: c.id, tool: c.name, target: targetOf(c.name, c.input, cwd), kind: kindOf(c.name, c.input),
          files: namedFiles(c.name, c.input).map((p) => relativize(p, cwd)),
          command: c.name === "Bash" ? String(c.input?.command ?? "") : "", ts, end: null, error: false, bytes: 0, s: null,
        };
        raw.calls.push(call);
        byUse.set(c.id, call);
      }
    } else if (e.type === "user" && Array.isArray(e.message?.content)) {
      for (const c of e.message.content) {
        if (c.type !== "tool_result" || answered.has(c.tool_use_id)) continue;
        const call = byUse.get(c.tool_use_id);
        if (!call) continue;
        answered.add(c.tool_use_id);
        const text = resultText(c.content);
        call.bytes = Buffer.byteLength(text);
        call.error = c.is_error === true || NONZERO.test(text.slice(0, 400));
        call.text = text;
        if (ts !== null && call.ts !== null) {
          call.end = ts;
          call.s = sec(ts - call.ts);
          if (lastResult === null || ts > lastResult) lastResult = ts;
        }
      }
    }
  }
  for (const raw of raws) {
    const st = raw.step;
    const ends = raw.calls.map((c) => c.end).filter((n): n is number => n !== null);
    st.s = ends.length && raw.ts !== null ? sec(Math.max(...ends) - raw.ts) : null;
    st.tool = [...new Set(raw.calls.map((c) => c.tool))].join("+");
    st.target = raw.calls.map((c) => c.target).join(" ; ").slice(0, 200);
    st.bytes = raw.calls.reduce((a, c) => a + c.bytes, 0);
    st.kind = [...raw.calls].sort((a, b) => (b.s ?? 0) - (a.s ?? 0))[0].kind;
    if (raw.think !== null) {
      const gen = Math.min(raw.think, (st.tokens_out / TOKENS_PER_SECOND) * 1000);
      st.think_s = sec(raw.think);
      st.gen_s = sec(gen);
      st.delib_s = sec(raw.think - gen);
    }
  }
  const result = [...events].reverse().find((e) => e.type === "result");
  const seconds = hasTime ? sec(Math.max(...times) - start) : result?.duration_ms ? sec(Number(result.duration_ms)) : null;
  return { raws, seconds, hasTime, lastAssistantTs, tokens };
}

const skipDirs = new Set([".git", "node_modules", "__pycache__", ".venv", "venv"]);
function walk(root: string, rel: string, out: string[]) {
  const abs = join(root, rel);
  let names: string[];
  try {
    names = readdirSync(abs);
  } catch {
    return;
  }
  for (const n of names) {
    if (skipDirs.has(n)) continue;
    const r = rel ? `${rel}/${n}` : n;
    let st;
    try {
      st = statSync(join(root, r));
    } catch {
      continue;
    }
    if (st.isDirectory()) walk(root, r, out);
    else out.push(r);
  }
}

/** Harness files: in the after worktree and absent from the before worktree, under docs, scripts, tests, .claude, plus CLAUDE.md and AGENTS.md. */
function harnessFiles(ws: string, slug: string): string[] {
  const base = join(ws, ".harnesskit", "proof", slug);
  const after = join(base, "after");
  if (!existsSync(after)) return [];
  const all: string[] = [];
  for (const d of ["docs", "scripts", "tests", ".claude"]) walk(after, d, all);
  for (const f of ["CLAUDE.md", "AGENTS.md"]) if (existsSync(join(after, f))) all.push(f);
  const before = join(base, "before");
  return all.filter((f) => !existsSync(join(before, f))).sort();
}

const HARNESS_PATTERN = /^(docs(\/|$)|CLAUDE\.md$|AGENTS\.md$|\.claude(\/|$)|scripts(\/|$)|tests(\/|$))/;
const MISS_ROOTS = /^(docs\/(breadcrumb_analysis|testing_process|guides)\/|scripts\/)/;

const diffFiles = (path: string): string[] => {
  if (!existsSync(path)) return [];
  return [...readFileSync(path, "utf8").matchAll(/^diff --git a\/(\S+) b\/(\S+)/gm)].map((m) => m[2]);
};

const matches = (opened: string, rel: string) => opened === rel || opened.endsWith("/" + rel) || rel.endsWith("/" + opened);

/** One target set per scenario, the same for both halves: files changed in either diff plus backticked paths in mustMention. */
export function targetFiles(ws: string, slug: string, sc: any): string[] {
  const out = new Set([...diffFiles(join(ws, "writeup", "proof", slug, "before.diff")), ...diffFiles(join(ws, "writeup", "proof", slug, "after.diff"))]);
  for (const m of (sc.mustMention ?? []) as string[]) for (const t of String(m).matchAll(/`([\w./-]+\.\w+)`/g)) out.add(t[1]);
  return [...out];
}

function analyzeHalf(ws: string, slug: string, half: "before" | "after", targets: string[], harness: string[]): HalfAnalysis | null {
  const log = join(ws, "writeup", "proof", slug, `${half}.jsonl`);
  if (!existsSync(log)) return null;
  const { raws, seconds, hasTime, lastAssistantTs, tokens } = parseRun(readFileSync(log, "utf8").split("\n").filter(Boolean));
  const steps = raws.map((r) => r.step);
  const calls = raws.flatMap((r) => r.calls);

  const docText = (f: string): string => {
    for (const w of ["after", "before"]) {
      const p = join(ws, ".harnesskit", "proof", slug, w, f);
      try {
        if (statSync(p).isFile()) return readFileSync(p, "utf8");
      } catch {
        /* not in this worktree */
      }
    }
    return "";
  };
  const namesTarget = (text: string) => targets.find((t) => text.includes(t));

  // harness files, as opened
  const opened = [...new Set(calls.flatMap((c) => c.files))];
  const hset = new Set(harness);
  const isHarness = (p: string) => (harness.length ? hset.has(p) || harness.some((h) => h.startsWith(p.replace(/\/$/, "") + "/")) : HARNESS_PATTERN.test(p));

  // error cause, waste flags per call
  const reads = new Map<string, number>();
  const cmds = new Map<string, number>();
  let failed = 0, empty = 0;
  const wasted = new Set<Call>();
  for (const c of calls) {
    const text = String(c.text ?? "");
    const isEmpty = c.kind === "search" && (EMPTY.test(text) || (c.error && !text.replace(/\[?exit(ed)?[^\]\n]*\]?/gi, "").trim()));
    if (isEmpty && c.text !== undefined) { empty++; wasted.add(c); }
    else if (c.error) { failed++; wasted.add(c); }
    const key = c.tool === "Read" ? "r:" + c.target : c.command ? "c:" + c.command : "";
    if (key) {
      const m = key[0] === "r" ? reads : cmds;
      const n = (m.get(key.slice(2)) ?? 0) + 1;
      m.set(key.slice(2), n);
      if (n > 1) wasted.add(c);
    }
  }
  const causeOf = (c: Call): Cause | null =>
    !c.error ? null : ENVIRONMENT.test(String(c.text ?? "")) ? "environment" : c.files.some((f) => !hset.has(f) && harness.length && HARNESS_PATTERN.test(f) && /no such file|not found/i.test(String(c.text ?? ""))) ? "harness" : "task";
  for (const r of raws) {
    const outs: CallOut[] = r.calls.map((c) => ({ tool: c.tool, target: c.target, kind: c.kind, error: c.error, cause: causeOf(c), bytes: c.bytes, s: c.s }));
    r.step.calls = outs;
    r.step.error = outs.some((o) => o.error);
    r.step.cause = outs.find((o) => o.error)?.cause ?? null;
  }

  // time by kind, and the split into waste, verification and work
  const callsByKind = Object.fromEntries(KINDS.map((k) => [k, 0])) as Record<Kind, number>;
  for (const s of steps) callsByKind[s.kind]++;
  let time_by_kind: HalfAnalysis["time_by_kind"] = null;
  let time_split: TimeSplit | null = null;
  if (hasTime) {
    const secs = Object.fromEntries(TIME_KEYS.map((k) => [k, 0])) as Record<TimeKey, number>;
    const split: TimeSplit = { waste: 0, verification: 0, work: 0 };
    for (const r of raws) {
      const s = r.step;
      secs[s.kind] += s.s ?? 0;
      secs.generation += s.gen_s ?? 0;
      secs.deliberation += s.delib_s ?? 0;
      const own = (s.think_s ?? 0) + (s.s ?? 0);
      const bucket = r.calls.every((c) => wasted.has(c)) ? "waste" : r.calls.some((c) => c.command && VERIFY.test(c.command) && causeOf(c) !== "environment") ? "verification" : "work";
      split[bucket] += own;
    }
    const lastEnd = Math.max(0, ...calls.map((c) => c.end ?? 0));
    if (lastAssistantTs !== null && lastEnd && lastAssistantTs > lastEnd) {
      const fin = sec(lastAssistantTs - lastEnd);
      secs.final += fin;
      split.work += fin;
    }
    const total = TIME_KEYS.reduce((a, k) => a + secs[k], 0) || 1;
    time_by_kind = Object.fromEntries(TIME_KEYS.map((k) => [k, { seconds: Math.round(secs[k] * 10) / 10, share: Math.round((secs[k] / total) * 100) / 100 }])) as any;
    time_split = { waste: Math.round(split.waste * 10) / 10, verification: Math.round(split.verification * 10) / 10, work: Math.round(split.work * 10) / 10 };
  }

  // first relevant: the step that opens a target, or a doc whose text names one
  let first_relevant: HalfAnalysis["first_relevant"] = null;
  for (const r of raws) {
    let hit: { file: string; via: "file" | "doc" } | null = null;
    for (const f of r.calls.flatMap((c) => c.files)) {
      const t = targets.find((x) => matches(f, x));
      if (t) { hit = { file: t, via: "file" }; break; }
      const named = /\.md$/.test(f) ? namesTarget(docText(f)) : undefined;
      if (named && !hit) hit = { file: named, via: "doc" };
    }
    if (hit) {
      first_relevant = { step: r.step.i, seconds: r.step.t, ...hit };
      break;
    }
  }

  const extra = (m: Map<string, number>) => [...m.values()].reduce((a, n) => a + Math.max(0, n - 1), 0);
  const waste = {
    failed,
    repeated_reads: [...reads].filter(([, n]) => n > 1).map(([p]) => p),
    repeated_commands: [...cmds].filter(([, n]) => n > 1).map(([c]) => c.replace(/\s+/g, " ").slice(0, 100)),
    empty_searches: empty,
    total: failed + extra(reads) + extra(cmds) + empty,
  };

  // harness
  const harness_used = opened.filter(isHarness);
  const openedSet = new Set(opened);
  // A target named by a quarter of the docs (app.py, say) points at none of them in particular.
  const docs = harness.filter((h) => MISS_ROOTS.test(h) && !/(^|\/)_INDEX\.md$/.test(h)).map((h) => ({ h, text: docText(h) }));
  const specific = targets.filter((t) => docs.filter((d) => d.text.includes(t)).length * 4 <= docs.length || docs.length < 8);
  const harness_missed = docs.filter((d) => !openedSet.has(d.h) && specific.some((t) => d.text.includes(t))).map((d) => d.h);

  return { seconds, steps, tokens, time_by_kind, time_split, calls_by_kind: callsByKind, first_relevant, waste, harness_used, harness_missed };
}

export type ScenarioAnalysis = { slug: string; title: string; before: HalfAnalysis | null; after: HalfAnalysis | null };

export function analyzeScenario(ws: string, slug: string): ScenarioAnalysis {
  const dir = join(ws, "writeup", "proof", slug);
  const sc = JSON.parse(readFileSync(join(dir, "scenario.json"), "utf8"));
  const harness = harnessFiles(ws, slug);
  const targets = targetFiles(ws, slug, sc);
  const result: ScenarioAnalysis = { slug, title: String(sc.title ?? slug), before: analyzeHalf(ws, slug, "before", targets, harness), after: analyzeHalf(ws, slug, "after", targets, harness) };
  writeFileSync(join(dir, "analysis.json"), JSON.stringify(result, null, 1) + "\n");
  return result;
}

/** Saved analyses, one per scenario, in slug order. */
export function readAnalyses(ws: string): ScenarioAnalysis[] {
  const root = join(ws, "writeup", "proof");
  if (!existsSync(root)) return [];
  return readdirSync(root).sort().map((s) => join(root, s, "analysis.json")).filter(existsSync).map((p) => JSON.parse(readFileSync(p, "utf8")));
}

const arrow = (b: unknown, a: unknown) => `${b ?? "-"} → ${a ?? "-"}`;
const use = (h: HalfAnalysis | null) => h?.harness_used.length;

export type Row = { slug: string; cells: string[] };
export const TABLE_HEAD = ["Scenario", "Seconds b→a", "Steps b→a", "Time to right file b→a", "Waste b→a", "Waste / verify / work s b→a", "Harness used b→a", "Harness missed (after)"];

export function crossRows(list: ScenarioAnalysis[]): Row[] {
  return list.map((s) => ({
    slug: s.slug,
    cells: [
      s.slug,
      arrow(s.before?.seconds, s.after?.seconds),
      arrow(s.before?.steps.length, s.after?.steps.length),
      arrow(...([s.before, s.after].map((h) => (h?.first_relevant ? (h.first_relevant.seconds !== null ? `${h.first_relevant.seconds}s (step ${h.first_relevant.step})` : `step ${h.first_relevant.step}`) : "never")) as [string, string])),
      arrow(s.before?.waste.total, s.after?.waste.total),
      arrow(...([s.before, s.after].map((h) => (h?.time_split ? `${h.time_split.waste}/${h.time_split.verification}/${h.time_split.work}` : undefined)) as [string, string])),
      arrow(use(s.before), use(s.after)),
      String(s.after?.harness_missed.length ?? "-"),
    ],
  }));
}

const mdEsc = (s: string) => s.replace(/\|/g, "\\|").replace(/\n/g, " ");

export function renderMarkdown(list: ScenarioAnalysis[]): string {
  const out = ["# Where the time goes", "", `| ${TABLE_HEAD.join(" | ")} |`, `|${TABLE_HEAD.map(() => "---").join("|")}|`];
  for (const r of crossRows(list)) out.push(`| ${r.cells.map(mdEsc).join(" | ")} |`);
  out.push(
    "",
    "b = baseline run, a = harness run. Waste is failed calls, repeated reads, repeated commands and empty searches. Time is split into waste (steps whose calls all failed, repeated or came back empty), verification (tests, the gate, linters) and work (the rest), so time on the gate is not read as slowness. Time to right file uses one target set per scenario for both halves (files in either diff plus paths in mustMention); a step reaches it by opening the file or a doc that names it.",
    `Generation is an estimate: output tokens at ${TOKENS_PER_SECOND} per second, capped by the gap before the call. Deliberation is the rest of that gap; final is the time after the last tool result.`,
  );
  for (const s of list) {
    out.push("", `## ${s.slug}`, "", s.title);
    for (const h of ["before", "after"] as const) {
      const r = s[h];
      if (!r) continue;
      out.push("", `### ${h}`, "");
      if (r.time_by_kind) out.push("Time: " + TIME_KEYS.map((k) => `${k} ${r.time_by_kind![k].seconds}s (${Math.round(r.time_by_kind![k].share * 100)}%)`).join(", "), "");
      else out.push("No timestamps in this log.", "");
      out.push(`Input tokens: ${r.tokens.cached} cached, ${r.tokens.uncached} uncached; output ${r.tokens.output}`, "");
      out.push(`Harness used: ${r.harness_used.join(", ") || "none"}`, `Harness missed: ${r.harness_missed.join(", ") || "none"}`, "");
      out.push("| i | t | think s (gen / deliberation) | tool s | kind | target | error |", "|---|---|---|---|---|---|---|");
      for (const s2 of r.steps) {
        const think = s2.think_s === null ? "-" : `${s2.think_s} (${s2.gen_s} / ${s2.delib_s})`;
        out.push(`| ${s2.i} | ${s2.t ?? "-"} | ${think} | ${s2.s ?? "-"} | ${s2.kind} | ${mdEsc(`${s2.tool} ${s2.target}`.slice(0, 160))} | ${s2.error ? s2.cause ?? "yes" : ""} |`);
      }
    }
  }
  return out.join("\n") + "\n";
}

export function analyze(ws: string, slug: string | undefined, log: (s: string) => void): number {
  const root = join(ws, "writeup", "proof");
  const slugs = slug ? [slug] : existsSync(root) ? readdirSync(root).sort().filter((s) => existsSync(join(root, s, "scenario.json"))) : [];
  if (!slugs.length) throw new Error(`no scenarios under ${root}`);
  for (const s of slugs) if (!existsSync(join(root, s, "scenario.json"))) throw new Error(`no scenario ${s} under ${root}`);
  for (const s of slugs) {
    const a = analyzeScenario(ws, s);
    log(`${s}: ${arrow(a.before?.steps.length, a.after?.steps.length)} steps, ${arrow(a.before?.seconds, a.after?.seconds)} seconds`);
  }
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "analysis.md"), renderMarkdown(readAnalyses(ws)));
  log("wrote writeup/proof/analysis.md");
  return 0;
}
