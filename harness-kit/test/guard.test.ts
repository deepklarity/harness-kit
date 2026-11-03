import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

const SRC = "kit/templates/hooks/guard.mjs";
let hook: string;

beforeAll(() => {
  // The hook ships unedited; the protected folders sit beside it in protected.txt.
  const dir = mkdtempSync(join(tmpdir(), "guard-"));
  hook = join(dir, "guard.mjs");
  writeFileSync(hook, readFileSync(SRC, "utf8"));
  writeFileSync(join(dir, "protected.txt"), "some-repo\n\n  other_repo  \n");
});

function bash(command: string, cwd = "/tmp") {
  const r = spawnSync("node", [hook], {
    input: JSON.stringify({ tool_name: "Bash", tool_input: { command }, cwd }),
    encoding: "utf8",
  });
  return { code: r.status, err: r.stderr };
}

describe("guard.mjs bash check", () => {
  it("lets a heredoc naming a repo next to rm go through", () => {
    const cmd =
      "python3 - <<'EOF'\nrows.append('rm the venv under some-repo')\n" +
      "open('writeup/board.csv','a').write(','.join(rows))\nEOF";
    expect(bash(cmd).code).toBe(0);
  });

  it("catches a repo after the verb in the same command", () => {
    const a = bash("rm -rf some-repo/.venv");
    expect(a.code).toBe(2);
    expect(a.err).toContain("blocked by .claude/hooks/guard.mjs");
    expect(a.err).toContain("some-repo/");
    expect(bash("cp a.txt other_repo/b.txt && ls").code).toBe(2);
  });

  it("does not catch a repo in another simple command", () => {
    expect(bash("ls some-repo; rm -f writeup/out/x").code).toBe(0);
    expect(bash("grep -r foo other_repo | tee writeup/out/hits").code).toBe(0);
  });

  it("does not read an arrow in a grep pattern as a redirect, but catches real ones", () => {
    expect(bash('grep -n "==> some-repo" logs/x.log').code).toBe(0);
    expect(bash("echo hi > some-repo/x").code).toBe(2);
    expect(bash("echo hi >> some-repo/x").code).toBe(2);
  });

  it("does not block git stash quoted in a heredoc, but blocks the real one", () => {
    const ok = "cat > writeup/x.md <<'EOF'\nNever run git stash in a shared checkout.\nEOF";
    expect(bash(ok).code).toBe(0);
    const bad = bash("git stash");
    expect(bad.code).toBe(2);
    expect(bad.err).toContain("git stash");
  });

  it("blocks reset --hard and force push", () => {
    expect(bash("git reset --hard HEAD~1").code).toBe(2);
    expect(bash("git push origin main --force").code).toBe(2);
  });
});

describe("guard.mjs edit check and input handling", () => {
  function edit(tool_name: string, file_path: string) {
    const r = spawnSync("node", [hook], {
      input: JSON.stringify({ tool_name, tool_input: { file_path }, cwd: "/tmp/ws" }),
      encoding: "utf8",
    });
    return r.status;
  }

  it("blocks an edit tool inside a protected repo, relative or absolute", () => {
    expect(edit("Edit", "some-repo/a.py")).toBe(2);
    expect(edit("Write", "/tmp/ws/other_repo/b.py")).toBe(2);
  });

  it("allows edits elsewhere, including a sibling that shares a prefix", () => {
    expect(edit("Edit", "writeup/a.md")).toBe(0);
    expect(edit("Edit", "some-repo-two/a.md")).toBe(0);
  });

  it("exits 0 on invalid or empty stdin", () => {
    for (const input of ["", "not json"]) {
      expect(spawnSync("node", [hook], { input, encoding: "utf8" }).status).toBe(0);
    }
  });
});

describe("guard.mjs protected.txt", () => {
  it("protects nothing when protected.txt is absent", () => {
    const dir = mkdtempSync(join(tmpdir(), "guard-"));
    const h = join(dir, "guard.mjs");
    writeFileSync(h, readFileSync(SRC, "utf8"));
    const r = spawnSync("node", [h], {
      input: JSON.stringify({ tool_name: "Bash", tool_input: { command: "rm -rf some-repo/x" }, cwd: "/tmp" }),
      encoding: "utf8",
    });
    expect(r.status).toBe(0);
  });
});
