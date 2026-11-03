import { spawnSync } from "node:child_process";
import { appendFileSync, cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "../kit/templates/scripts/check_claude_md.mjs");
// Each fixture is its own workspace: copied out with a .git, so paths resolve from its root.
const run = (fixture: string, draftDir = "") => {
  const ws = mkdtempSync(join(tmpdir(), "claude-md-"));
  cpSync(join(here, "fixtures/claude-md", fixture), ws, { recursive: true });
  mkdirSync(join(ws, ".git"));
  const draft = join(ws, draftDir, "CLAUDE.md");
  if (draftDir) { mkdirSync(join(ws, draftDir), { recursive: true }); cpSync(join(ws, "CLAUDE.md"), draft); }
  return spawnSync("node", [script, draft], { encoding: "utf8" });
};

describe("check_claude_md", () => {
  it("passes a routing file whose paths exist", () => {
    const r = run("good");
    expect(r.stdout).toContain("passed 13 of 13 checks");
    expect(r.status).toBe(0);
  });

  it("resolves paths from the workspace root when the draft sits in a subfolder", () => {
    expect(run("good", "writeup/agent-readiness").stdout).toContain("passed 13 of 13 checks");
  });

  it("reports each mechanical failure on its own line and exits 1", () => {
    const r = run("bad");
    expect(r.status).toBe(1);
    for (const needle of ["unfilled slot {{setup_doc}}", "banned phrase", 'missing section "Stop and Ask"', "table has 1 rows", "docs/missing/_INDEX.md does not exist", "no 'Breadcrumb-first' rule", "no command in a fenced block", "routing row targets docs/missing/_INDEX.md, which does not exist", "no 'Find the row first' rule"]) {
      expect(r.stdout).toContain(needle);
    }
    expect(r.stdout).toMatch(/passed \d of 13 checks/);
  });

  it("fails a file that carries a setup-time rule", () => {
    const run2 = (line: string) => {
      const ws = mkdtempSync(join(tmpdir(), "claude-md-"));
      cpSync(join(here, "fixtures/claude-md/good"), ws, { recursive: true });
      mkdirSync(join(ws, ".git"));
      appendFileSync(join(ws, "CLAUDE.md"), `\n${line}\n`);
      return spawnSync("node", [script, join(ws, "CLAUDE.md")], { encoding: "utf8" });
    };
    for (const line of ["Agents never touch repos listed in .claude/hooks/protected.txt.", "Do not edit src while harness setup runs."]) {
      const r = run2(line);
      expect(r.status).toBe(1);
      expect(r.stdout).toContain("setup-time rule");
    }
  });

  it("flags an anchor that names no heading", () => {
    const r = spawnSync("node", ["-e", `
      const fs=require("fs"),os=require("os"),p=require("path"),cp=require("child_process");
      const d=fs.mkdtempSync(p.join(os.tmpdir(),"cm-"));
      fs.mkdirSync(p.join(d,"docs"));fs.writeFileSync(p.join(d,"docs/a.md"),"# A\\n\\n## Real\\n");
      fs.writeFileSync(p.join(d,"CLAUDE.md"),"See \`docs/a.md#nope\` and \`docs/a.md#real\`.\\n");
      const r=cp.spawnSync("node",[${JSON.stringify(script)},p.join(d,"CLAUDE.md")],{encoding:"utf8"});
      process.stdout.write(r.stdout);`], { encoding: "utf8" });
    expect(r.stdout).toContain('has no heading "nope"');
    expect(r.stdout).not.toContain("#real");
  });
  it("fails a file over the word cap, and names the count", () => {
    const ws = mkdtempSync(join(tmpdir(), "claude-md-"));
    cpSync(join(here, "fixtures/claude-md/good"), ws, { recursive: true });
    mkdirSync(join(ws, ".git"));
    appendFileSync(join(ws, "CLAUDE.md"), "\n" + "word ".repeat(1600) + "\n");
    const r = spawnSync("node", [script, join(ws, "CLAUDE.md")], { encoding: "utf8" });
    expect(r.status).toBe(1);
    expect(r.stdout).toMatch(/\[at most 1500 words\].*1[67]\d\d words/);
  });

  it("fails a routing row that names no file, and a file that drops the find-the-row rule", () => {
    const ws = mkdtempSync(join(tmpdir(), "claude-md-"));
    cpSync(join(here, "fixtures/claude-md/good"), ws, { recursive: true });
    mkdirSync(join(ws, ".git"));
    const f = join(ws, "CLAUDE.md");
    writeFileSync(f, readFileSync(f, "utf8").replace("| Write a doc |", "| Write a doc | read the tone guide |\n| Other |").replace(/- \*\*Find the row first[^\n]*\n/, ""));
    const r = spawnSync("node", [script, f], { encoding: "utf8" });
    expect(r.stdout).toContain("routing row names no file");
    expect(r.stdout).toContain("no 'Find the row first' rule");
  });

  it("fails a routing table that leaves out an entry index the workspace has", () => {
    const ws = mkdtempSync(join(tmpdir(), "claude-md-"));
    cpSync(join(here, "fixtures/claude-md/good"), ws, { recursive: true });
    mkdirSync(join(ws, ".git"));
    writeFileSync(join(ws, "scripts/README.md"), "# Tools\n");
    writeFileSync(join(ws, "docs/_INDEX.md"), "# Docs\n");
    const r = spawnSync("node", [script, join(ws, "CLAUDE.md")], { encoding: "utf8" });
    expect(r.status).toBe(1);
    expect(r.stdout).toContain("does not name the entry index scripts/README.md");
    expect(r.stdout).toContain("does not name the entry index docs/_INDEX.md");
    expect(r.stdout).not.toContain("entry index docs/breadcrumb_analysis/_INDEX.md");
  });
});
