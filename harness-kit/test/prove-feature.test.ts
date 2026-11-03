import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { parseScenario, renderPage, runProve , hiddenFolder } from "../src/prove.js";

const FEATURE = `# Add a feature file

Tier: strong
Difficulty: complex
Kind: feature
Bills: no
Exercises: the verify gate

## The prompt

Please add the feature file so the report works.

## Hidden tests

writeup/scenarios/hidden/feat/

## Suite

\`\`\`sh
test ! -f BROKEN
\`\`\`

## Lint

\`\`\`sh
test -f feature.txt
\`\`\`

## The proof

\`\`\`sh
sh tests/hidden/check.sh
\`\`\`
`;

describe("feature scenario parser", () => {
  it("exposes hiddenDir, suite and lint, and needs a proof", () => {
    const s = parseScenario(FEATURE, "feat.md");
    expect(s.kind).toBe("feature");
    expect(s.hiddenDir).toBe("writeup/scenarios/hidden/feat");
    expect(s.suite).toBe("test ! -f BROKEN");
    expect(s.lint).toBe("test -f feature.txt");
    expect(parseScenario("# Q\n\n## The prompt\n\nhi\n", "q.md")).toMatchObject({ hiddenDir: null, suite: null, lint: null });
    expect(() => parseScenario("# Q\nKind: feature\n\n## The prompt\n\nhi\n", "q.md")).toThrow(/proof/);
  });
  it("takes a bare folder name as a folder under writeup/scenarios/hidden", () => {
    const s = parseScenario(FEATURE.replace("writeup/scenarios/hidden/feat/", "`feat`"), "feat.md");
    expect(s.hiddenDir).toBe("writeup/scenarios/hidden/feat");
  });
});

describe("feature run with a fake claude", () => {
  let root: string, repo: string, oldPath: string, baseline: string;
  const git = (...a: string[]) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8" }).trim();
  const slug = "feat";

  beforeAll(async () => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "prove-feat-")));
    const bin = join(root, "bin");
    mkdirSync(bin);
    writeFileSync(
      join(bin, "claude"),
      `#!/usr/bin/env node
const fs = require("fs");
// the harness half (it has HARNESS) builds the feature
if (fs.existsSync("HARNESS")) fs.writeFileSync("feature.txt", "built\\n");
console.log(JSON.stringify({ type: "result", subtype: "success", total_cost_usd: 0.5, num_turns: 1, duration_ms: 1000, result: "done", usage: { input_tokens: 1, output_tokens: 1 } }));
`,
    );
    chmodSync(join(bin, "claude"), 0o755);
    oldPath = process.env.PATH ?? "";
    process.env.PATH = `${bin}:${oldPath}`;
    repo = join(root, "ws");
    mkdirSync(repo);
    git("init", "-q");
    git("config", "user.email", "t@t");
    git("config", "user.name", "t");
    writeFileSync(join(repo, "README.md"), "hi\n");
    writeFileSync(join(repo, ".gitignore"), ".harnesskit/\nwriteup/\n");
    git("add", "-A");
    git("commit", "-qm", "baseline");
    baseline = git("rev-parse", "HEAD");
    writeFileSync(join(repo, "HARNESS"), "x\n");
    git("add", "-A");
    git("commit", "-qm", "harness");
    mkdirSync(join(repo, "writeup/scenarios/hidden/feat/tests/hidden"), { recursive: true });
    writeFileSync(join(repo, "writeup/scenarios/hidden/feat/tests/hidden/check.sh"), "test -f feature.txt\n");
    writeFileSync(join(repo, "writeup/scenarios/feat.md"), FEATURE);
    const out: string[] = [];
    const code = await runProve([join(repo, "writeup/scenarios/feat.md"), "--runner", "headless", "--baseline", baseline, "--budget", "10"], { cwd: repo, log: (s) => out.push(s) });
    expect(out.join("\n")).toBe(out.join("\n"));
    expect(code, out.join("\n")).toBe(0);
  });
  afterAll(() => {
    process.env.PATH = oldPath;
  });

  const rec = (h: string) => JSON.parse(readFileSync(join(repo, "writeup/proof", slug, `${h}.json`), "utf8"));

  it("runs the hidden tests, the suite and lint after each half and records the exits", () => {
    expect(rec("before")).toMatchObject({ hidden_exit: 1, suite_exit: 0, lint_exit: 1 });
    expect(rec("after")).toMatchObject({ hidden_exit: 0, suite_exit: 0, lint_exit: 0, proof_exit: 0 });
  });
  it("removes the copied hidden files so a rerun starts clean", () => {
    for (const h of ["before", "after"]) {
      expect(existsSync(join(repo, ".harnesskit/proof", slug, h, "tests/hidden/check.sh"))).toBe(false);
      expect(existsSync(join(repo, ".harnesskit/proof", slug, h, "tests"))).toBe(false);
    }
    expect(existsSync(join(repo, ".harnesskit/proof", slug, "after/feature.txt"))).toBe(true);
  });
  it("puts the facts and the diff in the judge packet", () => {
    const packet = readFileSync(join(repo, "writeup/proof", slug, "judge.md"), "utf8");
    expect(packet).toContain("feature.txt");
    expect(packet).toContain("+built");
    expect(packet).toMatch(/hidden_exit/);
    expect(packet).toMatch(/Hidden tests: (passed|failed)/);
    expect(packet).toMatch(/Suite: (passed|failed)/);
    expect(packet).toContain("quality");
  });
  it("leads the page with hidden tests passed, quality and regressions", () => {
    const dir = join(repo, "writeup/proof", slug);
    const mapping = JSON.parse(readFileSync(join(repo, ".harnesskit/proof", slug, "mapping.json"), "utf8"));
    const q = (n: number) => ({ correctness: n, fits_codebase: n, tests: n, scope: n, verification: n, overall: n, quality_why: `why ${n}` });
    const [bl, al] = mapping.A === "before" ? ["A", "B"] : ["B", "A"];
    const mk = (n: number) => ({ achieved: "yes", process: {}, quality: q(n) });
    writeFileSync(join(dir, "verdict.json"), JSON.stringify({ [bl]: mk(6), [al]: mk(8), preferred: al, preferred_why: "Answer was better" }));
    const p = renderPage(repo);
    const verdict = /<p class="verdict">([^<]*)<\/p>/.exec(p)![1];
    expect(verdict).toMatch(/^Hidden tests passed: baseline 0 of 1, harness 1 of 1; average quality 6\.0 against 8\.0 of 10; regressions 0 against 0/);
    expect(p).toContain("<th>Quality</th>");
    expect(p).toContain("<th>Hidden tests</th>");
    expect(p).toContain("6 &rarr; 8");
    expect(p).toMatch(/Correctness<\/th>/);
    expect(p).toContain('colspan="8"');
  });
});

describe("page without feature scenarios", () => {
  it("keeps the old columns", () => {
    const ws = realpathSync(mkdtempSync(join(tmpdir(), "page-nofeat-")));
    expect(renderPage(ws)).not.toContain("<th>Quality</th>");
    rmSync(ws, { recursive: true, force: true });
  });
});

describe("hiddenFolder", () => {
  it("reads the folder name inside a code fence, never the fence", () => {
    expect(hiddenFolder("```\nrole-lookup\n```")).toBe("writeup/scenarios/hidden/role-lookup");
    expect(hiddenFolder("`role-lookup`")).toBe("writeup/scenarios/hidden/role-lookup");
    expect(hiddenFolder("custom/dir/")).toBe("custom/dir");
    expect(hiddenFolder("```\n```")).toBeNull();
  });
});
