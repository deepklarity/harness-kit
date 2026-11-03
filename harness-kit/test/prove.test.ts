import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  budgetCheck,
  costFromUsage,
  estimateRun,
  parseLedger,
  parseProfile,
  parseScenario,
  runProve,
  summarizeStream,
  transcriptFolder,
  porcelainPaths,
  verdictFromText,
  sh,
  firstPrompt,
  appendLedger,
  renderPage,
} from "../src/prove.js";

const SCENARIO = `# Where does a task get its model

Tier: mid
Difficulty: 2
Kind: outcome
Bills: yes
Exercises: the verify gate; a bare agent would not know scripts/verify.sh exists

## The prompt

Find the file that names the model and say so.

## The proof

\`\`\`sh
test -f NEWFILE
\`\`\`

## Reset

\`\`\`sh
rm -f scratch.txt
\`\`\`

## A correct answer must mention

- NEWFILE
- the verify gate
`;

describe("scenario parser", () => {
  it("reads header lines and sections", () => {
    const s = parseScenario(SCENARIO, "writeup/scenarios/02-Task Model.md");
    expect(s.slug).toBe("02-task-model");
    expect(s).toMatchObject({ tier: "mid", kind: "outcome", bills: true, difficulty: "2" });
    expect(s.exercises).toContain("verify gate");
    expect(s.prompt).toBe("Find the file that names the model and say so.");
    expect(s.proof).toBe("test -f NEWFILE");
    expect(s.reset).toBe("rm -f scratch.txt");
    expect(s.mustMention).toEqual(["NEWFILE", "the verify gate"]);
  });
  it("defaults to a question and rejects bad input", () => {
    const q = parseScenario("# Q\n\n## The prompt\n\nhi\n", "q.md");
    expect(q.kind).toBe("question");
    expect(q.proof).toBeNull();
    expect(() => parseScenario("# Q\n\nbody\n", "q.md")).toThrow(/The prompt/);
    expect(() => parseScenario("# Q\nKind: outcome\n\n## The prompt\n\nhi\n", "q.md")).toThrow(/proof/);
    expect(() => parseScenario("# Q\nKind: riddle\n\n## The prompt\n\nhi\n", "q.md")).toThrow(/Kind/);
  });
});

describe("profile, ledger, budget, price", () => {
  it("parses key: value lines and ignores comments and other text", () => {
    const p = parseProfile("# Harness profile\n\nbudget_usd: 10\nrunner: herdr   # herdr | headless\nmodel: sonnet\nsome prose here\n");
    expect(p).toEqual({ budget_usd: "10", runner: "herdr", model: "sonnet" });
  });
  it("reads the ledger and estimates from the largest run", () => {
    const rows = parseLedger("slug,half,cost_usd,tokens,seconds,proof_exit,started_at\na,before,0.5,100,10,0,t\na,after,1.25,100,10,1,t\n");
    expect(rows).toHaveLength(2);
    expect(rows[1].cost_usd).toBe(1.25);
    expect(estimateRun(rows)).toBe(1.25);
    expect(estimateRun([])).toBe(0.75);
  });
  it("refuses when spent plus estimate exceeds the budget", () => {
    expect(budgetCheck(9, 1, 2, 10)).toMatchObject({ ok: false, remaining: 1 });
    expect(budgetCheck(5, 1, 2, 10)).toMatchObject({ ok: true, remaining: 5 });
  });
  it("prices usage from the table and prefers the result event", () => {
    expect(costFromUsage("sonnet", { input_tokens: 1_000_000, output_tokens: 1_000_000 })).toBeGreaterThan(0);
    const lines = [
      JSON.stringify({ type: "assistant", message: { usage: { input_tokens: 5, output_tokens: 5 } } }),
      JSON.stringify({ type: "result", total_cost_usd: 0.42, num_turns: 3, duration_ms: 2000, result: "done", usage: { input_tokens: 10, output_tokens: 20, cache_read_input_tokens: 30, cache_creation_input_tokens: 40 } }),
    ];
    expect(summarizeStream(lines, "sonnet")).toMatchObject({ cost: 0.42, turns: 3, seconds: 2, text: "done", tokens: 100 });
  });
});

