import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { runInit } from "../src/init.js";

const kitDir = resolve(dirname(fileURLToPath(import.meta.url)), "../kit");
const repo = () => {
  const d = mkdtempSync(join(tmpdir(), "hk-init-"));
  execFileSync("git", ["init", "-q", d]);
  return d;
};

describe("runInit", () => {
  it("works in a folder that is not a git repo, says measuring needs git, and records the baseline once there is a commit", () => {
    const d = mkdtempSync(join(tmpdir(), "hk-nogit-"));
    const first = runInit({ target: d, check: false, kitDir });
    expect(first.code).toBe(0);
    expect(existsSync(join(d, "docs/harnesskit/entry.md"))).toBe(true);
    expect(existsSync(join(d, "writeup/agent-readiness/board.csv"))).toBe(true);
    expect(existsSync(join(d, ".git"))).toBe(false);
    expect(first.output).toMatch(/not a git repository/);
    expect(first.output).toContain('claude "Set up this repo with harness-kit');
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).not.toMatch(/^baseline_commit [0-9a-f]/m);

    execFileSync("git", ["init", "-q", d]);
    execFileSync("git", ["-C", d, "add", "-A"]);
    execFileSync("git", ["-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "before"]);
    const head = execFileSync("git", ["-C", d, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    const second = runInit({ target: d, check: false, kitDir });
    expect(second.output).not.toMatch(/not a git repository/);
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).toContain(`baseline_commit ${head}`);
    expect(readFileSync(join(d, ".git/info/exclude"), "utf8")).toContain(".harnesskit/");
  });

  it("sets a workspace up, is idempotent, leaves edited kit files, and --check exits 1 on drift", () => {
    const d = repo();
    const first = runInit({ target: d, check: false, kitDir });
    expect(first.code).toBe(0);
    for (const p of [
      "docs/harnesskit/entry.md",
      "docs/skills/skills.csv",
      "docs/patterns/_INDEX.md",
      "docs/harnesskit/workflow/.claude/skills/hk-rca/SKILL.md",
      "docs/harnesskit/workflow/.claude/hooks/guard.mjs",
      "docs/harnesskit/workflow/.claude/settings.hook.json",
      "docs/harnesskit/manifest",
    ]) {
      expect(existsSync(join(d, p)), p).toBe(true);
    }
    expect(first.output).toContain("docs/harnesskit/entry.md");
    for (const p of ["CLAUDE.md", "AGENTS.md", ".claude", ".gitignore"]) expect(existsSync(join(d, p)), p).toBe(false);

    const second = runInit({ target: d, check: false, kitDir });
    expect(second.code).toBe(0);
    expect(second.report.written).toEqual([]);
    expect(second.report.upgraded).toEqual([]);
    expect(second.report.drifted).toEqual([]);

    const edited = join(d, "docs/playbook/tenets.md");
    writeFileSync(edited, "mine\n");
    const third = runInit({ target: d, check: false, kitDir });
    expect(third.report.drifted).toContain("docs/playbook/tenets.md");
    expect(third.output).toContain("docs/playbook/tenets.md");
    expect(readFileSync(edited, "utf8")).toBe("mine\n");

    const manifestBefore = readFileSync(join(d, "docs/harnesskit/manifest"), "utf8");
    const chk = runInit({ target: d, check: true, kitDir });
    expect(chk.code).toBe(1);
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).toBe(manifestBefore);
  });

  it("--check on a fresh repo writes nothing", () => {
    const d = repo();
    const r = runInit({ target: d, check: true, kitDir });
    expect(r.code).toBe(0);
    expect(existsSync(join(d, "CLAUDE.md"))).toBe(false);
    expect(existsSync(join(d, "docs/harnesskit/manifest"))).toBe(false);
  });

  it("seeds writeup/agent-readiness with no template slot left and the rows on the page", () => {
    const d = repo();
    runInit({ target: d, check: false, kitDir });
    const dir = join(d, "writeup/agent-readiness");
    const files = readdirSync(dir);
    for (const f of ["RESUME.md", "board.csv", "mission-control.html", "log.md"]) {
      expect(files, f).toContain(f);
    }
    expect(existsSync(join(d, "docs/harnesskit/templates/CLAUDE.md"))).toBe(true);
    expect(files).not.toContain("render-board.mjs");
    expect(existsSync(join(d, "docs/harnesskit/templates/mission-control/render-board.mjs"))).toBe(true);
    expect(readFileSync(join(dir, "mission-control.html"), "utf8")).toContain("docs/harnesskit/templates/mission-control/render-board.mjs");
    expect(files).not.toContain("render_board.py");
    for (const f of files) expect(readFileSync(join(dir, f), "utf8"), f).not.toContain("{{");
    const html = readFileSync(join(dir, "mission-control.html"), "utf8");
    const csv = readFileSync(join(dir, "board.csv"), "utf8");
    for (const id of ["t-survey", "t-guard", "t-checklist", "t-entrypoint", "t-decisions", "t-board"]) {
      expect(csv).toContain(id + ",task,");
    }
    expect(html).toContain("Look around the workspace");
    expect(html).toContain("agent-readiness");
    expect(html).toContain("Setting up harness-kit in this workspace: nothing assessed yet");
    expect(readFileSync(join(dir, "RESUME.md"), "utf8")).toContain("Look around the workspace");
    expect(html).toContain('<a href="board.csv">board.csv</a>');
    expect(html).toContain("<p><strong>Next for me:</strong> Look around the workspace and list each repo, then the rest of the board in order.</p>");
    expect(html).not.toContain("After scaffolding");
    const check = spawnSync("node", [join(kitDir, "skills/audits/hk-surface-design/scripts/check_surface.mjs"), join(dir, "mission-control.html")], { encoding: "utf8" });
    // the page is deliberately always light, so the theme items do not apply
    expect(check.stdout.split("\n").filter((l) => /^\d+-/.test(l) && !l.startsWith("12-theme"))).toEqual([]);
    expect(readFileSync(join(d, "docs/harnesskit/manifest"), "utf8")).not.toMatch(/  writeup\//);
  });

  it("does not touch an existing writeup/agent-readiness", () => {
    const d = repo();
    mkdirSync(join(d, "writeup/agent-readiness"), { recursive: true });
    writeFileSync(join(d, "writeup/agent-readiness/RESUME.md"), "mine\n");
    runInit({ target: d, check: false, kitDir });
    expect(readdirSync(join(d, "writeup/agent-readiness"))).toEqual(["RESUME.md"]);
  });

  it("keeps the hooks README as a workflow copy, not under .claude", () => {
    const d = repo();
    runInit({ target: d, check: false, kitDir });
    expect(existsSync(join(d, "docs/harnesskit/workflow/.claude/hooks/README.md"))).toBe(true);
    expect(existsSync(join(d, ".claude"))).toBe(false);
  });

  it("excludes .harnesskit/ through .git/info/exclude, once, and writes no .gitignore", () => {
    const d = repo();
    runInit({ target: d, check: false, kitDir });
    runInit({ target: d, check: false, kitDir });
    expect(readFileSync(join(d, ".git/info/exclude"), "utf8").split("\n").filter((l) => l === ".harnesskit/")).toHaveLength(1);
    expect(existsSync(join(d, ".gitignore"))).toBe(false);
  });
});
