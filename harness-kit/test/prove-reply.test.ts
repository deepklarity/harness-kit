import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { asksPerson, parseScenario, runProve } from "../src/prove.js";

const scenario = (reply: string | null) => `# Narrow the export

Tier: mid
Difficulty: mid
Kind: outcome
Exercises: a CLAUDE.md rule; a bare agent exports everything

## The prompt

Export the records.

## The proof

\`\`\`
test -f README.md
\`\`\`
${reply === null ? "" : `\n## If the agent asks\n\n${reply}\n`}
## A correct answer must mention

- the narrowed export
`;

describe("parseScenario reply", () => {
  it("reads the reply verbatim, or null when absent", () => {
    expect(parseScenario(scenario("Only the March ones."), "x.md").reply).toBe("Only the March ones.");
    expect(parseScenario(scenario(null), "x.md").reply).toBeNull();
  });
});

describe("asksPerson", () => {
  it("is true for a question to the person, false for a plain statement", () => {
    expect(asksPerson("Done.\n\nWhich month do you mean?")).toBe(true);
    expect(asksPerson("I need one thing: which month?\n\nThen I will go ahead.")).toBe(true);
    expect(asksPerson("Exported 3 rows. Why it works is in the log.")).toBe(false);
    expect(asksPerson("")).toBe(false);
  });
});

describe("scripted reply with a fake claude", () => {
  let root: string, repo: string, oldPath: string, baseline: string, calls: string;
  const git = (...a: string[]) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8" }).trim();
  const go = async (file: string, extra: string[] = []) => {
    const out: string[] = [];
    const code = await runProve([file, "--runner", "headless", "--baseline", baseline, "--budget", "20", ...extra], { cwd: repo, log: (s) => out.push(s) });
    return { code, text: out.join("\n") };
  };

  beforeAll(() => {
    root = realpathSync(mkdtempSync(join(tmpdir(), "prove-reply-")));
    const bin = join(root, "bin");
    mkdirSync(bin);
    calls = join(root, "calls.log");
    writeFileSync(
      join(bin, "claude"),
      `#!/usr/bin/env node
const fs = require("fs");
const a = process.argv.slice(2);
fs.appendFileSync(${JSON.stringify(calls)}, a.join(" ") + "\\n");
const resumed = a.includes("--resume");
const asks = !process.env.FAKE_NO_ASK;
console.log(JSON.stringify({ type: "system", subtype: "init", session_id: "sess-42" }));
console.log(JSON.stringify({ type: "assistant", session_id: "sess-42", message: { id: resumed ? "m2" : "m1", content: [
  { type: "tool_use", name: "Read", input: { file_path: process.cwd() + (resumed ? "/docs/two.md" : "/README.md") } } ] } }));
console.log(JSON.stringify({ type: "result", session_id: "sess-42", total_cost_usd: 0.5, num_turns: 2, duration_ms: 2000,
  result: resumed ? "Exported only: " + a[a.length - 1] : asks ? "Which month do you mean?" : "Exported everything.",
  usage: { input_tokens: 10, output_tokens: 20 } }));
`,
    );
    chmodSync(join(bin, "claude"), 0o755);
    const herdr = join(bin, "herdr");
    writeFileSync(herdr, `#!/bin/sh\necho "$@" >> ${JSON.stringify(join(root, "herdr.log"))}\n`);
    chmodSync(herdr, 0o755);
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
    writeFileSync(join(repo, "AFTER"), "x\n");
    git("add", "-A");
    git("commit", "-qm", "harness");
    writeFileSync(join(root, "narrow-export.md"), scenario("Only the March ones."));
    writeFileSync(join(root, "no-reply.md"), scenario(null));
  });
  afterAll(() => {
    process.env.PATH = oldPath;
  });

  it("resumes the session once with the reply and sums both legs", async () => {
    const r = await go(join(root, "narrow-export.md"));
    expect(r.code).toBe(0);
    const dir = join(repo, "writeup/proof/narrow-export");
    const rec = JSON.parse(readFileSync(join(dir, "after.json"), "utf8"));
    expect(rec.replied).toBe(true);
    expect(rec.cost_usd).toBe(1);
    expect(rec.turns).toBe(4);
    expect(rec.tokens).toBe(60);
    expect(rec.seconds).toBe(4);
    expect(rec.files_opened).toEqual(["README.md", "docs/two.md"]);
    expect(readFileSync(join(dir, "after.md"), "utf8")).toContain("Exported only: Only the March ones.");
    const log = readFileSync(calls, "utf8").trim().split("\n");
    const resumes = log.filter((l) => l.includes("--resume sess-42"));
    expect(resumes).toHaveLength(2); // one per half, once each
    for (const l of resumes) expect(l).toMatch(/--model sonnet --effort medium --permission-mode auto --output-format stream-json/);
    expect(readFileSync(join(dir, "judge.md"), "utf8")).toContain("Only the March ones.");
  });

  it("does not resume a scenario without a reply, or an answer that asks nothing", async () => {
    writeFileSync(calls, "");
    const none = await go(join(root, "no-reply.md"));
    expect(none.code).toBe(0);
    expect(readFileSync(calls, "utf8")).not.toContain("--resume");
    expect(JSON.parse(readFileSync(join(repo, "writeup/proof/no-reply/after.json"), "utf8")).replied).toBeFalsy();
    process.env.FAKE_NO_ASK = "1";
    try {
      writeFileSync(calls, "");
      expect((await go(join(root, "narrow-export.md"))).code).toBe(0);
      expect(readFileSync(calls, "utf8")).not.toContain("--resume");
    } finally {
      delete process.env.FAKE_NO_ASK;
    }
  });

  it("prove reply sends the recorded reply to both panes", async () => {
    const dir = join(repo, "writeup/proof/herdr-one");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "panes.json"), JSON.stringify({ model: "sonnet", reply: "Only the March ones.", panes: { before: { pane: "p1" }, after: { pane: "p2" } } }));
    const out: string[] = [];
    expect(await runProve(["reply", "herdr-one"], { cwd: repo, log: (s) => out.push(s) })).toBe(0);
    const log = readFileSync(join(root, "herdr.log"), "utf8");
    expect(log).toContain("pane send-text p1 Only the March ones.");
    expect(log).toContain("pane send-text p2 Only the March ones.");
    expect(log.match(/send-keys p\d Enter/g)).toHaveLength(2);
    writeFileSync(join(dir, "panes.json"), JSON.stringify({ model: "sonnet", reply: null, panes: {} }));
    expect(await runProve(["reply", "herdr-one"], { cwd: repo, log: (s) => out.push(s) })).toBe(1);
    expect(existsSync(dir)).toBe(true);
  });
});