describe("opened files", () => {
  const wt = "/w/ws/.harnesskit/proof/s/after";
  const asst = (...blocks: any[]) => JSON.stringify({ type: "assistant", message: { content: blocks } });
  const tool = (name: string, input: any) => ({ type: "tool_use", name, input });
  it("collects Read, Grep and Glob paths relative to the worktree, deduped, from both branches", () => {
    const lines = [
      asst(tool("Read", { file_path: `${wt}/docs/a.md` }), tool("Grep", { pattern: "x", path: `${wt}/src` })),
      asst(tool("Glob", { pattern: "*", path: "/elsewhere/x" }), tool("Read", { file_path: `${wt}/docs/a.md` }), tool("Bash", { command: "ls" })),
    ];
    const want = ["docs/a.md", "src", "/elsewhere/x"];
    expect(summarizeStream(lines, "sonnet").opened).toEqual(want);
    const withResult = [...lines, JSON.stringify({ type: "result", total_cost_usd: 1, usage: {} })];
    expect(summarizeStream(withResult, "sonnet").opened).toEqual(want);
  });
  it("adds files a Bash command reads or runs, and ignores bare words", () => {
    const lines = [
      asst(tool("Bash", { command: `sed -n 320,345p src/itsdangerous/serializer.py; cat ${wt}/docs/breadcrumb_analysis/x/DEBUG.md | head` })),
      asst(tool("Bash", { command: "scripts/verify.sh && uv run pytest -q tests/test_x.py -k last" })),
      asst(tool("Bash", { command: "ls; git status" })),
    ];
    expect(summarizeStream(lines, "sonnet").opened).toEqual([
      "src/itsdangerous/serializer.py", "docs/breadcrumb_analysis/x/DEBUG.md", "scripts/verify.sh", "tests/test_x.py",
    ]);
  });
});

