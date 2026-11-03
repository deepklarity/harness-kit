import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "../kit/templates/scripts/clean_run.sh");
const git = (cwd: string, ...a: string[]) => spawnSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", ...a], { cwd, encoding: "utf8" });

describe("clean_run.sh", () => {
  it("runs the command at HEAD, ignores uncommitted edits, and removes the worktree", () => {
    const repo = realpathSync(mkdtempSync(join(tmpdir(), "clean-run-")));
    git(repo, "init", "-q");
    writeFileSync(join(repo, "f.txt"), "committed\n");
    git(repo, "add", ".");
    git(repo, "commit", "-qm", "init");
    writeFileSync(join(repo, "f.txt"), "dirty\n");
    const r = spawnSync("sh", [script, "cat f.txt; pwd > ../clean-run-pwd.txt"], { cwd: repo, encoding: "utf8" });
    expect(r.status).toBe(0);
    expect(r.stdout).toBe("committed\n");
    expect(readFileSync(join(repo, "f.txt"), "utf8")).toBe("dirty\n");
    const wt = readFileSync(join(repo, "../clean-run-pwd.txt"), "utf8").trim();
    expect(existsSync(wt)).toBe(false);
    expect(git(repo, "worktree", "list").stdout.trim().split("\n")).toHaveLength(1);
  });

  it("passes the command's exit status through", () => {
    const repo = realpathSync(mkdtempSync(join(tmpdir(), "clean-run-")));
    git(repo, "init", "-q");
    git(repo, "commit", "-q", "--allow-empty", "-m", "init");
    expect(spawnSync("sh", [script, "exit 3"], { cwd: repo }).status).toBe(3);
  });
});
