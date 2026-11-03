import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { runProve, renderPage } from "../src/prove.js";

const put = (p: string, text: string) => {
  mkdirSync(dirname(p), { recursive: true });
  writeFileSync(p, text);
};
const jl = (...o: object[]) => o.map((x) => JSON.stringify(x)).join("\n") + "\n";
const use = (id: string, name: string, input: object, ts?: string, msg = id, usage = { input_tokens: 10, output_tokens: 5 }) => ({ type: "assistant", ...(ts ? { timestamp: ts } : {}), message: { id: msg, role: "assistant", usage, content: [{ type: "tool_use", id, name, input }] } });
const res = (id: string, content: string, ts?: string, is_error = false) => ({ type: "user", ...(ts ? { timestamp: ts } : {}), message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, content, is_error }] } });
const T = (s: number) => new Date(Date.UTC(2026, 0, 1, 0, 0, s)).toISOString();

function workspace() {
  const ws = mkdtempSync(join(tmpdir(), "analyze-"));
  const proof = join(ws, "writeup", "proof", "s1");
  put(join(proof, "scenario.json"), JSON.stringify({ title: "Where is it", mustMention: ["`config/database.py`"] }));
  // after worktree has harness docs the before one lacks
  put(join(ws, ".harnesskit/proof/s1/before/app.py"), "x");
  put(join(ws, ".harnesskit/proof/s1/after/app.py"), "x");
  put(join(ws, ".harnesskit/proof/s1/after/docs/breadcrumb_analysis/_INDEX.md"), "| symptom | flow |\n| saves a report | export_report/FLOW.md (config/database.py) |\n");
  put(join(ws, ".harnesskit/proof/s1/after/docs/breadcrumb_analysis/export_report/FLOW.md"), "save goes through config/database.py");
  put(join(ws, ".harnesskit/proof/s1/after/docs/glossary.md"), "g");
  put(join(ws, ".harnesskit/proof/s1/after/CLAUDE.md"), "c");
  put(join(proof, "after.diff"), "diff --git a/config/database.py b/config/database.py\n+x\n");
  // before: transcript format, timestamps, a failed call, a repeated read, a late hit
  put(join(proof, "before.jsonl"), jl(
    { type: "user", timestamp: T(0), cwd: "/w/before", message: { role: "user", content: "q" } },
    use("a", "Grep", { pattern: "report", path: "/w/before" }, T(2)),
    res("a", "No matches found", T(3)),
    use("b", "Read", { file_path: "/w/before/app.py" }, T(10)),
    res("b", "import x", T(11)),
    use("c", "Read", { file_path: "/w/before/app.py" }, T(12)),
    res("c", "import x", T(13)),
    use("d", "Bash", { command: "python missing.py" }, T(14)),
    res("d", "Traceback\n[exited with code 2]", T(20), true),
    use("e", "Read", { file_path: "/w/before/config/database.py" }, T(25)),
    res("e", "def save_report", T(26)),
    { type: "assistant", timestamp: T(30), message: { id: "z", content: [{ type: "text", text: "done" }], usage: { input_tokens: 1, output_tokens: 1 } } },
  ));
  // after: stream-json, no timestamps; opens the index and the glossary, never the FLOW doc
  put(join(proof, "after.jsonl"), jl(
    use("a", "Read", { file_path: "CLAUDE.md" }),
    res("a", "c"),
    use("b", "Bash", { command: "cat docs/breadcrumb_analysis/_INDEX.md" }),
    res("b", "| saves a report |"),
    use("c", "Read", { file_path: "config/database.py" }),
    res("c", "def save_report"),
    { type: "result", duration_ms: 12000, total_cost_usd: 0.1, usage: {}, num_turns: 3, result: "ok" },
  ));
  return ws;
}

const analyze = async (ws: string, slug?: string) => {
  const lines: string[] = [];
  const code = await runProve(slug ? ["analyze", slug] : ["analyze"], { cwd: ws, log: (s) => lines.push(s) });
  return { code, lines };
};
const readJson = (ws: string) => JSON.parse(readFileSync(join(ws, "writeup/proof/s1/analysis.json"), "utf8"));

