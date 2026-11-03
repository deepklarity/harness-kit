import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { runProve } from "../src/prove.js";

const SCENARIO = `# Nested code

Tier: strong
Difficulty: complex
Kind: feature
Bills: no
Exercises: nested repos

## The prompt

Edit the api code.

## Suite

\`\`\`sh
test -f api/src/app.txt
\`\`\`

## The proof

\`\`\`sh
test -f api/src/app.txt
\`\`\`
`;

describe("prove in a multi-repo workspace", () => {
  let root: string, ws: string, oldPath: string, baseline: string, scenario: string;
  const g = (cwd: string, ...a: string[]) => execFileSync("git", ["-C", cwd, ...a], { encoding: "utf8" }).trim();
  const slug = "nested-code";
  const wt = (h: string, rel = "") => join(ws, ".harnesskit/proof", slug, h, rel);
  const run = async (...extra: string[]) => {
    const out: string[] = [];
    const code = await runProve([scenario, "--runner", "headless", "--baseline", baseline, "--budget", "10", ...extra], { cwd: ws, log: (s) => out.push(s) });
    return { code, text: out.join("\n") };
  };
  const initRepo = (dir: string) => {
    mkdirSync(dir, { recursive: true });
    g(dir, "init", "-q");
    g(dir, "config", "user.email", "t@t");
    g(dir, "config", "user.name", "t");
  };

  beforeAll(() => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "prove-nested-")));
    const bin = join(root, "bin");
    mkdirSync(bin);
    // the harness half (it has HARNESS) edits code inside the nested api worktree
    writeFileSync(
      join(bin, "claude"),
      `#!/usr/bin/env node
const fs = require("fs");
if (fs.existsSync("HARNESS")) fs.writeFileSync("api/src/new.txt", "edited\\n");
console.log(JSON.stringify({ type: "result", subtype: "success", total_cost_usd: 0.5, num_turns: 1, duration_ms: 1000, result: "done", usage: { input_tokens: 1, output_tokens: 1 } }));
`,
    );
    chmodSync(join(bin, "claude"), 0o755);
    oldPath = process.env.PATH ?? "";
    process.env.PATH = `${bin}:${oldPath}`;
    ws = join(root, "ws");
    initRepo(ws);
    writeFileSync(join(ws, ".gitignore"), ".harnesskit/\nwriteup/\n");
    g(ws, "add", "-A");
    g(ws, "commit", "-qm", "empty baseline");
    baseline = g(ws, "rev-parse", "HEAD");
    writeFileSync(join(ws, "HARNESS"), "x\n");
    g(ws, "add", "-A");
    g(ws, "commit", "-qm", "harness");
    initRepo(join(ws, "api"));
    mkdirSync(join(ws, "api/src"));
    writeFileSync(join(ws, "api/src/app.txt"), "app\n");
    g(join(ws, "api"), "add", "-A");
    g(join(ws, "api"), "commit", "-qm", "code");
    initRepo(join(ws, "libs/web"));
    g(join(ws, "libs/web"), "commit", "-q", "--allow-empty", "-m", "empty");
    scenario = join(root, "nested-code.md");
    writeFileSync(scenario, SCENARIO);
  });
  afterAll(() => {
    process.env.PATH = oldPath;
  });

  it("dry run lists the nested repos", async () => {
    const r = await run("--dry-run");
    expect(r.code, r.text).toBe(0);
    expect(r.text).toContain("api");
    expect(r.text).toContain("libs/web");
  });

  it("refuses an uncommitted change inside a nested repo, naming it", async () => {
    writeFileSync(join(ws, "api/src/app.txt"), "dirty\n");
    const r = await run();
    expect(r.code).toBe(1);
    expect(r.text).toContain("api");
    expect(r.text).toMatch(/uncommitted/);
    expect(existsSync(join(ws, ".harnesskit"))).toBe(false);
    g(join(ws, "api"), "checkout", "--", ".");
  });

  it("gives both halves the nested code, only the harness half the root harness file, and diffs nested edits", async () => {
    const r = await run();
    expect(r.code, r.text).toBe(0);
    for (const h of ["before", "after"]) expect(readFileSync(wt(h, "api/src/app.txt"), "utf8")).toBe("app\n");
    expect(existsSync(wt("before", "libs/web/.git"))).toBe(true);
    expect(existsSync(wt("before", "HARNESS"))).toBe(false);
    expect(existsSync(wt("after", "HARNESS"))).toBe(true);
    const diff = readFileSync(join(ws, "writeup/proof", slug, "after.diff"), "utf8");
    expect(diff).toContain("b/api/src/new.txt");
    expect(diff).toContain("+edited");
    expect(readFileSync(join(ws, "writeup/proof", slug, "before.diff"), "utf8")).not.toContain("new.txt");
    expect(JSON.parse(readFileSync(join(ws, "writeup/proof", slug, "after.json"), "utf8")).diff_stat).toContain("api/src/new.txt");
  });

  it("a rerun resets the nested worktrees, and --clean removes every worktree", async () => {
    expect((await run()).code).toBe(0);
    expect(existsSync(wt("after", "api/src/new.txt"))).toBe(true); // the fake agent wrote it again
    const out: string[] = [];
    expect(await runProve(["--clean", slug], { cwd: ws, log: (s) => out.push(s) })).toBe(0);
    expect(existsSync(wt("after"))).toBe(false);
    expect(existsSync(wt("before"))).toBe(false);
    for (const r of [ws, join(ws, "api"), join(ws, "libs/web")]) expect(g(r, "worktree", "list")).not.toContain("harnesskit");
  });
});
