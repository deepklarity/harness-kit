import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { copyKit } from "../src/copy.js";
import { runProve } from "../src/prove.js";

const SCENARIO = `# Nested base

Tier: strong
Difficulty: complex
Kind: feature
Bills: no
Exercises: nested baseline

## The prompt

Look around.

## Suite

\`\`\`sh
true
\`\`\`

## The proof

\`\`\`sh
true
\`\`\`
`;

describe("prove starts the before half's nested repos at the sha recorded at init", () => {
  let root: string, ws: string, oldPath: string, scenario: string, kit: string, a: string, b: string;
  const g = (cwd: string, ...x: string[]) => execFileSync("git", ["-C", cwd, ...x], { encoding: "utf8" }).trim();
  const wt = (h: string, rel: string) => join(ws, ".harnesskit/proof/nested-base", h, rel);
  const manifest = () => readFileSync(join(ws, "docs/harnesskit/manifest"), "utf8");
  const run = async (...extra: string[]) => {
    const out: string[] = [];
    const code = await runProve([scenario, "--runner", "headless", "--budget", "10", ...extra], { cwd: ws, log: (s) => out.push(s) });
    return { code, text: out.join("\n") };
  };
  const initRepo = (dir: string) => {
    mkdirSync(dir, { recursive: true });
    g(dir, "init", "-q");
    g(dir, "config", "user.email", "t@t");
    g(dir, "config", "user.name", "t");
  };

  beforeAll(() => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "prove-nbase-")));
    const bin = join(root, "bin");
    mkdirSync(bin);
    writeFileSync(join(bin, "claude"), `#!/usr/bin/env node\nconsole.log(JSON.stringify({ type: "result", subtype: "success", total_cost_usd: 0.5, num_turns: 1, duration_ms: 1000, result: "done", usage: { input_tokens: 1, output_tokens: 1 } }));\n`);
    chmodSync(join(bin, "claude"), 0o755);
    oldPath = process.env.PATH ?? "";
    process.env.PATH = `${bin}:${oldPath}`;
    kit = join(root, "kit");
    mkdirSync(kit);
    writeFileSync(join(kit, "entry.md"), "# Entry\n");
    mkdirSync(join(kit, "skills"));
    writeFileSync(join(kit, "skills/skills.csv"), "name,category,tier,reason\n");
    ws = join(root, "ws");
    initRepo(ws);
    writeFileSync(join(ws, ".gitignore"), ".harnesskit/\nwriteup/\n");
    g(ws, "add", "-A");
    g(ws, "commit", "-qm", "base");
    a = join(ws, "api");
    initRepo(a);
    writeFileSync(join(a, "app.txt"), "app\n");
    g(a, "add", "-A");
    g(a, "commit", "-qm", "A");
    scenario = join(root, "nested-base.md");
    writeFileSync(scenario, SCENARIO);
  });
  afterAll(() => {
    process.env.PATH = oldPath;
  });

  it("init records the nested HEAD, and a re-init after a nested commit keeps it", () => {
    copyKit(kit, ws, { version: "1" });
    b = g(a, "rev-parse", "HEAD");
    expect(manifest()).toContain(`nested_baseline api ${b}`);
    writeFileSync(join(a, "harness-test.txt"), "added by setup\n");
    g(a, "add", "-A");
    g(a, "commit", "-qm", "B");
    copyKit(kit, ws, { version: "1" });
    expect(manifest()).toContain(`nested_baseline api ${b}`);
    expect(manifest().match(/nested_baseline api/g)).toHaveLength(1);
    g(ws, "add", "docs");
    g(ws, "commit", "-qm", "init");
  });

  it("a dry run prints both shas; the before half lacks the nested commit, the after half has it", async () => {
    const head = g(a, "rev-parse", "HEAD");
    const dry = await run("--dry-run", "--baseline", g(ws, "rev-parse", "HEAD~1"));
    expect(dry.text).toContain(`nested repo api: before at ${b.slice(0, 12)}, after at ${head.slice(0, 12)}`);
    const r = await run("--baseline", g(ws, "rev-parse", "HEAD~1"));
    expect(r.code, r.text).toBe(0);
    expect(existsSync(wt("before", "api/app.txt"))).toBe(true);
    expect(existsSync(wt("before", "api/harness-test.txt"))).toBe(false);
    expect(existsSync(wt("after", "api/harness-test.txt"))).toBe(true);
  });

  it("a nested repo that appears later is recorded on the next init, at its HEAD then", () => {
    const w = join(ws, "web");
    initRepo(w);
    g(w, "commit", "-q", "--allow-empty", "-m", "W");
    copyKit(kit, ws, { version: "1" });
    expect(manifest()).toContain(`nested_baseline web ${g(w, "rev-parse", "HEAD")}`);
    expect(manifest()).toContain(`nested_baseline api ${b}`);
  });

  it("a manifest with no nested lines warns and falls back to the current HEAD", async () => {
    writeFileSync(join(ws, "docs/harnesskit/manifest"), manifest().split("\n").filter((l) => !l.startsWith("nested_baseline")).join("\n"));
    g(ws, "add", "docs");
    g(ws, "commit", "-qm", "strip");
    const r = await run("--dry-run", "--baseline", g(ws, "rev-parse", "HEAD~1"));
    expect(r.text).toMatch(/warning: no nested_baseline for api/);
    const head = g(a, "rev-parse", "HEAD").slice(0, 12);
    expect(r.text).toContain(`nested repo api: before at ${head}, after at ${head}`);
  });
});