describe("prove analyze", () => {
  it("reads a timestamped transcript: steps, time by kind, first relevant file, waste", async () => {
    const ws = workspace();
    expect((await analyze(ws, "s1")).code).toBe(0);
    const b = readJson(ws).before;
    expect(b.seconds).toBe(30);
    expect(b.steps.map((s: any) => [s.i, s.tool, s.target])).toEqual([
      [1, "Grep", "report in ."],
      [2, "Read", "app.py"],
      [3, "Read", "app.py"],
      [4, "Bash", "python missing.py"],
      [5, "Read", "config/database.py"],
    ]);
    expect(b.steps[1].t).toBe(10);
    expect(b.steps[0]).toMatchObject({ tokens_in: 10, tokens_out: 5, bytes: 16 });
    expect(b.steps[3].error).toBe(true);
    expect(b.time_by_kind.run.seconds).toBe(6);
    expect(b.time_by_kind.read.seconds).toBe(3);
    expect(b.time_by_kind.generation.seconds + b.time_by_kind.deliberation.seconds).toBeGreaterThan(10);
    expect(Object.values(b.time_by_kind).reduce((a: number, k: any) => a + k.share, 0)).toBeCloseTo(1, 1);
    expect(b.first_relevant).toEqual({ step: 5, seconds: 25, file: "config/database.py", via: "file" });
    expect(b.waste).toMatchObject({ failed: 1, repeated_reads: ["app.py"], empty_searches: 1, total: 3 });
  });

  it("reads stream-json without timestamps by order only, and finds the unopened harness doc", async () => {
    const ws = workspace();
    await analyze(ws, "s1");
    const a = readJson(ws).after;
    expect(a.seconds).toBe(12);
    expect(a.time_by_kind).toBeNull();
    expect(a.steps.every((s: any) => s.t === null)).toBe(true);
    expect(a.first_relevant).toMatchObject({ step: 2, seconds: null, via: "doc" });
    expect(a.harness_used).toEqual(["CLAUDE.md", "docs/breadcrumb_analysis/_INDEX.md"]);
    expect(a.harness_missed).toEqual(["docs/breadcrumb_analysis/export_report/FLOW.md"]);
  });

  it("writes analysis.md with the cross table and the slowest steps, and the proof page folds it in", async () => {
    const ws = workspace();
    expect((await analyze(ws)).code).toBe(0);
    const md = readFileSync(join(ws, "writeup/proof/analysis.md"), "utf8");
    expect(md).toContain("| s1 | 30 → 12 | 5 → 3 | 25s (step 5) → step 2 | 3 → 0 | 12/0/18 → - | 0 → 2 | 1 |");
    expect(md).toContain("## s1");
    expect(md).toContain("python missing.py");
    expect(md).toContain("| task |");
    const html = renderPage(ws);
    expect(html).toContain('<section id="analysis"><h2>Analysis</h2>');
    expect(html).toContain("analysis.md");
    expect(html.indexOf('id="analysis"')).toBeGreaterThan(html.indexOf('id="scenarios"'));
    expect(html.indexOf('id="analysis"')).toBeLessThan(html.indexOf('id="next"'));
  });

  it("fails on an unknown slug and leaves the page alone when nothing was analyzed", async () => {
    const ws = workspace();
    expect((await analyze(ws, "nope")).code).toBe(1);
    expect(renderPage(ws)).not.toContain('id="analysis"');
  });
});