describe("prove end to end with a fake claude", () => {
  let root: string, repo: string, scenario: string, oldPath: string, oldHome: string | undefined;
  let baseline: string;
  const git = (...a: string[]) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8" }).trim();
  const quiet = () => {
    const out: string[] = [];
    return { out, log: (s: string) => out.push(s) };
  };

  beforeAll(() => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "prove-")));
    const bin = join(root, "bin");
    mkdirSync(bin);
    const fake = join(bin, "claude");
    writeFileSync(
      fake,
      `#!/usr/bin/env node
const fs = require("fs");
const sleep = Number(process.env.FAKE_CLAUDE_SLEEP_MS || 0);
setTimeout(() => {
  const has = fs.existsSync("NEWFILE");
  console.log(JSON.stringify({ type: "system", subtype: "init" }));
  const cwd = process.cwd();
  console.log(JSON.stringify({ type: "assistant", message: { id: "m1", content: [
    { type: "tool_use", name: "Read", input: { file_path: cwd + "/README.md" } },
    { type: "tool_use", name: "Read", input: { file_path: cwd + "/docs/guide.md" } },
    { type: "tool_use", name: "Read", input: { file_path: cwd + "/README.md" } },
    { type: "tool_use", name: "Grep", input: { pattern: "x", path: cwd + "/docs" } },
    { type: "tool_use", name: "Bash", input: { command: "ls" } } ] } }));
  const wanted = /verdict json to (\\S+\\.json)/.exec(process.argv.join(" "));
  if (wanted) fs.writeFileSync(wanted[1], JSON.stringify({ preferred: "tie", preferred_why: "judged by fake" }));
  console.log(JSON.stringify({ type: "result", subtype: "success", total_cost_usd: Number(process.env.FAKE_CLAUDE_COST || 0.5), num_turns: 2, duration_ms: 1500,
    result: "NEWFILE present: " + has + " args: " + process.argv.slice(2).join(" "), usage: { input_tokens: 10, output_tokens: 20 } }));
}, sleep);
`,
    );
    chmodSync(fake, 0o755);
    oldPath = process.env.PATH ?? "";
    process.env.PATH = `${bin}:${oldPath}`;
    oldHome = process.env.CLAUDE_CONFIG_DIR;

    repo = join(root, "ws");
    mkdirSync(repo);
    git("init", "-q");
    git("config", "user.email", "t@t");
    git("config", "user.name", "t");
    writeFileSync(join(repo, "README.md"), "hi\n");
    mkdirSync(join(repo, "docs"));
    writeFileSync(join(repo, "docs/guide.md"), "g\n");
    writeFileSync(join(repo, ".gitignore"), ".harnesskit/\nwriteup/\n");
    git("add", "-A");
    git("commit", "-qm", "baseline");
    baseline = git("rev-parse", "HEAD");
    writeFileSync(join(repo, "NEWFILE"), "after\n");
    git("add", "-A");
    git("commit", "-qm", "harness");
    scenario = join(root, "where-does-a-task-get-its-model.md");
    writeFileSync(scenario, SCENARIO);
  });
  afterAll(() => {
    process.env.PATH = oldPath;
    if (oldHome === undefined) delete process.env.CLAUDE_CONFIG_DIR;
    else process.env.CLAUDE_CONFIG_DIR = oldHome;
  });

  const run = (args: string[]) => {
    const q = quiet();
    return runProve([scenario, "--runner", "headless", "--baseline", baseline, ...args], { cwd: repo, log: q.log }).then((code) => ({ code, text: q.out.join("\n") }));
  };

  it("dry run prints the plan and touches nothing", async () => {
    const r = await run(["--dry-run", "--budget", "10"]);
    expect(r.code).toBe(0);
    expect(r.text).toContain(".harnesskit/proof/where-does-a-task-get-its-model/before");
    expect(r.text).toMatch(/estimate/i);
    expect(existsSync(join(repo, ".harnesskit"))).toBe(false);
    expect(existsSync(join(repo, "writeup"))).toBe(false);
  });

  it("refuses a run that would pass the budget, without creating worktrees", async () => {
    const r = await run(["--budget", "1"]);
    expect(r.code).toBe(1);
    expect(r.text).toMatch(/budget/i);
    expect(existsSync(join(repo, ".harnesskit"))).toBe(false);
  });

  it("runs both halves in worktrees at the right commits, records proof exits and the ledger", async () => {
    const r = await run(["--budget", "10"]);
    expect(r.code).toBe(0);
    const slug = "where-does-a-task-get-its-model";
    const dir = join(repo, "writeup/proof", slug);
    const before = JSON.parse(readFileSync(join(dir, "before.json"), "utf8"));
    const after = JSON.parse(readFileSync(join(dir, "after.json"), "utf8"));
    // the baseline worktree lacks the file committed after it
    expect(readFileSync(join(dir, "before.md"), "utf8")).toContain("NEWFILE present: false");
    expect(readFileSync(join(dir, "after.md"), "utf8")).toContain("NEWFILE present: true");
    expect(readFileSync(join(dir, "before.md"), "utf8")).toContain("--output-format stream-json");
    expect(before.proof_exit).toBe(1);
    expect(after.proof_exit).toBe(0);
    expect(existsSync(join(dir, "before.jsonl"))).toBe(true);
    const ledger = readFileSync(join(repo, "writeup/proof/ledger.csv"), "utf8").trim().split("\n");
    expect(ledger[0]).toBe("slug,half,cost_usd,tokens,seconds,proof_exit,started_at");
    expect(ledger).toHaveLength(3);
    expect(ledger.slice(1).map((l) => l.split(",").slice(0, 4).join(",")).sort()).toEqual(["where-does-a-task-get-its-model,after,0.5,30", "where-does-a-task-get-its-model,before,0.5,30"]);
    expect(r.text).toMatch(/remaining/i);
    // worktrees have no writeup/
    expect(existsSync(join(repo, ".harnesskit/proof", slug, "after/writeup"))).toBe(false);
    expect(existsSync(join(repo, ".harnesskit/proof", slug, "before/NEWFILE"))).toBe(false);
    expect(existsSync(join(repo, ".harnesskit/proof", slug, "after/NEWFILE"))).toBe(true);
    // judge packet and page
    expect(existsSync(join(dir, "mapping.json"))).toBe(false);
    const mapping = JSON.parse(readFileSync(join(repo, ".harnesskit/proof", slug, "mapping.json"), "utf8"));
    expect(new Set([mapping.A, mapping.B])).toEqual(new Set(["before", "after"]));
    expect(before.files_opened).toEqual(["README.md", "docs/guide.md", "docs"]);
    const judge = readFileSync(join(dir, "judge.md"), "utf8");
    expect(judge).not.toMatch(/Do not open it/);
    expect(judge).toContain("Files this run opened: README.md, docs/guide.md, docs");
    expect(judge).toContain("Harness files opened: docs/guide.md, docs");
    expect(judge).toContain("NEWFILE present: false");
    expect(judge).toContain("the verify gate");
    const page = readFileSync(join(repo, "writeup/proof/index.html"), "utf8");
    expect(page).toContain("Not judged");
    expect(page).not.toContain("prefers-color-scheme");
  });

  it("refuses the next run once the ledger has spent the budget", async () => {
    const r = await run(["--budget", "1.5"]);
    expect(r.code).toBe(1);
    expect(r.text).toMatch(/budget/i);
  });

  it("render shows the verdict once judged, worst first", async () => {
    const slug = "where-does-a-task-get-its-model";
    const dir = join(repo, "writeup/proof", slug);
    const mapping = JSON.parse(readFileSync(join(repo, ".harnesskit/proof", slug, "mapping.json"), "utf8"));
    const afterLetter = mapping.A === "after" ? "A" : "B";
    writeFileSync(join(dir, "verdict.json"), JSON.stringify({ preferred: afterLetter, preferred_why: "found it first" }));
    const q = quiet();
    expect(await runProve(["render"], { cwd: repo, log: q.log })).toBe(0);
    const page = readFileSync(join(repo, "writeup/proof/index.html"), "utf8");
    expect(page).toMatch(/helped in 1 of 1 scenarios/);
    expect(page).toContain("Harness</span>");
    expect(page).toContain("found it first");
  });

  it("render moves an old mapping.json out of writeup", async () => {
    const slug = "where-does-a-task-get-its-model";
    const old = join(repo, "writeup/proof", slug, "mapping.json");
    const sealed = join(repo, ".harnesskit/proof", slug, "mapping.json");
    const m = readFileSync(sealed, "utf8");
    rmSync(sealed);
    writeFileSync(old, m);
    const q = quiet();
    expect(await runProve(["render"], { cwd: repo, log: q.log })).toBe(0);
    expect(existsSync(old)).toBe(false);
    expect(readFileSync(sealed, "utf8")).toBe(m);
  });

  it("judge runs headless in the after worktree, logs a ledger row and renders; refuses a second time without --again", async () => {
    const slug = "where-does-a-task-get-its-model";
    const dir = join(repo, "writeup/proof", slug);
    rmSync(join(dir, "verdict.json"));
    const q = quiet();
    expect(await runProve(["judge", slug, "--budget", "10"], { cwd: repo, log: q.log })).toBe(0);
    expect(JSON.parse(readFileSync(join(dir, "verdict.json"), "utf8")).preferred).toBe("tie");
    expect(readFileSync(join(repo, "writeup/proof/ledger.csv"), "utf8")).toMatch(new RegExp(`${slug},judge,0.5,30`));
    expect(readFileSync(join(repo, "writeup/proof/index.html"), "utf8")).toContain("judged by fake");
    const again = quiet();
    expect(await runProve(["judge", slug, "--budget", "10"], { cwd: repo, log: again.log })).toBe(1);
    expect(again.out.join("\n")).toMatch(/--again/);
    expect(await runProve(["judge", slug, "--budget", "10", "--again"], { cwd: repo, log: quiet().log })).toBe(0);
  });

  it("judge refuses when the budget is spent", async () => {
    const q = quiet();
    expect(await runProve(["judge", "where-does-a-task-get-its-model", "--budget", "0.1", "--again"], { cwd: repo, log: q.log })).toBe(1);
    expect(q.out.join("\n")).toMatch(/budget/i);
  });

  it("records a timeout and still writes a ledger row", async () => {
    process.env.FAKE_CLAUDE_SLEEP_MS = "5000";
    try {
      const r = await run(["--budget", "100", "--cap-min", "0.01"]);
      expect(r.code).toBe(0);
      const rec = JSON.parse(readFileSync(join(repo, "writeup/proof/where-does-a-task-get-its-model/after.json"), "utf8"));
      expect(rec.timed_out).toBe(true);
    } finally {
      delete process.env.FAKE_CLAUDE_SLEEP_MS;
    }
  }, 20000);

  it("clean removes the worktrees", async () => {
    const q = quiet();
    expect(await runProve(["--clean", "where-does-a-task-get-its-model"], { cwd: repo, log: q.log })).toBe(0);
    expect(existsSync(join(repo, ".harnesskit/proof/where-does-a-task-get-its-model/after"))).toBe(false);
    expect(git("worktree", "list")).not.toContain("harnesskit");
  });

  it("refuses with uncommitted changes outside writeup and .harnesskit", async () => {
    writeFileSync(join(repo, "README.md"), "dirty\n");
    const r = await run(["--budget", "100"]);
    expect(r.code).toBe(1);
    expect(r.text).toMatch(/commit/i);
    git("checkout", "README.md");
  });

  it("explains when the baseline is missing", async () => {
    const q = quiet();
    const code = await runProve([scenario, "--runner", "headless", "--budget", "10"], { cwd: repo, log: q.log });
    expect(code).toBe(1);
    expect(q.out.join("\n")).toMatch(/baseline/i);
  });
});

