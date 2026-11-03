/**
 * harnesskit prove: run one scenario twice, once in a worktree at the
 * workspace's baseline commit (before init) and once at HEAD (after), then
 * record cost, proof exit and answers, write a blind judge packet and a page.
 *
 *   harnesskit prove <scenario.md> [--runner herdr|headless] [--budget <usd>]
 *                    [--cap-min <n>] [--baseline <sha>] [--dry-run]
 *   harnesskit prove reply <slug>
 *   harnesskit prove collect <slug>
 *   harnesskit prove judge <slug> [--again]
 *   harnesskit prove render
 *   harnesskit prove --clean <slug>
 */
import { spawn, spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { headOf, nestedRepos, readManifest } from "./manifest.js";
import { analyze, crossRows, namedFiles, readAnalyses, TABLE_HEAD } from "./analyze.js";
import { basename, join, resolve } from "node:path";

// ---------------------------------------------------------------- scenario

export type Scenario = {
  slug: string;
  title: string;
  tier: string;
  difficulty: string;
  kind: "question" | "outcome" | "feature";
  bills: boolean;
  exercises: string;
  prompt: string;
  mustMention: string[];
  proof: string | null;
  reset: string | null;
  reply: string | null;
  /** Feature scenarios: the folder of hidden acceptance tests, relative to the workspace. */
  hiddenDir: string | null;
  /** The repo's own test and lint commands, run in each worktree after the run. */
  suite: string | null;
  lint: string | null;
};

const HIDDEN_ROOT = "writeup/scenarios/hidden";
/** A bare name means a folder under writeup/scenarios/hidden/; a path is taken as written. */
export function hiddenFolder(block: string | null): string | null {
  // The first line that names something; a code fence around it is not the name.
  const first = (block ?? "").split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith("```"));
  if (!first) return null;
  const name = first.replace(/^[-*]\s*/, "").replace(/`/g, "").replace(/\/+$/, "");
  if (!name) return null;
  return name.includes("/") ? name : `${HIDDEN_ROOT}/${name}`;
}

export const slugify = (title: string): string => title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

function fenced(block: string | null): string | null {
  if (block === null) return null;
  const m = /```[a-z]*\n([\s\S]*?)```/.exec(block);
  const body = (m ? m[1] : block).split("\n").filter((l) => l.trim()).join("\n").trim();
  return body || null;
}

export function parseScenario(text: string, path: string): Scenario {
  const lines = text.split("\n");
  const title = lines[0]?.startsWith("#") ? lines[0].replace(/^#+/, "").trim() : path.replace(/^.*\//, "").replace(/\.md$/, "");
  const header = (key: string) => new RegExp(`^${key}:[ \\t]*(.*)$`, "m").exec(text)?.[1].trim() ?? "";
  const kind = (header("Kind").split(/\s+/)[0] || "question").toLowerCase();
  if (kind !== "question" && kind !== "outcome" && kind !== "feature") throw new Error(`${path}: Kind must be question, outcome or feature, not ${kind}`);
  const section = (name: string): string | null => {
    const m = new RegExp(`^## ${name}[ \\t]*\\n([\\s\\S]*?)(?=\\n## |$(?![\\s\\S]))`, "m").exec(text);
    return m ? m[1].replace(/^\n+|\n+$/g, "") : null;
  };
  const prompt = section("The prompt");
  if (prompt === null) throw new Error(`${path}: missing '## The prompt' section`);
  const mustMention = (section("A correct answer must mention") ?? "")
    .split("\n")
    .filter((l) => l.trim().startsWith("-"))
    .map((l) => l.trim().replace(/^-\s*/, ""));
  const proof = fenced(section("The proof"));
  if (kind !== "question" && !proof) throw new Error(`${path}: an ${kind} scenario needs a '## The proof' section with the command that checks the result`);
  return {
    // The file name is short, unique in the folder and chosen by the author; a title can run to a paragraph.
    slug: slugify(basename(path).replace(/\.md$/i, "")),
    title,
    tier: header("Tier").split(/\s+/)[0] || "mid",
    difficulty: header("Difficulty").split(/\s+/)[0],
    kind,
    bills: ["yes", "true"].includes(header("Bills").split(/\s+/)[0].toLowerCase()),
    exercises: header("Exercises"),
    prompt: prompt.trim(),
    mustMention,
    proof,
    reset: fenced(section("Reset")),
    reply: section("If the agent asks")?.trim() || null,
    hiddenDir: hiddenFolder(section("Hidden tests")),
    suite: fenced(section("Suite")),
    lint: fenced(section("Lint")),
  };
}