describe("prove analyze measurement", () => {
  const run = async (lines: object[], sc: object = { title: "t", mustMention: ["`a.py`"] }) => {
    const ws = mkdtempSync(join(tmpdir(), "analyze-"));
    const proof = join(ws, "writeup", "proof", "s1");
    put(join(proof, "scenario.json"), JSON.stringify(sc));
    put(join(proof, "before.jsonl"), jl(...lines));
    put(join(ws, ".harnesskit/proof/s1/before/a.py"), "x");
    await analyze(ws, "s1");
    return { ws, b: readJson(ws).before };
  };

  it("groups parallel calls into one step per message, think counted once", async () => {
    const { b } = await run([
      { type: "user", timestamp: T(0), message: { role: "user", content: "q" } },
      { type: "assistant", timestamp: T(10), message: { id: "m1", usage: { input_tokens: 1, output_tokens: 800 }, content: [{ type: "tool_use", id: "x", name: "Read", input: { file_path: "a.py" } }] } },
      { type: "assistant", timestamp: T(10), message: { id: "m1", usage: { input_tokens: 1, output_tokens: 800 }, content: [{ type: "tool_use", id: "y", name: "Read", input: { file_path: "b.py" } }] } },
      res("x", "1", T(11)),
      res("y", "2", T(14)),
    ]);
    expect(b.steps).toHaveLength(1);
    expect(b.steps[0].calls.map((c: any) => c.target)).toEqual(["a.py", "b.py"]);
    expect(b.steps[0].think_s).toBe(10);
    expect(b.steps[0].s).toBe(4);
    expect(b.steps[0].gen_s).toBe(10); // 800 tokens at 80/s, capped by the gap
    expect(b.steps[0].delib_s).toBe(0);
  });

  it("counts each tool_use and tool_result once when the log repeats them", async () => {
    const u = use("x", "Read", { file_path: "a.py" }, T(2));
    const r = res("x", "abc", T(3));
    const { b } = await run([{ type: "user", timestamp: T(0), message: { role: "user", content: "q" } }, u, u, r, r]);
    expect(b.steps).toHaveLength(1);
    expect(b.steps[0].bytes).toBe(3);
    expect(b.tokens).toMatchObject({ output: 5 });
  });

  it("splits think into generation, deliberation and final answer, and cached from uncached input", async () => {
    const { b } = await run([
      { type: "user", timestamp: T(0), message: { role: "user", content: "q" } },
      use("x", "Read", { file_path: "a.py" }, T(20), "m1", { input_tokens: 5, output_tokens: 160, cache_read_input_tokens: 100 } as any),
      res("x", "abc", T(21)),
      { type: "assistant", timestamp: T(30), message: { id: "z", content: [{ type: "text", text: "done" }], usage: { input_tokens: 2, output_tokens: 1, cache_read_input_tokens: 50 } } },
    ]);
    expect(b.steps[0].gen_s).toBe(2);
    expect(b.steps[0].delib_s).toBe(18);
    expect(b.time_by_kind.final.seconds).toBe(9);
    expect(b.tokens).toEqual({ cached: 150, uncached: 7, output: 161 });
  });

  it("reaches the target through a doc that names it, and tags the cause of errors", async () => {
    const ws = mkdtempSync(join(tmpdir(), "analyze-"));
    const proof = join(ws, "writeup", "proof", "s1");
    put(join(proof, "scenario.json"), JSON.stringify({ title: "t", mustMention: [] }));
    put(join(proof, "after.diff"), "diff --git a/a.py b/a.py\n+x\n");
    put(join(proof, "before.diff"), "diff --git a/c.py b/c.py\n+x\n");
    put(join(ws, ".harnesskit/proof/s1/after/docs/guide/note.md"), "see c.py");
    put(join(ws, ".harnesskit/proof/s1/after/docs/guide/other.md"), "unrelated");
    put(join(proof, "after.jsonl"), jl(
      use("a", "Read", { file_path: "docs/guide/note.md" }),
      res("a", "see c.py"),
      use("b", "mcp__x", {}),
      res("b", "No such tool available: mcp__x", undefined, true),
    ));
    await analyze(ws, "s1");
    const a = readJson(ws).after;
    expect(a.first_relevant).toMatchObject({ step: 1, file: "c.py", via: "doc" });
    expect(a.steps[1].cause).toBe("environment");
    expect(a.harness_missed).toEqual([]);
  });

  it("splits time into waste, verification and work", async () => {
    const { b } = await run([
      { type: "user", timestamp: T(0), message: { role: "user", content: "q" } },
      use("a", "Bash", { command: "pytest -q" }, T(2)),
      res("a", "ok", T(7)),
      use("b", "Bash", { command: "python nope.py" }, T(8)),
      res("b", "Traceback\n[exited with code 1]", T(10), true),
      use("c", "Read", { file_path: "a.py" }, T(11)),
      res("c", "x", T(12)),
    ]);
    expect(b.time_split).toEqual({ waste: 3, verification: 7, work: 2 });
    expect(b.steps[1].cause).toBe("task");
  });
});