describe("ledger writes", () => {
  it("replaces the row for the same run instead of billing it twice", () => {
    const ws = mkdtempSync(join(tmpdir(), "ledger-"));
    const row = { slug: "s", half: "after", cost_usd: 0.29, tokens: 10, seconds: 5, proof_exit: "0", started_at: "t1" };
    appendLedger(ws, row);
    appendLedger(ws, { ...row, proof_exit: "1" });
    appendLedger(ws, { ...row, started_at: "t2" });
    const rows = parseLedger(readFileSync(join(ws, "writeup/proof/ledger.csv"), "utf8"));
    expect(rows.map((r) => [r.started_at, String(r.proof_exit)])).toEqual([["t1", "1"], ["t2", "0"]]);
  });
});

describe("proof page", () => {
  let ws: string;
  const half = (slug: string, h: string, cost: number, tokens: number, seconds: number, proof: number | null, files?: string[]) => {
    writeFileSync(join(ws, "writeup/proof", slug, `${h}.json`), JSON.stringify({ slug, half: h, commit: h === "before" ? "abcdef1234567890" : "1234567890abcdef", cost_usd: cost, tokens, turns: 3, seconds, proof_exit: proof, ...(files ? { files_opened: files } : {}) }));
  };
  const scenario = (slug: string, title: string, verdict?: object, map = { A: "before", B: "after" }, extra: object = {}) => {
    const dir = join(ws, "writeup/proof", slug);
    mkdirSync(dir, { recursive: true });
    mkdirSync(join(ws, ".harnesskit/proof", slug), { recursive: true });
    writeFileSync(join(dir, "scenario.json"), JSON.stringify({ title, exercises: `exercises ${slug}`, kind: "question", difficulty: "easy", ...extra }));
    writeFileSync(join(ws, ".harnesskit/proof", slug, "mapping.json"), JSON.stringify(map));
    if (verdict) writeFileSync(join(dir, "verdict.json"), JSON.stringify(verdict));
  };
  const ok = (bought = "") => ({ achieved: "yes", process: { what_the_harness_bought: bought } });

  beforeAll(() => {
    ws = realpathSync(mkdtempSync(join(tmpdir(), "page-")));
    mkdirSync(join(ws, "docs/harnesskit"), { recursive: true });
    writeFileSync(join(ws, "docs/harnesskit/profile.md"), "budget_usd: 10\n");
    // harness wins: harness is B
    scenario("a-win", "Win scenario", { A: ok(), B: ok("it read the breadcrumb"), preferred: "B", preferred_why: "Answer B was faster" });
    half("a-win", "before", 0.2, 200000, 20, 0);
    half("a-win", "after", 0.1, 100000, 10, 0, ["docs/breadcrumb_analysis/x/DEBUG.md", "src/app.py"]);
    // baseline wins: harness is A here
    scenario("b-loss", "Loss scenario", { A: { achieved: "no", process: {} }, B: ok(), preferred: "B", preferred_why: "Answer B was right" }, { A: "after", B: "before" });
    half("b-loss", "before", 0.2, 100000, 10, null);
    half("b-loss", "after", 0.3, 150000, 20, null, ["src/app.py"]);
    scenario("c-tie", "Tie scenario", { A: ok(), B: ok(), preferred: "tie", preferred_why: "same" });
    half("c-tie", "before", 0.1, 1000, 5, 1);
    half("c-tie", "after", 0.15, 1000, 5, 0);
    scenario("d-open", "Unjudged scenario");
    half("d-open", "before", 0.1, 1000, 5, null);
    half("d-open", "after", 0.1, 1000, 5, null);
    // proof needs a scripted reply that neither run asked for
    scenario("e-invalid", "Invalid scenario", { A: { achieved: "no", process: {} }, B: { achieved: "no", process: {} }, preferred: "A", preferred_why: "cheaper" }, { A: "before", B: "after" }, { reply: "use resumes.csv" });
    half("e-invalid", "before", 0.1, 1000, 5, 1);
    half("e-invalid", "after", 0.1, 1000, 5, 1);
    writeFileSync(join(ws, "writeup/proof/ledger.csv"), "slug,half,cost_usd,tokens,seconds,proof_exit,started_at\na-win,before,0.2,1,1,,t1\na-win,judge,0.32,1,1,,t2\n");
  });
  afterAll(() => rmSync(ws, { recursive: true, force: true }));

  const page = () => renderPage(ws);
  const section = (p: string, id: string) => p.slice(p.indexOf(`<section id="${id}"`), p.indexOf("</section>", p.indexOf(`<section id="${id}"`)));
  const rowOf = (p: string, slug: string) => p.slice(p.indexOf(`data-slug="${slug}"`), p.indexOf("</tr>", p.indexOf(`id="d-${slug}"`)));

  it("says the direction plainly in the verdict, with the trust line", () => {
    const p = page();
    expect(p).toContain('<p class="kicker">Harness proof</p>');
    expect(p).toContain(`<h1>${basename(ws)}</h1>`);
    expect(p).toContain('<p class="verdict">The harness helped in 1 of 4 scenarios and cost 8% more; 1 tied; the harness was correct less often (2 against 3 of 3).</p>');
    expect(p).toContain("sonnet, medium effort");
    expect(p).toContain("1 run per half");
    expect(p).toContain("judged blind");
    expect(p).toContain("compared abcdef1 (before) with 1234567 (after)");
    expect(p).toContain("judging cost $0.32");
    expect(p).toContain("1 proof invalid");
    expect(p).toMatch(/rendered \d{4}-\d\d-\d\d \d\d:\d\d UTC/);
  });

  it("names each scenario once, in one table before Next, in file order", () => {
    const p = page();
    const at = (id: string) => p.indexOf(`<section id="${id}"`);
    expect(at("scenarios")).toBeGreaterThan(p.indexOf("</header>"));
    expect(at("scenarios")).toBeLessThan(at("next"));
    expect(at("comparison")).toBe(-1);
    const titles = ["Win scenario", "Loss scenario", "Tie scenario", "Unjudged scenario", "Invalid scenario"];
    for (const t of titles) expect(p.split(t).length - 1).toBe(1);
    const scn = section(p, "scenarios");
    expect(scn.match(/<tr class="row"/g)).toHaveLength(5);
    const order = titles.map((t) => scn.indexOf(t));
    expect(order).toEqual([...order].sort((x, y) => x - y));
    expect(scn).toContain('<span class="meta">question · easy</span>');
    expect(scn).toContain("<b>What it exercises:</b> exercises a-win");
  });

  it("gives each scenario a comparison row with a word pill: green harness, neutral baseline, never red", () => {
    const cmp = section(page(), "scenarios");
    expect(cmp.match(/<tr class="row"/g)).toHaveLength(5);
    expect(cmp).toContain('<span class="pill harness">Harness</span>');
    expect(cmp).toContain('<span class="pill baseline">Baseline</span>');
    expect(cmp).toContain('<span class="pill tie">Tie</span>');
    expect(cmp).toContain('<span class="pill none">Not judged</span>');
    expect(page()).not.toMatch(/\.pill\.baseline\{[^}]*(red|#a31515|#b3392f)/);
  });

  it("words each change and colours it by direction for the harness", () => {
    const row = rowOf(page(), "a-win");
    expect(row).toContain("200k &rarr; 100k<br><span class=\"delta better\">50% fewer</span>");
    expect(row).toContain('<span class="delta better">50% less time</span>');
    expect(row).toContain('<span class="delta better">50% less</span>');
    const loss = rowOf(page(), "b-loss");
    expect(loss).toContain('<span class="delta worse">50% more</span>');
    expect(rowOf(page(), "c-tie")).toContain('<span class="delta same">unchanged</span>');
  });

  it("shows correctness as marks with words for screen readers", () => {
    const row = rowOf(page(), "b-loss");
    expect(row).toContain('<span class="no" aria-hidden="true">&#10007;</span><span class="sr">not correct</span>');
    expect(row).toContain('<span class="yes" aria-hidden="true">&#10003;</span><span class="sr">correct</span>');
  });

  it("marks a proof that failed in both for a missing detail as invalid and leaves it out of the counts", () => {
    const row = rowOf(page(), "e-invalid");
    expect(row).toContain("Proof invalid &mdash; not counted");
    expect(row).toContain("<span class=\"sr\">not counted</span>");
    const total = section(page(), "scenarios").match(/<tr class="total">[\s\S]*?<\/tr>/)![0];
    expect(total).toContain("Baseline 3 of 3");
    expect(total).toContain("Harness 2 of 3");
    expect(total).not.toContain("of 4");
    expect(total).toContain("1 invalid, not counted");
    expect(rowOf(page(), "a-win")).not.toContain("Proof invalid");
  });

  it("totals the judged scenarios in a last row", () => {
    const total = section(page(), "scenarios").match(/<tr class="total">[\s\S]*?<\/tr>/)![0];
    expect(total).toContain("1 harness &middot; 2 baseline &middot; 1 tie");
    expect(total).toContain('<span class="delta worse">8% more</span>');
  });

  it("puts the judge's reason, harness files, what it bought and the three links in each detail", () => {
    const row = rowOf(page(), "a-win");
    expect(row).toContain("Answer B was faster".replace("Answer B", "the harness run"));
    expect(row).toContain('<code class="hf" title="harness file">docs/breadcrumb_analysis/x/DEBUG.md</code>');
    expect(row).toContain("<code>src/app.py</code>");
    expect(row).toContain("it read the breadcrumb");
    for (const f of ["before.md", "after.md", "verdict.json"]) expect(row).toContain(`href="a-win/${f}"`);
    expect(rowOf(page(), "b-loss")).toContain("opened no harness file");
    expect(page()).not.toContain("any gain came from CLAUDE.md, read at start");
  });

  it("lists what to do next from the data", () => {
    const next = section(page(), "next");
    expect(next).toContain("Fix the proof for scenario 5");
    expect(next).toContain("In scenario 2 the baseline won");
    expect(next).toContain("In scenario 2 the harness run opened no harness file");
    expect(next).not.toContain("Win scenario");
  });

  it("is light only, has a table wrapper that scrolls and no dark-mode block", () => {
    const p = page();
    expect(p).not.toContain("prefers-color-scheme");
    expect(p).not.toContain("data-theme");
    expect(p).toContain("overflow-x:auto");
    expect(p).toContain('<meta name="viewport"');
    expect(p).toContain("font-variant-numeric:tabular-nums");
  });
});

describe("firstPrompt", () => {
  const user = (content: any) => JSON.stringify({ type: "user", message: { role: "user", content } });
  it("returns the first user message, string or text blocks, skipping noise", () => {
    expect(firstPrompt(["not json", JSON.stringify({ type: "summary" }), user("Which signer?"), user("later")])).toBe("Which signer?");
    expect(firstPrompt([user([{ type: "text", text: "Read the packet" }])])).toBe("Read the packet");
    expect(firstPrompt([JSON.stringify({ type: "assistant", message: { content: [] } })])).toBe("");
  });
});

describe("answers from memory", () => {
  it("counts tool calls and calls a run with none an answer from memory", () => {
    const asst = (...blocks: any[]) => JSON.stringify({ type: "assistant", message: { content: blocks } });
    const none = [asst({ type: "text", text: "It is the last signer." })];
    const some = [asst({ type: "tool_use", name: "Bash", input: { command: "cat src/a.py" } }, { type: "tool_use", name: "Read", input: { file_path: "src/b.py" } })];
    expect(summarizeStream(none, "sonnet").toolCalls).toBe(0);
    expect(summarizeStream(some, "sonnet").toolCalls).toBe(2);
  });
});

describe("proof commands", () => {
  it("fail on the first failing line of a multi-line proof", () => {
    expect(sh("false\ntrue", tmpdir())).not.toBe(0);
    expect(sh("true\ntrue", tmpdir())).toBe(0);
  });
});

describe("verdictFromText", () => {
  it("takes a verdict the judge returned instead of writing", () => {
    const v = { A: { achieved: "yes" }, B: { achieved: "yes" }, preferred: "B", preferred_why: "cheaper" };
    expect(verdictFromText("Here it is:\n```json\n" + JSON.stringify(v) + "\n```\nDone.")).toEqual(v);
    expect(verdictFromText(JSON.stringify(v))).toEqual(v);
    expect(verdictFromText("```json\n{\"note\": 1}\n```")).toBeNull();
    expect(verdictFromText("no json here")).toBeNull();
  });
});

describe("porcelainPaths", () => {
  it("keeps the whole path when the first line's leading space was trimmed", () => {
    expect(porcelainPaths("M docs/a.json\n M docs/b.md\n?? new.txt\nA  tests/t.py")).toEqual(["docs/a.json", "docs/b.md", "new.txt", "tests/t.py"]);
  });
});

describe("transcriptFolder", () => {
  it("dashes every non-alphanumeric character, underscores included", () => {
    expect(transcriptFolder("/home/dev/my_multi_repo/.harnesskit/proof/01-x/before")).toBe("-home-dev-my-multi-repo--harnesskit-proof-01-x-before");
  });
});
