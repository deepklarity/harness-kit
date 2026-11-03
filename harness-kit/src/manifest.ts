import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

export const MANIFEST_REL = "docs/harnesskit/manifest";

/** baselineCommit: the workspace HEAD before the first init (null: no commits yet; undefined: a manifest from before it was recorded). */
/** nestedBaselines: each nested repo's HEAD (by relative path) when the manifest first saw it; the baseline half of a prove starts there. */
/** claudeMdBefore: whether the workspace had its own CLAUDE.md before the first init (undefined: not recorded). */
export type Manifest = { version: string; hashes: Map<string, string>; baselineCommit?: string | null; claudeMdBefore?: boolean; nestedBaselines?: Map<string, string>; installSpec?: string };
/** installSpec: how this init was run (an npx package spec, `project` for a project install, or the registry name); the docs/harnesskit/harnesskit wrapper runs the kit that way. */

export const sha256 = (data: Buffer | string): string => createHash("sha256").update(data).digest("hex");

/** Read target/docs/harnesskit/manifest; null when the workspace has none yet. */
export function readManifest(target: string): Manifest | null {
  let text: string;
  try {
    text = readFileSync(join(target, MANIFEST_REL), "utf8");
  } catch {
    return null;
  }
  const [first, ...rest] = text.split("\n");
  const m = /^version (.+)$/.exec(first);
  if (!m) throw new Error(`${MANIFEST_REL} must start with "version <x>", got "${first}"`);
  const hashes = new Map<string, string>();
  let baselineCommit: string | null | undefined;
  let claudeMdBefore: boolean | undefined;
  const nestedBaselines = new Map<string, string>();
  let installSpec: string | undefined;
  for (const line of rest) {
    if (!line.trim()) continue;
    const b = /^baseline_commit (.+)$/.exec(line);
    if (b) {
      baselineCommit = b[1] === "null" ? null : b[1];
      continue;
    }
    const i = /^install_spec (.+)$/.exec(line);
    if (i) {
      installSpec = i[1];
      continue;
    }
    const n = /^nested_baseline (\S.*) ([0-9a-f]{7,40})$/.exec(line);
    if (n) {
      nestedBaselines.set(n[1], n[2]);
      continue;
    }
    const c = /^claude_md_before (yes|no)$/.exec(line);
    if (c) {
      claudeMdBefore = c[1] === "yes";
      continue;
    }
    const parts = /^([0-9a-f]{64}) {2}(.+)$/.exec(line);
    if (!parts) throw new Error(`bad line in ${MANIFEST_REL}: "${line}"`);
    hashes.set(parts[2], parts[1]);
  }
  return { version: m[1], hashes, baselineCommit, claudeMdBefore, nestedBaselines, installSpec };
}

export function writeManifest(target: string, { version, hashes, baselineCommit, claudeMdBefore, nestedBaselines, installSpec }: Manifest): void {
  const lines = [...hashes].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([rel, h]) => `${h}  ${rel}`);
  const file = join(target, MANIFEST_REL);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, [`version ${version}`, ...(baselineCommit === undefined ? [] : [`baseline_commit ${baselineCommit}`]), ...(claudeMdBefore === undefined ? [] : [`claude_md_before ${claudeMdBefore ? "yes" : "no"}`]), ...(installSpec === undefined ? [] : [`install_spec ${installSpec}`]), ...[...(nestedBaselines ?? [])].sort(([a], [b]) => (a < b ? -1 : 1)).map(([rel, sha]) => `nested_baseline ${rel} ${sha}`), ...lines].join("\n") + "\n");
}

function git(cwd: string, ...args: string[]) {
  const r = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8" });
  return { ok: r.status === 0, out: (r.stdout ?? "").trim() };
}

/** The HEAD sha of a repo, or null when it has no commit. */
export function headOf(dir: string): string | null {
  const r = git(dir, "rev-parse", "--verify", "-q", "HEAD");
  return r.ok ? r.out : null;
}

/**
 * Code repos cloned inside the workspace root (own .git, not tracked by the root): the root worktree holds none
 * of their code, so each half gets a worktree of them at the same relative path. Looks two levels down.
 */
export function nestedRepos(ws: string): string[] {
  const skip = (n: string) => n.startsWith(".") || n === "node_modules" || n === "writeup";
  const dirs = (d: string) => {
    try {
      return readdirSync(d, { withFileTypes: true }).filter((e) => e.isDirectory() && !skip(e.name)).map((e) => e.name);
    } catch {
      return [];
    }
  };
  const isRepo = (rel: string) => existsSync(join(ws, rel, ".git")) && git(ws, "ls-files", "--", rel).out === "" && headOf(join(ws, rel)) !== null; // a repo with no commit has no code to add
  const found: string[] = [];
  for (const a of dirs(ws)) {
    if (isRepo(a)) found.push(a);
    else for (const b of dirs(join(ws, a))) if (isRepo(`${a}/${b}`)) found.push(`${a}/${b}`);
  }
  return found;
}
