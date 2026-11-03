import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.js";

const git = (d: string, ...a: string[]) => execFileSync("git", ["-C", d, ...a], { encoding: "utf8" }).trim();

async function cli(argv: string[]) {
  const out: string[] = [];
  const write = process.stdout.write;
  process.stdout.write = ((s: string) => (out.push(String(s)), true)) as typeof process.stdout.write;
  try {
    return { code: await main(argv), out: out.join("") };
  } finally {
    process.stdout.write = write;
  }
}

describe("init, end to end through the CLI", () => {
  const repo = () => {
    const d = mkdtempSync(join(tmpdir(), "hk-e2e-"));
    execFileSync("git", ["init", "-q", d]);
    git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base");
    return d;
  };
  const brief = (d: string) => existsSync(join(d, "docs/harnesskit/MERGE_CLAUDE_MD.md"));

  it("adds seeded steps a kit upgrade introduced to an existing board, leaving its rows alone", async () => {
    const d = repo();
    await cli(["init", "--target", d]);
    const board = join(d, "writeup/agent-readiness/board.csv");
    const lines = readFileSync(board, "utf8").trimEnd().split("\n");
    const kept = lines.filter((l) => !l.startsWith("t-harness-audit,")).map((l) => (l.startsWith("t-survey,") ? l.replace(",open,", ",done,") : l));
    writeFileSync(board, kept.join("\n") + "\n");
    await cli(["init", "--target", d]);
    const after = readFileSync(board, "utf8");
    expect(after.match(/^t-harness-audit,/m)).not.toBeNull();
    expect(after).toMatch(/^t-survey,[^\n]*,done,/m);
    expect(after.match(/^t-survey,/gm)).toHaveLength(1);
  });

  it("never writes the merge brief on a re-run once setup has filled CLAUDE.md", async () => {
    const d = repo();
    await cli(["init", "--target", d]);
    expect(brief(d)).toBe(false);
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).toContain("claude_md_before no");
    writeFileSync(join(d, "CLAUDE.md"), "# Filled by setup\n\nNo slots left.\n");
    await cli(["init", "--target", d]);
    expect(brief(d)).toBe(false);
    expect(readFileSync(join(d, "CLAUDE.md"), "utf8")).toBe("# Filled by setup\n\nNo slots left.\n");
  });

  it("writes the merge brief when the workspace brought its own CLAUDE.md, and keeps it on re-run", async () => {
    const d = repo();
    writeFileSync(join(d, "CLAUDE.md"), "# Ours\n");
    await cli(["init", "--target", d]);
    expect(brief(d)).toBe(true);
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).toContain("claude_md_before yes");
    expect(readFileSync(join(d, "CLAUDE.md"), "utf8")).toBe("# Ours\n");
    await cli(["init", "--target", d]);
    expect(brief(d)).toBe(true);
  });

  it("records the baseline, excludes .harnesskit, reports drift in a workflow copy and exits 1 on --check", async () => {
    const d = mkdtempSync(join(tmpdir(), "hk-e2e-"));
    execFileSync("git", ["init", "-q", d]);
    git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base");
    const head = git(d, "rev-parse", "HEAD");

    const first = await cli(["init", "--target", d]);
    expect(first.code).toBe(0);
    expect(first.out.trimEnd().split("\n").length).toBeLessThan(24);
    expect(first.out).toMatch(/\d+ written/);
    expect(first.out).toContain('claude "Set up this repo with harnesskit - follow docs/harnesskit/entry.md"');
    expect(first.out).not.toMatch(/slot/);

    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).toContain(`baseline_commit ${head}`);
    expect(readFileSync(join(d, ".git/info/exclude"), "utf8").split("\n")).toContain(".harnesskit/");
    expect(readFileSync(join(d, "docs/harnesskit/workflow/.claude/hooks/protected.txt"), "utf8")).toBe("");

    // Re-running after a new commit keeps the first baseline and the single exclude line.
    git(d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "later");
    await cli(["init", "--target", d]);
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).toContain(`baseline_commit ${head}`);
    expect(readFileSync(join(d, ".git/info/exclude"), "utf8").split("\n").filter((l) => l === ".harnesskit/")).toHaveLength(1);
    expect((await cli(["init", "--check", "--target", d])).code).toBe(0);

    // An edit to a workflow copy is drift like any kit file: reported, left alone.
    const guard = "docs/harnesskit/workflow/.claude/hooks/guard.mjs";
    writeFileSync(join(d, guard), "// mine\n");
    const again = await cli(["init", "--target", d]);
    expect(again.out).toMatch(/1 drifted/);
    expect(again.out).toContain(guard);
    expect(readFileSync(join(d, guard), "utf8")).toBe("// mine\n");
    expect((await cli(["init", "--check", "--target", d])).code).toBe(1);
  });

  it("records a null baseline when the workspace has no commits", async () => {
    const d = mkdtempSync(join(tmpdir(), "hk-e2e-"));
    execFileSync("git", ["init", "-q", d]);
    await cli(["init", "--target", d]);
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).toContain("baseline_commit null");
  });
});