/** True when the final answer puts a question to the person: one of its last lines ends in a question mark. */
export function asksPerson(answer: string): boolean {
  const lines = answer.split("\n").map((l) => l.trim()).filter(Boolean).slice(-8);
  return lines.some((l) => /\?[\s*_)"'`]*$/.test(l));
}

// ----------------------------------------------------------- profile, ledger

export function parseProfile(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const m = /^([a-z_][a-z0-9_]*):[ \t]*(.*)$/.exec(raw.replace(/\s+#.*$/, "").trimEnd());
    if (m && m[2] !== "") out[m[1]] = m[2].trim();
  }
  return out;
}

export type LedgerRow = {
  slug: string;
  half: string;
  cost_usd: number;
  tokens: number;
  seconds: number;
  proof_exit: string;
  started_at: string;
};
const LEDGER_HEADER = "slug,half,cost_usd,tokens,seconds,proof_exit,started_at";

export function parseLedger(text: string): LedgerRow[] {
  return text
    .split("\n")
    .slice(1)
    .filter((l) => l.trim())
    .map((l) => {
      const [slug, half, cost, tokens, seconds, proof_exit, started_at] = l.split(",");
      return { slug, half, cost_usd: Number(cost), tokens: Number(tokens), seconds: Number(seconds), proof_exit, started_at };
    });
}

export const ledgerLine = (r: LedgerRow): string =>
  [r.slug, r.half, r.cost_usd, r.tokens, r.seconds, r.proof_exit, r.started_at].join(",");

/** The largest earlier run, or 0.75 when there is none. */
export const estimateRun = (rows: LedgerRow[]): number => (rows.length ? Math.max(...rows.map((r) => r.cost_usd)) : 0.75);

export function budgetCheck(spent: number, estimate: number, runs: number, budget: number) {
  const need = estimate * runs;
  return { ok: spent + need <= budget, need, remaining: Math.round((budget - spent) * 1e4) / 1e4 };
}

// ------------------------------------------------------------ pricing, streams

type Usage = {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
};

// Prices are estimates as of 2026-10; update them when list prices change.
// USD per million tokens: input, output, cache read, cache write. An estimate
// from published list prices; it only prices herdr transcripts and timed-out
// runs. Headless runs report the real figure in the result event.
const PRICES: Record<string, [number, number, number, number]> = {
  sonnet: [3, 15, 0.3, 3.75],
  opus: [5, 25, 0.5, 6.25],
  haiku: [1, 5, 0.1, 1.25],
};

export function costFromUsage(model: string, u: Usage): number {
  const key = /opus/i.test(model) ? "opus" : /haiku/i.test(model) ? "haiku" : "sonnet";
  const [i, o, cr, cw] = PRICES[key];
  const usd = ((u.input_tokens ?? 0) * i + (u.output_tokens ?? 0) * o + (u.cache_read_input_tokens ?? 0) * cr + (u.cache_creation_input_tokens ?? 0) * cw) / 1e6;
  return Math.round(usd * 1e6) / 1e6;
}

const totalTokens = (u: Usage) => (u.input_tokens ?? 0) + (u.output_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0);

export type StreamSummary = { cost: number; tokens: number; turns: number; seconds: number; text: string; fromResult: boolean; opened: string[]; toolCalls: number };

/** Every distinct file or folder the run's Read, Grep and Glob calls named, and every file a Bash command named, relative to its worktree, first-seen order. */
function openedFiles(lines: string[]): string[] {
  const out: string[] = [];
  for (const l of lines) {
    let e: any;
    try {
      e = JSON.parse(l);
    } catch {
      continue;
    }
    if (e.type !== "assistant" || !Array.isArray(e.message?.content)) continue;
    for (const c of e.message.content) {
      if (c.type !== "tool_use") continue;
      const named = c.name === "Edit" || c.name === "Write" || c.name === "MultiEdit" ? [] : namedFiles(c.name, c.input);
      for (const raw of named) {
        if (typeof raw !== "string" || !raw) continue;
        const p = raw.replace(/^.*\/\.harnesskit\/proof\/[^/]+\/(before|after)\//, "").replace(/^\.\//, "");
        if (!out.includes(p)) out.push(p);
      }
    }
  }
  return out;
}

/** The text of the first user message in a session transcript, or "". */
export function firstPrompt(lines: string[]): string {
  for (const l of lines) {
    let e: any;
    try {
      e = JSON.parse(l);
    } catch {
      continue;
    }
    if (e.type !== "user" || !e.message) continue;
    const c = e.message.content;
    if (typeof c === "string") return c;
    if (Array.isArray(c)) {
      const text = c.filter((b: any) => b.type === "text").map((b: any) => b.text).join("\n");
      if (text) return text;
    }
  }
  return "";
}

const sameText = (a: string, b: string) => a.replace(/\s+/g, " ").trim() === String(b ?? "").replace(/\s+/g, " ").trim();

/** How many tools the run called; zero means it answered from memory. */
function toolCallCount(lines: string[]): number {
  let n = 0;
  for (const l of lines) {
    if (!l.includes('"tool_use"')) continue;
    try {
      const e = JSON.parse(l);
      if (e.type === "assistant" && Array.isArray(e.message?.content)) n += e.message.content.filter((c: any) => c.type === "tool_use").length;
    } catch {
      /* partial line */
    }
  }
  return n;
}

/** The verdict object in a judge's final message (a fenced json block or bare object), or null. */
export function verdictFromText(text: string): Record<string, unknown> | null {
  const fenced = /```(?:json)?\s*\n([\s\S]*?)```/.exec(text)?.[1];
  for (const candidate of [fenced, text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1)]) {
    if (!candidate) continue;
    try {
      const v = JSON.parse(candidate);
      if (v && typeof v === "object" && "preferred" in v && "A" in v && "B" in v) return v;
    } catch {
      /* not this one */
    }
  }
  return null;
}

/** Claude Code names a project's transcript folder by its path with every non-alphanumeric character as a dash. */
export const transcriptFolder = (path: string) => path.replace(/[^A-Za-z0-9]/g, "-");

const isHarnessFile = (p: string) => /^(docs(\/|$)|CLAUDE\.md$|AGENTS\.md$|\.claude(\/|$))/.test(p);

/** Fold a stream-json run (or a session transcript) into one record. The
 *  final result event is authoritative; without one, usage is summed from
 *  assistant events and priced from the table. */
export function summarizeStream(lines: string[], model: string): StreamSummary {
  const opened = openedFiles(lines);
  const events: any[] = [];
  for (const l of lines) {
    try {
      events.push(JSON.parse(l));
    } catch {
      /* partial line from a killed run */
    }
  }
  const result = [...events].reverse().find((e) => e.type === "result");
  if (result) {
    return {
      cost: Number(result.total_cost_usd ?? 0),
      tokens: totalTokens(result.usage ?? {}),
      turns: Number(result.num_turns ?? 0),
      seconds: Math.round(Number(result.duration_ms ?? 0) / 1000),
      text: String(result.result ?? ""),
      fromResult: true,
      opened,
      toolCalls: toolCallCount(lines),
    };
  }
  const sum: Usage = {};
  const seen = new Set<string>();
  let turns = 0;
  let text = "";
  const times: number[] = [];
  for (const e of events) {
    const t = Date.parse(e.timestamp ?? "");
    if (!Number.isNaN(t)) times.push(t);
    if (e.type !== "assistant" || !e.message) continue;
    turns++;
    const content = Array.isArray(e.message.content) ? e.message.content : [];
    const said = content.filter((c: any) => c.type === "text").map((c: any) => c.text).join("\n");
    if (said) text = said;
    const id = e.message.id ?? `${turns}`;
    if (seen.has(id) || !e.message.usage) continue; // one message can span several events
    seen.add(id);
    for (const k of ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"] as const) sum[k] = (sum[k] ?? 0) + (e.message.usage[k] ?? 0);
  }
  const seconds = times.length > 1 ? Math.round((Math.max(...times) - Math.min(...times)) / 1000) : 0;
  return { cost: costFromUsage(model, sum), tokens: totalTokens(sum), turns, seconds, text, fromResult: false, opened, toolCalls: toolCallCount(lines) };
}

// ------------------------------------------------------------------ shell, git

type Log = (s: string) => void;
type Ctx = { cwd: string; log: Log };

// -e: a proof of several lines fails on its first failing line, not only its last.
export function sh(command: string, cwd: string): number {
  return spawnSync("sh", ["-ec", command], { cwd, stdio: "ignore" }).status ?? 1;
}
function git(cwd: string, ...args: string[]) {
  const r = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8" });
  return { ok: r.status === 0, out: (r.stdout ?? "").trim(), err: (r.stderr ?? "").trim() };
}

function readBaseline(ws: string): string | null {
  for (const rel of ["docs/harnesskit/manifest", "docs/harnesskit/.manifest.json"]) {
    const p = join(ws, rel);
    if (!existsSync(p)) continue;
    const text = readFileSync(p, "utf8");
    const m = /^\s*"?baseline_commit"?\s*[:= ]\s*"?([0-9a-f]{7,40})"?/m.exec(text);
    if (m) return m[1];
  }
  return null;
}

const proofDir = (ws: string, slug: string) => join(ws, "writeup", "proof", slug);
const sealedMapping = (ws: string, slug: string) => join(ws, ".harnesskit", "proof", slug, "mapping.json");
const worktreePath = (ws: string, slug: string, half: string) => join(ws, ".harnesskit", "proof", slug, half);

/** Each nested repo's worktree in `path`, detached at the sha given for it. */
function addNested(ws: string, path: string, nested: Record<string, string>) {
  for (const [rel, sha] of Object.entries(nested)) {
    const at = join(path, rel);
    const repo = join(ws, rel);
    if (existsSync(join(at, ".git"))) {
      git(at, "reset", "--hard", "-q", sha);
      git(at, "clean", "-fdq");
      continue;
    }
    git(repo, "worktree", "prune");
    mkdirSync(join(at, ".."), { recursive: true });
    const r = git(repo, "worktree", "add", "--detach", at, sha);
    if (!r.ok) throw new Error(`git worktree add failed for ${rel}: ${r.err}`);
  }
}

function removeNested(ws: string, path: string, nested: string[]) {
  for (const rel of nested) {
    const at = join(path, rel);
    if (existsSync(at)) git(join(ws, rel), "worktree", "remove", "--force", at);
    git(join(ws, rel), "worktree", "prune");
  }
}

function ensureWorktree(ws: string, slug: string, half: string, commit: string, nested: Record<string, string>): string {
  const path = worktreePath(ws, slug, half);
  const want = git(ws, "rev-parse", commit).out;
  if (existsSync(path)) {
    if (git(path, "rev-parse", "HEAD").out === want) {
      git(path, "reset", "--hard", "-q", want);
      git(path, "clean", "-fdq");
      rmSync(join(path, "writeup"), { recursive: true, force: true });
      addNested(ws, path, nested);
      return path;
    }
    removeNested(ws, path, Object.keys(nested));
    git(ws, "worktree", "remove", "--force", path);
  }
  mkdirSync(join(ws, ".harnesskit", "proof", slug), { recursive: true });
  const r = git(ws, "worktree", "add", "--detach", path, want);
  if (!r.ok) throw new Error(`git worktree add failed for ${half}: ${r.err}`);
  rmSync(join(path, "writeup"), { recursive: true, force: true });
  addNested(ws, path, nested);
  return path;
}

/** Paths in `git status --porcelain` output; the status code is matched, not sliced, because the git helper trims the first line's leading space. */
export function porcelainPaths(out: string): string[] {
  return out
    .split("\n")
    .filter(Boolean)
    .map((l) => l.replace(/^\s*[MADRCU?!]{1,2}\s+/, ""));
}

function dirtyPaths(ws: string, nested: string[]): string[] {
  const inNested = (p: string) => nested.some((n) => p.replace(/\/$/, "") === n || p.startsWith(`${n}/`));
  const root = porcelainPaths(git(ws, "status", "--porcelain", "-uall").out).filter((p) => !/^("?)(writeup|\.harnesskit)\//.test(p) && !inNested(p));
  const inner = nested.filter((n) => git(join(ws, n), "status", "--porcelain").out !== "").map((n) => `${n} (nested repo)`);
  return [...root, ...inner];
}

// -------------------------------------------------------------------- runners

type Settings = { model: string; effort: string };
const claudeArgs = (prompt: string, s: Settings, headless: boolean): string[] =>
  headless
    ? ["-p", prompt, "--model", s.model, "--effort", s.effort, "--permission-mode", "auto", "--output-format", "stream-json", "--verbose"]
    : ["--model", s.model, "--effort", s.effort, "--permission-mode", "auto", prompt];

/** The second leg of a run whose agent asked the person something: same session, same flags, the scenario's reply. */
const resumeArgs = (sessionId: string, reply: string, s: Settings): string[] => [
  "-p", "--resume", sessionId, "--model", s.model, "--effort", s.effort, "--permission-mode", "auto", "--output-format", "stream-json", "--verbose", reply,
];

function sessionIdOf(lines: string[]): string | null {
  for (const l of lines) {
    try {
      const id = JSON.parse(l).session_id;
      if (typeof id === "string" && id) return id;
    } catch {
      /* partial line */
    }
  }
  return null;
}

type HalfRecord = {
  slug: string;
  half: string;
  commit: string;
  cost_usd: number;
  tokens: number;
  turns: number;
  seconds: number;
  proof_exit: number | null;
  reset_exit: number | null;
  timed_out: boolean;
  started_at: string;
  runner: string;
  files_opened: string[];
  tool_calls?: number;
  replied?: boolean;
  /** Feature scenarios: exit of the hidden tests, the repo's suite and its lint, run after the agent finished. Null when not configured. */
  hidden_exit?: number | null;
  suite_exit?: number | null;
  lint_exit?: number | null;
  diff_stat?: string;
};

type Grade = Pick<HalfRecord, "proof_exit" | "hidden_exit" | "suite_exit" | "lint_exit" | "diff_stat">;
type Gradable = { proof: string | null; hiddenDir?: string | null; suite?: string | null; lint?: string | null; kind?: string };

/** The root diff against `commit` plus each nested repo's diff against the sha its half started from, nested paths prefixed with their folder. */
function halfDiff(ws: string, worktree: string, commit: string, nestedStart: Record<string, string>): { stat: string; diff: string } {
  const nested = Object.keys(nestedStart);
  const out = (cwd: string, ...a: string[]) => spawnSync("git", ["-C", cwd, ...a], { encoding: "utf8" }).stdout ?? "";
  // Intent-to-add makes new files show up in the diff without staging their content.
  const skip = nested.map((n) => `:(exclude)${n}`);
  git(worktree, "add", "-N", "--", ".", ...skip);
  let stat = git(worktree, "diff", "--stat", commit, "--", ".", ...skip).out;
  let diff = out(worktree, "diff", commit, "--", ".", ...skip);
  for (const rel of nested) {
    const at = join(worktree, rel);
    if (!existsSync(join(at, ".git"))) continue;
    const base = nestedStart[rel];
    git(at, "add", "-N", ".");
    const pre = [`--src-prefix=a/${rel}/`, `--dst-prefix=b/${rel}/`];
    diff += out(at, "diff", ...pre, base);
    const st = git(at, "diff", "--stat", base).out.split("\n").filter(Boolean).map((l) => (l.includes(" | ") ? l.replace(/^(\s*)/, `$1${rel}/`) : l));
    stat = [stat, ...st].filter(Boolean).join("\n");
  }
  return { stat, diff };
}

/** Everything run after an agent finishes: the diff it made, the hidden tests copied in and run through the proof, then the suite and lint; the hidden files are removed again. */
function gradeHalf(ws: string, sc: Gradable, worktree: string, commit: string, dir: string, half: string, nestedStart: Record<string, string>): Grade {
  let diff_stat: string | undefined;
  if (sc.kind === "feature") {
    const d = halfDiff(ws, worktree, commit, nestedStart);
    diff_stat = d.stat;
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, `${half}.diff`), d.diff);
  }
  const copied = sc.hiddenDir ? copyHidden(resolve(ws, sc.hiddenDir), worktree) : null;
  const proof_exit = sc.proof ? sh(sc.proof, worktree) : null;
  const hidden_exit = copied ? proof_exit : null;
  if (copied) removeHidden(worktree, copied);
  const suite_exit = sc.suite ? sh(sc.suite, worktree) : null;
  const lint_exit = sc.lint ? sh(sc.lint, worktree) : null;
  return { proof_exit, hidden_exit, suite_exit, lint_exit, ...(diff_stat !== undefined ? { diff_stat } : {}) };
}

function filesUnder(root: string, rel = ""): string[] {
  return readdirSync(join(root, rel), { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? filesUnder(root, join(rel, e.name)) : [join(rel, e.name)]));
}

/** Copy the hidden files into the worktree at the paths they declare; remember what to remove. */
function copyHidden(from: string, worktree: string): { files: string[]; dirs: string[] } {
  if (!existsSync(from)) throw new Error(`hidden tests folder not found: ${from}`);
  const files = filesUnder(from);
  const dirs = new Set<string>();
  for (const rel of files) {
    // The top-most folder this copy creates is the one to remove afterwards.
    const parts = rel.split("/").slice(0, -1);
    for (let i = 1; i <= parts.length; i++) {
      const d = parts.slice(0, i).join("/");
      if (!existsSync(join(worktree, d))) {
        dirs.add(d);
        break;
      }
    }
    mkdirSync(join(worktree, rel, ".."), { recursive: true });
    copyFileSync(join(from, rel), join(worktree, rel));
  }
  return { files, dirs: [...dirs] };
}

function removeHidden(worktree: string, c: { files: string[]; dirs: string[] }) {
  for (const f of c.files) rmSync(join(worktree, f), { force: true });
  for (const d of c.dirs) rmSync(join(worktree, d), { recursive: true, force: true });
}

function runHeadless(path: string, prompt: string, s: Settings, capMs: number, rawOut: string, args: string[] = claudeArgs(prompt, s, true)) {
  return new Promise<{ lines: string[]; timedOut: boolean }>((done) => {
    const child = spawn("claude", args, { cwd: path, stdio: ["ignore", "pipe", "pipe"] });
    let buf = "";
    let timedOut = false;
    child.stdout.on("data", (d) => (buf += d));
    child.stderr.on("data", () => {});
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, capMs);
    const finish = () => {
      clearTimeout(timer);
      writeFileSync(rawOut, buf);
      done({ lines: buf.split("\n").filter(Boolean), timedOut });
    };
    child.on("close", finish);
    child.on("error", (e) => {
      buf += JSON.stringify({ type: "error", message: String(e) }) + "\n";
      finish();
    });
  });
}

function record(ws: string, rec: HalfRecord, answer: string) {
  const dir = proofDir(ws, rec.slug);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${rec.half}.md`), answer);
  writeFileSync(join(dir, `${rec.half}.json`), JSON.stringify(rec, null, 1));
  appendLedger(ws, { slug: rec.slug, half: rec.half, cost_usd: rec.cost_usd, tokens: rec.tokens, seconds: rec.seconds, proof_exit: rec.proof_exit === null ? "" : String(rec.proof_exit), started_at: rec.started_at });
}

/** One row per run: collecting the same run again replaces its row instead of billing it twice. */
export function appendLedger(ws: string, row: LedgerRow) {
  mkdirSync(join(ws, "writeup", "proof"), { recursive: true });
  const ledger = join(ws, "writeup", "proof", "ledger.csv");
  const same = (r: LedgerRow) => r.slug === row.slug && r.half === row.half && r.started_at === row.started_at;
  const rows = existsSync(ledger) ? parseLedger(readFileSync(ledger, "utf8")).filter((r) => !same(r)) : [];
  writeFileSync(ledger, [LEDGER_HEADER, ...[...rows, row].map(ledgerLine)].join("\n") + "\n");
}

function readLedger(ws: string): LedgerRow[] {
  const p = join(ws, "writeup", "proof", "ledger.csv");
  return existsSync(p) ? parseLedger(readFileSync(p, "utf8")) : [];
}
const spentOf = (rows: LedgerRow[]) => rows.reduce((a, r) => a + r.cost_usd, 0);
const usd = (n: number) => `$${n.toFixed(2)}`;

// ------------------------------------------------------------- judge and page

/** Older runs left mapping.json next to the packet, where the judge could read it. */
function moveOldMapping(ws: string, slug: string) {
  const old = join(proofDir(ws, slug), "mapping.json");
  if (!existsSync(old)) return;
  mkdirSync(join(ws, ".harnesskit", "proof", slug), { recursive: true });
  renameSync(old, sealedMapping(ws, slug));
}

const DIFF_LINES = 400;

function writePacket(ws: string, sc: Scenario) {
  const dir = proofDir(ws, sc.slug);
  const read = (half: string) => ({
    answer: existsSync(join(dir, `${half}.md`)) ? readFileSync(join(dir, `${half}.md`), "utf8") : "(no answer recorded)",
    rec: existsSync(join(dir, `${half}.json`)) ? JSON.parse(readFileSync(join(dir, `${half}.json`), "utf8")) : {},
  });
  const mapPath = sealedMapping(ws, sc.slug);
  moveOldMapping(ws, sc.slug);
  let mapping: { A: string; B: string };
  if (existsSync(mapPath)) mapping = JSON.parse(readFileSync(mapPath, "utf8"));
  else {
    const beforeFirst = Math.random() < 0.5;
    mapping = beforeFirst ? { A: "before", B: "after" } : { A: "after", B: "before" };
    mkdirSync(join(ws, ".harnesskit", "proof", sc.slug), { recursive: true });
    writeFileSync(mapPath, JSON.stringify(mapping));
  }
  const a = read(mapping.A);
  const b = read(mapping.B);
  const opened = (r: any) => {
    const f: string[] = r.files_opened ?? [];
    const calls = r.tool_calls === undefined ? "" : `\nTool calls: ${r.tool_calls}${r.tool_calls === 0 ? " (answered from memory)" : ""}`;
    return `Files this run opened: ${f.join(", ") || "none"}\nHarness files opened: ${f.filter(isHarnessFile).join(", ") || "none"}${calls}`;
  };
  const feature = sc.kind === "feature";
  const rec = (r: any) => JSON.stringify({ cost_usd: r.cost_usd, tokens: r.tokens, turns: r.turns, seconds: r.seconds, proof_exit: r.proof_exit, ...(feature ? { hidden_exit: r.hidden_exit ?? null, suite_exit: r.suite_exit ?? null, lint_exit: r.lint_exit ?? null } : {}), timed_out: r.timed_out }, null, 1);
  const result = (code: unknown) => (code === null || code === undefined ? "not configured" : code === 0 ? "passed" : `failed (exit ${code})`);
  const facts = (r: any) => `Hidden tests: ${result(r.hidden_exit)}\nSuite: ${result(r.suite_exit)}\nLint: ${result(r.lint_exit)}`;
  const changes = (half: string, r: any) => {
    const f = join(dir, `${half}.diff`);
    const all = existsSync(f) ? readFileSync(f, "utf8").split("\n") : [];
    const body = all.length > DIFF_LINES ? [...all.slice(0, DIFF_LINES), `... (${all.length - DIFF_LINES} more lines cut; the worktree holds the rest)`] : all;
    return `${facts(r)}\n\nWhat this run changed (git diff --stat):\n\n\`\`\`\n${r.diff_stat || "(nothing changed)"}\n\`\`\`\n\nThe diff, first ${DIFF_LINES} lines:\n\n\`\`\`diff\n${body.join("\n").trim() || "(empty)"}\n\`\`\``;
  };
  writeFileSync(
    join(dir, "judge.md"),
    `# Judge packet: ${sc.title}

Follow docs/harnesskit/proof/judge.md. Save the json it asks for as writeup/proof/${sc.slug}/verdict.json, then run \`harnesskit prove render\`.
The two answers are shuffled as A and B.

## The question

${sc.prompt}
${sc.reply ? `\nIf the agent asked, the person's scripted reply was: ${sc.reply}\n` : ""}
## Things a correct answer is expected to cover (hints for you only)

${sc.mustMention.map((m) => `- ${m}`).join("\n") || "(none given)"}

${sc.proof ? `## The proof command\n\n\`\`\`sh\n${sc.proof}\n\`\`\`\n\nIts exit code per answer is in the run record below (0 passed).\n` : ""}${sc.kind === "feature" ? "\nThis is a feature scenario: the proof ran hidden tests the agent never saw. Cite the hidden-test, suite and lint results under each answer, read the diff, and return the `quality` object for each answer as judge.md describes.\n" : ""}
## Answer A

${a.answer}

${opened(a.rec)}
${feature ? `\n${changes(mapping.A, a.rec)}\n` : ""}
## Answer B

${b.answer}

${opened(b.rec)}
${feature ? `\n${changes(mapping.B, b.rec)}\n` : ""}
## Run record for A

\`\`\`json
${rec(a.rec)}
\`\`\`

## Run record for B

\`\`\`json
${rec(b.rec)}
\`\`\`
`,
  );
}

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

type Verdict = "won" | "lost" | "tie" | "unjudged";
type Card = {
  slug: string; title: string; kind: string; difficulty: string; exercises: string;
  b: any; a: any; verdict: Verdict; why: string; bought: string;
  correct: { b: boolean | null; a: boolean | null }; invalid: boolean; invalidWhy: string;
  quality: { b: Quality | null; a: Quality | null };
};

const RUBRIC = [["correctness", "Correctness"], ["fits_codebase", "Fits codebase"], ["tests", "Tests"], ["scope", "Scope"], ["verification", "Verification"], ["overall", "Overall"]] as const;
type Quality = Record<(typeof RUBRIC)[number][0], number> & { quality_why: string };

function readQuality(q: any): Quality | null {
  if (!q || typeof q !== "object" || !Number.isFinite(Number(q.overall))) return null;
  const out: any = { quality_why: String(q.quality_why ?? "") };
  for (const [k] of RUBRIC) out[k] = Number(q[k]);
  return out;
}
const tenth = (n: number) => n.toFixed(1);

/** "35% fewer"; the words say which way it moved, so a reader never compares two numbers to find out. */
function change(before: number, after: number, up: string, down: string): string {
  if (!(before > 0)) return "";
  const pct = Math.round((Math.abs(after - before) / before) * 100);
  return pct === 0 ? "unchanged" : `${pct}% ${after > before ? up : down}`;
}
/** For tokens, time and cost lower is better: the class says which way the harness moved it. */
const direction = (before: number, after: number): "better" | "worse" | "same" => {
  const pct = before > 0 ? Math.round((Math.abs(after - before) / before) * 100) : 0;
  return pct === 0 ? "same" : after < before ? "better" : "worse";
};
const money = (n: number) => `$${n.toFixed(2)}`;
const kilo = (n: number) => (n >= 10000 ? `${Math.round(n / 1000)}k` : String(n));

function proofWords(b: any, a: any): string {
  const bp = b?.proof_exit, ap = a?.proof_exit;
  if (bp == null && ap == null) return "a question, no proof command";
  if (bp == null || ap == null) return "proof ran in one half only";
  if (bp === 0 && ap === 0) return "proof passed in both";
  if (bp !== 0 && ap === 0) return "proof failed before, passed after";
  if (bp === 0) return "proof passed before, failed after";
  return "proof failed in both";
}

const HARNESS_PATH = /^(docs\/|CLAUDE\.md$|AGENTS\.md$|\.claude\/|scripts\/)/;
const clean = (f: string) => f.replace(/^\.\//, "");

/** Kind and difficulty live in scenario.json for new runs; older runs read them from the scenario file. */
function scenarioMeta(ws: string, slug: string, sc: any): { kind: string; difficulty: string } {
  let kind = sc.kind as string | undefined, difficulty = sc.difficulty as string | undefined;
  const file = join(ws, "writeup", "scenarios", `${slug}.md`);
  if ((!kind || !difficulty) && existsSync(file)) {
    const text = readFileSync(file, "utf8");
    kind ||= /^Kind:\s*(\S+)/im.exec(text)?.[1];
    difficulty ||= /^Difficulty:\s*(\S+)/im.exec(text)?.[1];
  }
  return { kind: kind ?? "", difficulty: difficulty ?? "" };
}

const PILL: Record<Verdict, [string, string]> = { won: ["harness", "Harness"], lost: ["baseline", "Baseline"], tie: ["tie", "Tie"], unjudged: ["none", "Not judged"] };
const short = (sha: unknown) => String(sha ?? "").slice(0, 7);

function analysisSection(ws: string): string {
  const list = readAnalyses(ws);
  if (!list.length) return "";
  const rows = crossRows(list).map((r) => `<tr>${r.cells.map((c, i) => (i === 0 ? `<th scope="row">${esc(c)}</th>` : `<td>${esc(c)}</td>`)).join("")}</tr>`).join("\n");
  return `<section id="analysis"><h2>Analysis</h2>
<div class="card wrap"><table class="cmp"><caption class="sr">Where each run's time goes, baseline to harness</caption>
<thead><tr>${TABLE_HEAD.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
<tbody>
${rows}
</tbody></table></div>
<p class="mute" style="font-size:.85rem">Baseline to harness. Every step of every run is in <a href="analysis.md">analysis.md</a>.</p></section>`;
}

export function renderPage(ws: string): string {
  const analysisHtml = analysisSection(ws);
  const root = join(ws, "writeup", "proof");
  const cards: Card[] = [];
  if (existsSync(root)) {
    for (const slug of readdirSync(root).sort()) {
      const dir = join(root, slug);
      if (!statSync(dir).isDirectory() || !existsSync(join(dir, "scenario.json"))) continue;
      const sc = JSON.parse(readFileSync(join(dir, "scenario.json"), "utf8"));
      const load = (h: string) => (existsSync(join(dir, `${h}.json`)) ? JSON.parse(readFileSync(join(dir, `${h}.json`), "utf8")) : null);
      const b = load("before"), a = load("after");
      let verdict: Verdict = "unjudged";
      let why = "";
      let bought = "";
      let flagged = false;
      const correct: Card["correct"] = { b: null, a: null };
      const quality: Card["quality"] = { b: null, a: null };
      moveOldMapping(ws, slug);
      if (existsSync(join(dir, "verdict.json")) && existsSync(sealedMapping(ws, slug))) {
        const v = JSON.parse(readFileSync(join(dir, "verdict.json"), "utf8"));
        const m = JSON.parse(readFileSync(sealedMapping(ws, slug), "utf8"));
        const unblind = (s: string) => s.replace(/\bAnswer A\b/g, m.A === "after" ? "the harness run" : "the baseline run").replace(/\bAnswer B\b/g, m.B === "after" ? "the harness run" : "the baseline run");
        why = unblind(String(v.why ?? v.preferred_why ?? ""));
        if (v.preferred_why && v.why) why = unblind(String(v.preferred_why)) + " " + why;
        flagged = v.proof_invalid === true;
        verdict = v.preferred === "tie" ? "tie" : m[v.preferred] === "after" ? "won" : m[v.preferred] === "before" ? "lost" : "unjudged";
        for (const L of ["A", "B"]) {
          quality[m[L] === "after" ? "a" : "b"] = readQuality(v[L]?.quality);
          const ach = v[L]?.achieved;
          if (typeof ach !== "string") continue;
          correct[m[L] === "after" ? "a" : "b"] = ach === "yes";
          if (m[L] === "after") bought = String(v[L]?.process?.what_the_harness_bought ?? "");
        }
      }
      // A proof that failed in both halves says nothing about the agents when the scenario never gave them what it checks.
      const failedBoth = b?.proof_exit != null && a?.proof_exit != null && b.proof_exit !== 0 && a.proof_exit !== 0;
      const neverGiven = Boolean(sc.reply) && !b?.replied && !a?.replied;
      const bothAchieved = correct.b === true && correct.a === true;
      const invalid = failedBoth && (flagged || neverGiven || bothAchieved);
      const invalidWhy = flagged ? "the judge marked the proof invalid" : neverGiven ? "the proof needs a detail only the scripted reply gives, and neither run asked for it" : "both answers were judged correct, so the proof is what failed";
      cards.push({ slug, title: sc.title, ...scenarioMeta(ws, slug, sc), exercises: sc.exercises ?? "", b, a, verdict, why, bought, correct, invalid, invalidWhy, quality });
    }
  }

  const judged = cards.filter((r) => r.verdict !== "unjudged" && r.a && r.b);
  const won = judged.filter((r) => r.verdict === "won").length;
  const lost = judged.filter((r) => r.verdict === "lost").length;
  const tied = judged.filter((r) => r.verdict === "tie").length;
  const sum = (rs: Card[], h: "a" | "b", k: string) => rs.reduce((s, r) => s + (r[h]?.[k] ?? 0), 0);
  const graded = judged.filter((r) => !r.invalid && r.correct.a !== null && r.correct.b !== null);
  const invalidCount = cards.filter((r) => r.invalid).length;
  const [cb, ca] = [graded.filter((r) => r.correct.b).length, graded.filter((r) => r.correct.a).length];
  // Feature scenarios lead with what hidden tests and the quality score say; cost and time come after.
  const features = cards.filter((r) => r.kind === "feature");
  const hasFeature = features.length > 0;
  const hiddenM = features.filter((r) => r.a?.hidden_exit != null && r.b?.hidden_exit != null).length;
  const hiddenPassed = (h: "a" | "b") => features.filter((r) => r.a?.hidden_exit != null && r.b?.hidden_exit != null && r[h].hidden_exit === 0).length;
  const scored = features.filter((r) => r.quality.a && r.quality.b);
  const avgQ = (h: "a" | "b") => scored.reduce((s, r) => s + r.quality[h]!.overall, 0) / scored.length;
  const regressions = (h: "a" | "b") => features.filter((r) => r[h]?.suite_exit != null && r[h].suite_exit !== 0).length;
  const featureLead = hasFeature
    ? `Hidden tests passed: baseline ${hiddenPassed("b")} of ${hiddenM}, harness ${hiddenPassed("a")} of ${hiddenM}; ` +
      (scored.length ? `average quality ${tenth(avgQ("b"))} against ${tenth(avgQ("a"))} of 10; ` : "average quality not judged yet; ") +
      `regressions ${regressions("b")} against ${regressions("a")}. `
    : "";
  let headline: string;
  if (judged.length) {
    const bc = sum(judged, "b", "cost_usd"), ac = sum(judged, "a", "cost_usd");
    const cc = change(bc, ac, "more", "less");
    const cost = cc === "unchanged" || !cc ? "cost the same" : `cost ${cc}`;
    const helped = won === 0 ? "The harness helped in none of" : `The harness helped in ${won} of`;
    headline = `${helped} ${judged.length} scenarios and ${cost}`;
    if (tied) headline += `; ${tied} tied`;
    if (graded.length) headline += cb === ca ? `; correctness was the same (${ca} of ${graded.length} correct either way).` : ca > cb ? `; the harness was correct more often (${ca} against ${cb} of ${graded.length}).` : `; the harness was correct less often (${ca} against ${cb} of ${graded.length}).`;
    else headline += ".";
  } else {
    headline = "Not judged yet: " + (cards.length ? "run the judge packets, then harnesskit prove render." : "no runs recorded.");
  }
  headline = featureLead + headline;

  const ledger = readLedger(ws);
  const judgeCost = ledger.filter((r) => r.half === "judge").reduce((s, r) => s + r.cost_usd, 0);
  const profilePath = join(ws, "docs", "harnesskit", "profile.md");
  const profile = existsSync(profilePath) ? parseProfile(readFileSync(profilePath, "utf8")) : {};
  const budget = Number(profile.budget_usd);
  const shas = (h: "a" | "b") => [...new Set(cards.map((r) => short(r[h]?.commit)).filter(Boolean))].join(", ");
  const trust = [
    `${profile.model ?? "sonnet"}, ${profile.effort ?? "medium"} effort`,
    "1 run per half",
    "judged blind",
    shas("b") || shas("a") ? `compared ${shas("b") || "?"} (before) with ${shas("a") || "?"} (after)` : "",
    judgeCost > 0 ? `judging cost ${money(judgeCost)}` : "",
    Number.isFinite(budget) ? `${money(budget - spentOf(ledger))} of ${money(budget)} budget left` : "",
    invalidCount ? `${invalidCount} proof${invalidCount > 1 ? "s" : ""} invalid` : "",
  ].filter(Boolean);
  const stamp = new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC";

  // One table names each scenario once; everything else refers to it by number.
  const numOf = new Map(cards.map((r, i) => [r.slug, i + 1]));
  const ref = (r: Card) => `scenario ${numOf.get(r.slug)}`;

  const num = (b: any, a: any, get: (x: any) => number, fmt: (n: number) => string, up: string, down: string) =>
    b && a
      ? `<td class="n">${fmt(get(b))} &rarr; ${fmt(get(a))}<br><span class="delta ${direction(get(b), get(a))}">${change(get(b), get(a), up, down)}</span></td>`
      : `<td class="n">&ndash;</td>`;
  const mark = (v: boolean | null, invalid: boolean) =>
    invalid ? `<span class="na" aria-hidden="true">&ndash;</span><span class="sr">not counted</span>`
    : v === null ? `<span class="na" aria-hidden="true">&ndash;</span><span class="sr">not judged</span>`
    : v ? `<span class="yes" aria-hidden="true">&#10003;</span><span class="sr">correct</span>`
    : `<span class="no" aria-hidden="true">&#10007;</span><span class="sr">not correct</span>`;

  const filesOf = (rec: any, who: string) => {
    if (!rec?.files_opened) return `<p><b>${who}:</b> not recorded</p>`;
    const fs = (rec.files_opened as string[]).map(clean);
    const list = fs.length ? fs.map((f) => (HARNESS_PATH.test(f) ? `<code class="hf" title="harness file">${esc(f)}</code>` : `<code>${esc(f)}</code>`)).join(" ") : "none";
    return `<p><b>${who}:</b> ${list}</p>`;
  };
  const qDirection = (b: number, a: number) => (a > b ? "better" : a < b ? "worse" : "same");
  const qualityCell = (b: Quality | null, a: Quality | null) =>
    b && a ? `<td class="n">${b.overall} &rarr; ${a.overall}<br><span class="delta ${qDirection(b.overall, a.overall)}">${a.overall > b.overall ? "better" : a.overall < b.overall ? "worse" : "unchanged"}</span></td>` : `<td class="n">&ndash;</td>`;
  const hiddenMark = (x: any) => (x?.hidden_exit == null ? `<span class="na" aria-hidden="true">&ndash;</span><span class="sr">no hidden tests</span>` : x.hidden_exit === 0 ? `<span class="yes" aria-hidden="true">&#10003;</span><span class="sr">hidden tests passed</span>` : `<span class="no" aria-hidden="true">&#10007;</span><span class="sr">hidden tests failed</span>`);
  const hiddenCell = (r: Card) => (r.kind === "feature" ? `<td class="mk"><span class="lbl">Baseline </span>${hiddenMark(r.b)} <span class="lbl">Harness </span>${hiddenMark(r.a)}</td>` : `<td class="mk"><span class="na">&ndash;</span></td>`);
  const cols = hasFeature ? 8 : 6;
  const rubric = (r: Card) => {
    if (!r.quality.b && !r.quality.a) return "";
    const cell = (q: Quality | null, k: string) => (q ? String((q as any)[k]) : "&ndash;");
    const rows = RUBRIC.map(([k, name]) => `<tr><th scope="row">${name}</th><td>${cell(r.quality.b, k)}</td><td>${cell(r.quality.a, k)}</td></tr>`).join("");
    const why = [r.quality.b?.quality_why ? `<p><b>Baseline:</b> ${esc(r.quality.b.quality_why)}</p>` : "", r.quality.a?.quality_why ? `<p><b>Harness:</b> ${esc(r.quality.a.quality_why)}</p>` : ""].join("");
    return `<table class="rubric"><caption class="sr">Quality scores out of 10, baseline and harness</caption><thead><tr><th>Quality</th><th>Baseline</th><th>Harness</th></tr></thead><tbody>${rows}</tbody></table>${why}`;
  };
  const checks = (r: Card) => {
    if (r.kind !== "feature") return "";
    const w = (x: any, name: string) => (x == null ? `${name}: not configured` : `${name}: ${x === 0 ? "passed" : "failed"}`);
    const line = (who: string, x: any) => `<p><b>${who}:</b> ${[w(x?.hidden_exit, "hidden tests"), w(x?.suite_exit, "suite"), w(x?.lint_exit, "lint")].join(" &middot; ")}</p>`;
    return line("Baseline", r.b) + line("Harness", r.a);
  };
  const detail = (r: Card) => {
    const proof = r.invalid
      ? `<p><b>Proof:</b> <span class="invalid">Proof invalid &mdash; not counted</span>. It failed in both halves, and ${esc(r.invalidWhy)}. Fix the scenario, not the agent.</p>`
      : `<p><b>Proof:</b> ${esc(proofWords(r.b, r.a))}.</p>`;
    const memory = [r.b?.tool_calls === 0 ? "the baseline" : "", r.a?.tool_calls === 0 ? "the harness run" : ""].filter(Boolean);
    const harnessOpened = ((r.a?.files_opened ?? []) as string[]).map(clean).filter((f) => HARNESS_PATH.test(f));
    const note = r.a?.files_opened && !harnessOpened.length ? `<p class="mute">The harness run opened no harness file. Whatever it gained came from what it read at start (CLAUDE.md), which the record does not show.</p>` : "";
    return `<tr class="detail" id="d-${esc(r.slug)}"><td colspan="${cols}"><div class="dbody">
${r.exercises ? `<p><b>What it exercises:</b> ${esc(r.exercises)}</p>` : ""}
${r.why ? `<p><b>Why the judge chose this:</b> ${esc(r.why)}</p>` : `<p class="mute">Not judged yet.</p>`}
${proof}
${checks(r)}${rubric(r)}
${r.bought ? `<p><b>What the harness bought:</b> ${esc(r.bought)}</p>` : ""}
${memory.length ? `<p><b>Answered from memory:</b> ${memory.join(" and ")} opened nothing and ran nothing.</p>` : ""}
<div class="files">${filesOf(r.b, "Baseline opened")}${filesOf(r.a, "Harness run opened")}${note}</div>
<p class="links"><a href="${esc(r.slug)}/before.md">Baseline answer</a> &middot; <a href="${esc(r.slug)}/after.md">Harness answer</a> &middot; <a href="${esc(r.slug)}/verdict.json">Judge verdict</a></p>
</div></td></tr>`;
  };
  const cmpRows = cards.map((r) => {
    const [cls, word] = PILL[r.verdict];
    return `<tr class="row" data-slug="${esc(r.slug)}"><th scope="row"><button type="button" class="tog" aria-expanded="true" aria-controls="d-${esc(r.slug)}"><span class="chev" aria-hidden="true"></span><span class="num">${numOf.get(r.slug)}</span> ${esc(r.title)}</button><span class="meta">${esc([r.kind, r.difficulty].filter(Boolean).join(" · "))}</span></th>
<td><span class="pill ${cls}">${word}</span></td>
<td class="mk"><span class="lbl">Baseline </span>${mark(r.correct.b, r.invalid)} <span class="lbl">Harness </span>${mark(r.correct.a, r.invalid)}</td>
${hasFeature ? qualityCell(r.quality.b, r.quality.a) + "\n" + hiddenCell(r) + "\n" : ""}${num(r.b, r.a, (x) => x.tokens, kilo, "more", "fewer")}
${num(r.b, r.a, (x) => x.seconds, (n) => `${n}s`, "more time", "less time")}
${num(r.b, r.a, (x) => x.cost_usd, money, "more", "less")}</tr>
${detail(r)}`;
  }).join("\n");
  const tot = (get: (rs: Card[], h: "a" | "b") => number, fmt: (n: number) => string, up: string, down: string) =>
    judged.length ? `<td class="n">${fmt(get(judged, "b"))} &rarr; ${fmt(get(judged, "a"))}<br><span class="delta ${direction(get(judged, "b"), get(judged, "a"))}">${change(get(judged, "b"), get(judged, "a"), up, down)}</span></td>` : `<td class="n">&ndash;</td>`;
  const totals = `<tr class="total"><th scope="row">Total, ${judged.length} judged</th>
<td>${won} harness &middot; ${lost} baseline${tied ? ` &middot; ${tied} tie` : ""}</td>
<td class="mk">${graded.length ? `Baseline ${cb} of ${graded.length}<br>Harness ${ca} of ${graded.length}` : "&ndash;"}${invalidCount ? `<br><span class="mute">${invalidCount} invalid, not counted</span>` : ""}</td>
${hasFeature ? (scored.length ? `<td class="n">${tenth(avgQ("b"))} &rarr; ${tenth(avgQ("a"))}<br><span class="mute">average of 10</span></td>` : `<td class="n">&ndash;</td>`) + `\n<td class="mk">${hiddenM ? `Baseline ${hiddenPassed("b")} of ${hiddenM}<br>Harness ${hiddenPassed("a")} of ${hiddenM}` : "&ndash;"}<br><span class="mute">regressions ${regressions("b")} &rarr; ${regressions("a")}</span></td>\n` : ""}${tot((rs, h) => sum(rs, h, "tokens"), kilo, "more", "fewer")}
${tot((rs, h) => sum(rs, h, "seconds"), (n) => `${n}s`, "more time", "less time")}
${tot((rs, h) => sum(rs, h, "cost_usd"), money, "more", "less")}</tr>`;

  const next: string[] = [];
  for (const r of cards.filter((c) => c.invalid)) next.push(`Fix the proof for ${ref(r)}: ${esc(r.invalidWhy)}.`);
  for (const r of cards.filter((c) => c.a?.files_opened && !(c.a.files_opened as string[]).some((f) => HARNESS_PATH.test(clean(f))))) next.push(`In ${ref(r)} the harness run opened no harness file. Check the docs point at what this scenario needs.`);
  for (const r of cards.filter((c) => c.verdict === "lost")) next.push(`In ${ref(r)} the baseline won. Read the judge's reason and find what the harness lacked or added.`);
  const nextHtml = next.length ? `<ul class="next">${next.map((n) => `<li>${n}</li>`).join("")}</ul>` : `<p class="mute">Nothing to fix from this run.</p>`;

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Harness proof</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--line:#dcdfe4;--ink:#1c1f23;--ink-2:#3a434d;--ink-3:#5b6570;--accent:#2f6feb;--accent-soft:#eef3ff;
--ok:#1d6b3a;--ok-soft:#e8f6ed;--warn:#8a5a09;--warn-soft:#fdf3e2;--flat:#324a86;--flat-soft:#eef1f8;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink-2);font:16px/1.5 var(--sans);padding:0 16px 4rem;-webkit-font-smoothing:antialiased}
main{max-width:960px;margin:0 auto}
header{padding:2rem 0 .5rem}
.kicker,h2{font:600 .75rem/1.2 var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);margin:0}
h2{display:flex;align-items:center;gap:.5rem;margin:0 0 .6rem}
h2::before{content:"";width:1.1rem;height:2px;background:var(--accent)}
h1{font-size:2rem;line-height:1.2;font-weight:750;color:var(--ink);margin:.4rem 0 .6rem;letter-spacing:-.01em;overflow-wrap:anywhere}
.verdict{font-size:1.15rem;color:var(--ink);margin:0 0 .6rem;max-width:62ch}
.trust{font-size:.85rem;color:var(--ink-3);margin:0;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:.1rem 1rem}
section{padding-top:1.75rem}
.num{display:inline-block;min-width:1.4em;color:var(--ink-3,#6b7280);font-variant-numeric:tabular-nums}
.meta{display:block;margin:.15rem 0 0 2.6em;font-size:.8rem;font-weight:400;color:var(--ink-3,#6b7280)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.25rem .75rem .5rem}
.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.92rem;font-variant-numeric:tabular-nums}
.cmp{min-width:680px}.scn{min-width:560px}
th,td{text-align:left;padding:.55rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
thead th{font:600 .7rem/1.2 var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)}
tbody th{font-weight:600;color:var(--ink)}
td.n{white-space:nowrap}.n{font-variant-numeric:tabular-nums}
.clip{display:block;max-width:34ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.clip:hover,.clip:focus{white-space:normal}
.pill{display:inline-block;border-radius:99px;padding:.05rem .6rem;font-size:.8rem;font-weight:600;white-space:nowrap}
.pill.harness{color:var(--ok);background:var(--ok-soft)}
.pill.baseline{color:var(--flat);background:var(--flat-soft)}
.pill.tie,.pill.none{color:var(--ink-3);background:#f0f0ee}
.delta{font-size:.8rem;font-weight:600}
.delta.better{color:var(--ok)}.delta.worse{color:var(--warn)}.delta.same{color:var(--ink-3)}
.yes{color:var(--ok);font-weight:700}.no{color:var(--warn);font-weight:700}.na{color:var(--ink-3)}
.lbl{font-size:.75rem;color:var(--ink-3)}
.mk{white-space:nowrap}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
tr.total th,tr.total td{border-bottom:none;border-top:2px solid var(--line);background:#fafaf8;font-weight:600;color:var(--ink)}
tr.total .mute{font-weight:400}
.tog{font:inherit;font-weight:600;color:var(--ink);background:none;border:0;padding:0;cursor:pointer;text-align:left;display:flex;gap:.5rem;align-items:baseline}
.chev{display:inline-block;width:.5rem;height:.5rem;border-right:2px solid var(--ink-3);border-bottom:2px solid var(--ink-3);transform:rotate(-45deg);flex:none;transition:transform .15s}
.tog[aria-expanded="true"] .chev{transform:rotate(45deg)}
.tog:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
tr.detail td{background:#fafaf8;padding:0}
.js tr.detail.closed{display:none}
.dbody{padding:.6rem .9rem .9rem;max-width:min(72ch,calc(100vw - 4rem));position:sticky;left:.75rem;font-size:.92rem}
.dbody p{margin:.4rem 0}.dbody b{color:var(--ink)}
.invalid{color:var(--warn);font-weight:700}
.mute{color:var(--ink-3)}
table.rubric{width:auto;min-width:280px;margin:.4rem 0;font-size:.85rem}table.rubric th,table.rubric td{padding:.2rem .8rem .2rem 0;border-bottom:1px solid var(--line)}
code{font-family:var(--mono);font-size:.8rem;margin-right:.4rem;display:inline-block;overflow-wrap:anywhere}
code.hf{background:var(--accent-soft);color:var(--ink);padding:0 .3rem}
a{color:var(--accent)}
ul.next{margin:.2rem 0 0;padding-left:1.2rem}ul.next li{margin:.3rem 0}
.stamp{font-size:.85rem;color:var(--ink-3);margin-top:1.5rem}
@media (max-width:640px){h1{font-size:1.5rem}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><main>
<header>
<p class="kicker">Harness proof</p>
<h1>${esc(basename(resolve(ws)))}</h1>
<p class="verdict">${esc(headline)}</p>
<ul class="trust">${trust.map((t) => `<li>${esc(t)}</li>`).join("")}<li>rendered ${stamp}</li></ul>
</header>
<section id="scenarios"><h2>Scenarios</h2>
<div class="card wrap"><table class="cmp"><caption class="sr">Harness against baseline, one row per scenario; each row opens its detail</caption>
<thead><tr><th>Scenario</th><th>Winner</th><th>Correct</th>${hasFeature ? "<th>Quality</th><th>Hidden tests</th>" : ""}<th>Tokens</th><th>Time</th><th>Cost</th></tr></thead>
<tbody>
${cmpRows}
${totals}
</tbody></table></div>
<p class="mute" style="font-size:.85rem">Change is the harness run against the baseline. Green is better (lower), amber is worse (higher).</p></section>
${analysisHtml}
<section id="next"><h2>Next</h2>
${nextHtml}
</section>
<p class="stamp">Rendered ${stamp} from writeup/proof by harnesskit prove render.</p>
</main>
<script>
document.documentElement.classList.add("js");
document.querySelectorAll(".tog").forEach(function(b){
  var d=document.getElementById(b.getAttribute("aria-controls"));
  d.classList.add("closed");b.setAttribute("aria-expanded","false");
  b.addEventListener("click",function(){var c=d.classList.toggle("closed");b.setAttribute("aria-expanded",String(!c));});
});
</script>
</body></html>
`;
}

function writePage(ws: string) {
  mkdirSync(join(ws, "writeup", "proof"), { recursive: true });
  writeFileSync(join(ws, "writeup", "proof", "index.html"), renderPage(ws));
}

// ----------------------------------------------------------------------- main

function flag(args: string[], name: string): string | undefined {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}

function settings(ws: string, args: string[]) {
  const pf = join(ws, "docs", "harnesskit", "profile.md");
  const profile = existsSync(pf) ? parseProfile(readFileSync(pf, "utf8")) : {};
  const herdrOnPath = spawnSync("herdr", ["--version"], { stdio: "ignore" }).status === 0;
  const runner = flag(args, "--runner") ?? profile.runner ?? (herdrOnPath ? "herdr" : "headless");
  const budgetRaw = flag(args, "--budget") ?? profile.budget_usd;
  return {
    runner,
    budget: budgetRaw === undefined ? null : Number(budgetRaw),
    model: profile.model ?? "sonnet",
    effort: profile.effort ?? "medium",
  };
}

const shq = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

export async function runProve(argv: string[], ctx: Ctx): Promise<number> {
  const { log } = ctx;
  const ws = resolve(ctx.cwd);
  try {
    if (argv[0] === "render") {
      writePage(ws);
      log("wrote writeup/proof/index.html");
      return 0;
    }
    if (argv[0] === "analyze") return analyze(ws, argv[1], log);
    if (argv[0] === "judge") return await judge(ws, argv, log);
    if (argv[0] === "--clean") {
      const slug = argv[1];
      if (!slug) throw new Error("--clean needs a slug");
      for (const half of ["before", "after"]) {
        const p = worktreePath(ws, slug, half);
        removeNested(ws, p, nestedRepos(ws));
        if (existsSync(p)) git(ws, "worktree", "remove", "--force", p);
        rmSync(p, { recursive: true, force: true });
      }
      git(ws, "worktree", "prune");
      log(`removed worktrees for ${slug}`);
      return 0;
    }
    if (argv[0] === "reply") return reply(ws, argv[1], log);
    if (argv[0] === "collect") return collect(ws, argv[1], argv, log);
    return await launch(ws, argv, log);
  } catch (e) {
    log(`prove: ${(e as Error).message}`);
    return 1;
  }
}

const TRUST_PROMPT = /trust this folder/i;

/** True when Claude Code has a recorded trust for this folder (read only). */
export function trustedByClaude(dir: string, configPath = join(process.env.CLAUDE_CONFIG_DIR ?? homedir(), ".claude.json")): boolean {
  try {
    const projects = JSON.parse(readFileSync(configPath, "utf8")).projects ?? {};
    return projects[realpathSync(dir)]?.hasTrustDialogAccepted === true;
  } catch {
    return false;
  }
}

/**
 * A worktree is a new folder, so its pane opens on Claude Code's trust prompt.
 * Accept it only when the person already trusted the workspace it was cut from;
 * otherwise say which pane waits, so the run never hangs unseen.
 */
async function passTrustPrompt(pane: string, ws: string, log: Log): Promise<void> {
  for (let i = 0; i < 30; i++) {
    const screen = spawnSync("herdr", ["pane", "read", pane], { encoding: "utf8" }).stdout ?? "";
    if (TRUST_PROMPT.test(screen)) {
      if (!trustedByClaude(ws)) {
        log(`pane ${pane} waits on Claude Code's folder-trust prompt; approve it there`);
        return;
      }
      spawnSync("herdr", ["pane", "send-keys", pane, "Down", "Enter"]);
      log(`pane ${pane}: accepted the trust prompt (the workspace is already trusted)`);
      return;
    }
    if (/esc to interrupt/i.test(screen)) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function launch(ws: string, args: string[], log: Log): Promise<number> {
  const file = args.find((a, i) => !a.startsWith("--") && !["--runner", "--budget", "--cap-min", "--baseline"].includes(args[i - 1] ?? ""));
  if (!file) throw new Error("usage: harnesskit prove <scenario.md> [--runner herdr|headless] [--budget <usd>] [--dry-run]");
  const sc = parseScenario(readFileSync(resolve(ws, file), "utf8"), file);
  const st = settings(ws, args);
  if (st.budget === null || Number.isNaN(st.budget)) throw new Error("no budget: set budget_usd in docs/harnesskit/profile.md or pass --budget <usd>");
  if (st.runner !== "herdr" && st.runner !== "headless") throw new Error(`runner must be herdr or headless, not ${st.runner}`);
  const baseline = flag(args, "--baseline") ?? readBaseline(ws);
  if (!baseline) throw new Error("no baseline_commit in the manifest: a baseline needs a commit before init (commit first, then re-run harnesskit init), or pass --baseline <sha>");
  const head = git(ws, "rev-parse", "HEAD");
  if (!head.ok) throw new Error("this workspace has no commits");
  const capMin = Number(flag(args, "--cap-min") ?? (sc.kind === "feature" ? 30 : 15));
  if (sc.hiddenDir && !existsSync(resolve(ws, sc.hiddenDir))) throw new Error(`hidden tests folder not found: ${sc.hiddenDir}`);

  const rows = readLedger(ws);
  const budget = budgetCheck(spentOf(rows), estimateRun(rows), 2, st.budget);
  const dry = args.includes("--dry-run");
  const nested = nestedRepos(ws);
  const recorded = readManifest(ws)?.nestedBaselines ?? new Map<string, string>();
  const nestedStart = { before: {} as Record<string, string>, after: {} as Record<string, string> };
  for (const rel of nested) {
    nestedStart.after[rel] = headOf(join(ws, rel))!;
    const rec = recorded.get(rel);
    if (!rec) log(`warning: no nested_baseline for ${rel} in the manifest; its before half starts at the current HEAD, so commits made there since init leak into it (re-run harnesskit init to record one)`);
    nestedStart.before[rel] = rec ?? nestedStart.after[rel];
  }
  const paths = { before: worktreePath(ws, sc.slug, "before"), after: worktreePath(ws, sc.slug, "after") };

  if (dry) {
    log(`plan for "${sc.title}" (${sc.slug}), runner ${st.runner}, model ${st.model}, effort ${st.effort}`);
    log(`  before: ${paths.before} at ${baseline}`);
    log(`  after:  ${paths.after} at ${head.out.slice(0, 12)}`);
    for (const rel of nested) log(`  nested repo ${rel}: before at ${nestedStart.before[rel].slice(0, 12)}, after at ${nestedStart.after[rel].slice(0, 12)}`);
    log(`  command: claude ${claudeArgs("<prompt>", st, st.runner === "headless").join(" ")}`);
    log(`  reset: ${sc.reset ?? "none"}; proof: ${sc.proof ?? "none"}; cap ${capMin} min`);
    if (sc.kind === "feature") log(`  hidden tests: ${sc.hiddenDir ?? "none"}; suite: ${sc.suite ?? "none"}; lint: ${sc.lint ?? "none"}`);
    log(`  estimate ${usd(budget.need)} for two runs; remaining budget ${usd(budget.remaining)} of ${usd(st.budget)}${budget.ok ? "" : " (would refuse)"}`);
    return 0;
  }
  if (!budget.ok) throw new Error(`budget: two runs estimate ${usd(budget.need)} but ${usd(budget.remaining)} remains of ${usd(st.budget)}; raise --budget or run fewer scenarios`);
  const dirty = dirtyPaths(ws, nested);
  if (dirty.length) throw new Error(`uncommitted changes would be missing from the after run (${dirty.slice(0, 5).join(", ")}); commit them first`);

  const halves = { before: ensureWorktree(ws, sc.slug, "before", baseline, nestedStart.before), after: ensureWorktree(ws, sc.slug, "after", head.out, nestedStart.after) };
  const dir = proofDir(ws, sc.slug);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "scenario.json"), JSON.stringify({ title: sc.title, tier: sc.tier, kind: sc.kind, difficulty: sc.difficulty, exercises: sc.exercises, prompt: sc.prompt, mustMention: sc.mustMention, proof: sc.proof, reply: sc.reply, hiddenDir: sc.hiddenDir, suite: sc.suite, lint: sc.lint }, null, 1));
  const commits = { before: git(halves.before, "rev-parse", "HEAD").out, after: git(halves.after, "rev-parse", "HEAD").out };
  const resetExit = (h: "before" | "after") => (sc.reset ? sh(sc.reset, halves[h]) : null);

  if (st.runner === "herdr") {
    const panes: Record<string, unknown> = {};
    for (const h of ["before", "after"] as const) {
      const reset_exit = resetExit(h);
      const r = spawnSync("herdr", ["tab", "create", "--cwd", halves[h], "--label", `${sc.slug}-${h}`, "--no-focus"], { encoding: "utf8" });
      const pane = /"pane_id"\s*:\s*"([^"]+)"/.exec(r.stdout ?? "")?.[1];
      if (r.status !== 0 || !pane) throw new Error(`herdr tab create failed: ${(r.stderr || r.stdout || "").trim()}`);
      const cmd = ["claude", ...claudeArgs(sc.prompt, st, false).map(shq)].join(" ");
      const run = spawnSync("herdr", ["pane", "run", pane, cmd], { encoding: "utf8" });
      if (run.status !== 0) throw new Error(`herdr pane run failed: ${(run.stderr || "").trim()}`);
      await passTrustPrompt(pane, ws, log);
      panes[h] = { pane, worktree: halves[h], commit: commits[h], nested: nestedStart[h], reset_exit, started_at: new Date().toISOString() };
      log(`${h}: pane ${pane} in ${halves[h]}`);
    }
    writeFileSync(join(dir, "panes.json"), JSON.stringify({ model: st.model, reply: sc.reply, panes }, null, 1));
    if (sc.reply) log(`if a pane waits on a question: harnesskit prove reply ${sc.slug}`);
    log(`when both panes are done: harnesskit prove collect ${sc.slug}`);
    return 0;
  }

  const runHalf = async (h: "before" | "after") => {
    const started = new Date().toISOString();
    const reset_exit = resetExit(h);
    const first = await runHeadless(halves[h], sc.prompt, st, capMin * 60_000, join(dir, `${h}.jsonl`));
    let sum = summarizeStream(first.lines, st.model);
    let timedOut = first.timedOut;
    let replied = false;
    const sessionId = sessionIdOf(first.lines);
    if (sc.reply && sessionId && !timedOut && asksPerson(sum.text)) {
      replied = true;
      const second = await runHeadless(halves[h], sc.reply, st, capMin * 60_000, join(dir, `${h}.reply.jsonl`), resumeArgs(sessionId, sc.reply, st));
      const more = summarizeStream(second.lines, st.model);
      timedOut = second.timedOut;
      sum = {
        cost: sum.cost + more.cost,
        tokens: sum.tokens + more.tokens,
        turns: sum.turns + more.turns,
        seconds: sum.seconds + more.seconds,
        text: `${sum.text}\n\n(The scenario's scripted reply was sent: ${sc.reply})\n\n${more.text}`,
        fromResult: sum.fromResult && more.fromResult,
        opened: [...sum.opened, ...more.opened.filter((f) => !sum.opened.includes(f))],
        toolCalls: sum.toolCalls + more.toolCalls,
      };
    }
    const g = gradeHalf(ws, sc, halves[h], commits[h], dir, h, nestedStart[h]);
    const { proof_exit } = g;
    const rec: HalfRecord = { slug: sc.slug, half: h, commit: commits[h], cost_usd: sum.cost, tokens: sum.tokens, turns: sum.turns, seconds: sum.seconds, ...g, reset_exit, timed_out: timedOut, started_at: started, runner: "headless", files_opened: sum.opened, tool_calls: sum.toolCalls, ...(replied ? { replied } : {}) };
    record(ws, rec, sum.text);
    return rec;
  };
  const [b, a] = await Promise.all([runHalf("before"), runHalf("after")]);
  for (const r of [b, a]) log(`${r.half}: ${usd(r.cost_usd)}, ${r.tokens} tokens, ${r.seconds}s, proof exit ${r.proof_exit ?? "none"}${r.timed_out ? ", TIMED OUT" : ""}`);
  finish(ws, sc, st.budget, log);
  return 0;
}

async function judge(ws: string, args: string[], log: Log): Promise<number> {
  const slug = args[1];
  if (!slug || slug.startsWith("--")) throw new Error("usage: harnesskit prove judge <slug> [--again] [--budget <usd>]");
  const dir = proofDir(ws, slug);
  const packet = join(dir, "judge.md");
  const verdict = join(dir, "verdict.json");
  if (!existsSync(packet)) throw new Error(`no judge packet for ${slug} (expected ${packet}); run the scenario first`);
  if (existsSync(verdict) && !args.includes("--again")) throw new Error(`${verdict} exists; pass --again to judge once more`);
  const after = worktreePath(ws, slug, "after");
  if (!existsSync(after)) throw new Error(`no after worktree at ${after}; the judge reads the harness run's code from it`);
  const st = settings(ws, args);
  if (st.budget === null || Number.isNaN(st.budget)) throw new Error("no budget: set budget_usd in docs/harnesskit/profile.md or pass --budget <usd>");
  const rows = readLedger(ws);
  const earlier = rows.filter((r) => r.half === "judge");
  const budget = budgetCheck(spentOf(rows), earlier.length ? estimateRun(earlier) : 0.5, 1, st.budget);
  if (!budget.ok) throw new Error(`budget: the judge estimates ${usd(budget.need)} but ${usd(budget.remaining)} remains of ${usd(st.budget)}; raise --budget`);
  // The rules are the workspace's current ones, not the copy inside the commit under test.
  const instruction = `Read ${packet} and follow ${join(ws, "docs", "harnesskit", "proof", "judge.md")}. Check the claims in both answers against the code in this folder. Write the verdict json to ${verdict}.`;
  const started = new Date().toISOString();
  const { lines, timedOut } = await runHeadless(after, instruction, st, Number(flag(args, "--cap-min") ?? 15) * 60_000, join(dir, "judge.jsonl"));
  const sum = summarizeStream(lines, st.model);
  appendLedger(ws, { slug, half: "judge", cost_usd: sum.cost, tokens: sum.tokens, seconds: sum.seconds, proof_exit: "", started_at: started });
  log(`judge: ${usd(sum.cost)}, ${sum.tokens} tokens, ${sum.seconds}s${timedOut ? ", TIMED OUT" : ""}`);
  // A judge sometimes answers with the verdict instead of writing it; keep it rather than pay for it twice.
  const said = existsSync(verdict) ? null : verdictFromText(sum.text);
  if (said) {
    writeFileSync(verdict, JSON.stringify(said, null, 1));
    log(`prove: the judge returned the verdict instead of writing it; saved it to ${verdict}`);
  }
  writePage(ws);
  if (!existsSync(verdict)) {
    log(`prove: the judge did not write ${verdict}; see ${join(dir, "judge.jsonl")}`);
    return 1;
  }
  log(`verdict: ${verdict}; page: writeup/proof/index.html`);
  return 0;
}

function finish(ws: string, sc: Scenario, budget: number, log: Log) {
  writePacket(ws, sc);
  writePage(ws);
  log(`remaining budget ${usd(budget - spentOf(readLedger(ws)))} of ${usd(budget)}`);
  log(`judge packet: writeup/proof/${sc.slug}/judge.md; page: writeup/proof/index.html`);
}

function collect(ws: string, slug: string | undefined, args: string[], log: Log): number {
  if (!slug) throw new Error("usage: harnesskit prove collect <slug>");
  const dir = proofDir(ws, slug);
  const panesFile = join(dir, "panes.json");
  if (!existsSync(panesFile)) throw new Error(`no herdr run recorded for ${slug} (expected ${panesFile})`);
  const { model, panes } = JSON.parse(readFileSync(panesFile, "utf8"));
  const meta = JSON.parse(readFileSync(join(dir, "scenario.json"), "utf8"));
  const proofCmd = scenarioProof(ws, slug);
  const projects = join(process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), ".claude"), "projects");
  for (const h of ["before", "after"] as const) {
    const p = panes[h];
    const encoded = transcriptFolder(realpathSync(p.worktree));
    const tdir = join(projects, encoded);
    const files = existsSync(tdir) ? readdirSync(tdir).filter((f) => f.endsWith(".jsonl")).map((f) => ({ f, t: statSync(join(tdir, f)).mtimeMs })).sort((x, y) => y.t - x.t) : [];
    // The newest transcript can belong to a later session in the same folder (the judge runs in `after`),
    // so take the newest one that opened with this scenario's prompt.
    const run = files
      .map(({ f }) => readFileSync(join(tdir, f), "utf8").split("\n").filter(Boolean))
      .find((lines) => sameText(firstPrompt(lines), meta.prompt));
    if (!run) throw new Error(`no transcript for ${h} under ${tdir} starts with the scenario's prompt; is the pane finished?`);
    const lines = run;
    writeFileSync(join(dir, `${h}.jsonl`), lines.join("\n") + "\n");
    const sum = summarizeStream(lines, model);
    const g = gradeHalf(ws, { ...meta, proof: proofCmd }, p.worktree, p.commit, dir, h, p.nested ?? {});
    const { proof_exit } = g;
    record(ws, { slug, half: h, commit: p.commit, cost_usd: sum.cost, tokens: sum.tokens, turns: sum.turns, seconds: sum.seconds, ...g, reset_exit: p.reset_exit, timed_out: false, started_at: p.started_at, runner: "herdr", files_opened: sum.opened, tool_calls: sum.toolCalls }, sum.text);
    log(`${h}: ${usd(sum.cost)} (estimated from the price table), ${sum.tokens} tokens, ${sum.seconds}s, proof exit ${proof_exit ?? "none"}`);
  }
  const budget = Number(flag(args, "--budget") ?? settings(ws, args).budget ?? 0);
  finish(ws, { slug, title: meta.title, kind: meta.kind, prompt: meta.prompt, mustMention: meta.mustMention ?? [], proof: proofCmd, reply: meta.reply ?? null } as Scenario, budget, log);
  return 0;
}

/** Herdr runs: type the scenario's reply into both panes, for a pane that is waiting on a question. */
function reply(ws: string, slug: string | undefined, log: Log): number {
  if (!slug) throw new Error("usage: harnesskit prove reply <slug>");
  const file = join(proofDir(ws, slug), "panes.json");
  if (!existsSync(file)) throw new Error(`no herdr run recorded for ${slug} (expected ${file})`);
  const { reply: text, panes } = JSON.parse(readFileSync(file, "utf8"));
  if (!text) throw new Error(`${slug} has no '## If the agent asks' reply`);
  for (const h of ["before", "after"]) {
    if (!panes[h]) continue;
    spawnSync("herdr", ["pane", "send-text", panes[h].pane, text]);
    spawnSync("herdr", ["pane", "send-keys", panes[h].pane, "Enter"]);
    log(`${h}: sent the reply to pane ${panes[h].pane}`);
  }
  return 0;
}

function scenarioProof(ws: string, slug: string): string | null {
  const p = join(proofDir(ws, slug), "scenario.json");
  return JSON.parse(readFileSync(p, "utf8")).proof ?? null;
}

export function main(argv: string[]): Promise<number> {
  return runProve(argv, { cwd: process.cwd(), log: (s) => console.log(s) });
}
