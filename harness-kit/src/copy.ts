import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { generateStubs } from "./stubs.js";
import { headOf, type Manifest, nestedRepos, readManifest, sha256, writeManifest } from "./manifest.js";

export type CopyReport = { written: string[]; upgraded: string[]; drifted: string[]; unchanged: string[]; seeded: string[] };

/** Every file under dir, as paths relative to dir with "/" separators, sorted. */
function walk(dir: string, prefix = ""): string[] {
  const out: string[] = [];
  for (const e of readdirSync(join(dir, prefix), { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) out.push(...walk(dir, rel));
    else if (e.isFile()) out.push(rel);
  }
  return out;
}

const isDir = (p: string) => {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
};

/** The "yours" files a workspace owns: seeded once when absent, never overwritten, never in the manifest. */
const SEEDS: Record<string, string> = {
  "docs/glossary.md":
    "# Glossary\n\nThe words this workspace uses its own way, one line each: what it means here, where it lives in the code.\nEmpty until a board row writes the first ones down.\n",
  "docs/patterns/_INDEX.md":
    "Reusable learnings live here: a named principle, how to recognise it, what to do, and the instances that taught it.\nEmpty until a board row appends the first pattern.\n",
  "docs/breadcrumb_analysis/_INDEX.md":
    "End-to-end traces of flows live here, one FLOW.md/DETAILS.md/DEBUG.md set per flow, indexed with a symptom table.\nEmpty until a board row traces the first flow.\n",
  "docs/testing_process/_INDEX.md":
    "The testing workflow, debugging protocol and test shortcuts for this workspace live here.\nEmpty until a board row writes the first one down.\n",
  "docs/guides/_INDEX.md":
    "Longer how-tos a person reads to learn a system live here: setup, deployment, a subsystem end to end.\nEmpty until a board row writes the first guide.\n",
};

/** [target-relative destination, bytes] for every file a workspace receives. */
function plan(kitDir: string, wantMergeBrief: boolean): [string, Buffer][] {
  const files: [string, Buffer][] = [];
  const addFile = (src: string, dest: string) => {
    try {
      files.push([dest, readFileSync(join(kitDir, src))]);
    } catch {
      /* optional file absent from the kit */
    }
  };
  const add = (srcRoot: string, destRoot: string) => {
    if (!isDir(join(kitDir, srcRoot))) return;
    for (const rel of walk(join(kitDir, srcRoot))) addFile(`${srcRoot}/${rel}`, `${destRoot}/${rel}`);
  };
  add("docs", "docs");

  const csvPath = join(kitDir, "skills/skills.csv");
  let csv: string;
  try {
    csv = readFileSync(csvPath, "utf8");
  } catch {
    throw new Error(`kit has no skills.csv (looked for ${csvPath})`);
  }
  const lines = csv.split("\n");
  const kept = [lines[0]];
  const categories = new Set<string>();
  for (const line of lines.slice(1)) {
    if (!line.trim()) continue;
    const [name, category, tier] = line.split(",");
    if (tier !== "kit") continue;
    kept.push(line);
    categories.add(category);
    add(`skills/${category}/${name}`, `docs/skills/${category}/${name}`);
  }
  files.push(["docs/skills/skills.csv", Buffer.from(kept.join("\n") + "\n")]);
  addFile("skills/README.md", "docs/skills/README.md");
  for (const c of categories) addFile(`skills/${c}/README.md`, `docs/skills/${c}/README.md`);

  addFile("entry.md", "docs/harnesskit/entry.md");
  // The merge brief is only for a workspace that had its own CLAUDE.md before the first init, as
  // that init recorded. A CLAUDE.md the kit or its setup wrote never earns one, on any re-run.
  const merge = "docs/harnesskit/MERGE_CLAUDE_MD.md";
  if (wantMergeBrief) {
    addFile("templates/MERGE_CLAUDE_MD.md", merge);
  }
  add("templates/mission-control", "docs/harnesskit/templates/mission-control");
  add("templates/scripts", "docs/harnesskit/templates/scripts");
  add("proof", "docs/harnesskit/proof");
  addFile("templates/CLAUDE.md", "docs/harnesskit/templates/CLAUDE.md");
  addFile("templates/journey.md", "docs/harnesskit/journey.md");
  addFile("templates/scenario.md", "docs/harnesskit/templates/scenario.md");
  addFile("templates/scenario-archetypes.md", "docs/harnesskit/templates/scenario-archetypes.md");

  // Ready-made copies of the workflow files, mirroring their destination; only the setup agent installs them.
  const W = "docs/harnesskit/workflow";
  addFile("templates/hooks/guard.mjs", `${W}/.claude/hooks/guard.mjs`);
  addFile("templates/hooks/README.md", `${W}/.claude/hooks/README.md`);
  addFile("templates/hooks/settings.json", `${W}/.claude/settings.hook.json`);
  files.push([`${W}/.claude/hooks/protected.txt`, Buffer.alloc(0)]);
  addFile("templates/AGENTS.md", `${W}/AGENTS.md`);
  addFile("templates/gitignore.lines", `${W}/gitignore.lines`);
  const tmp = mkdtempSync(join(tmpdir(), "hk-stubs-"));
  try {
    generateStubs(join(kitDir, "skills"), tmp, { tiers: ["kit"] });
    for (const rel of walk(tmp)) files.push([`${W}/.claude/skills/${rel}`, readFileSync(join(tmp, rel))]);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
  return files;
}

export type CopyOptions = { version: string; /** recorded in the manifest as install_spec */ installSpec?: string };

/** The workspace's HEAD commit, or null when it has none. */
function headCommit(dir: string): string | null {
  try {
    return execFileSync("git", ["-C", dir, "rev-parse", "--verify", "-q", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return null;
  }
}

function run(kitDir: string, target: string, { version, installSpec }: CopyOptions, dryRun: boolean): CopyReport {
  const prev = readManifest(target);
  // Recorded once, before anything is written: did the workspace bring its own CLAUDE.md?
  const claudeMdBefore = prev ? prev.claudeMdBefore ?? false : existsSync(join(target, "CLAUDE.md"));
  // Recorded once, before the first write; a re-run keeps the original.
  // Recorded at the first init that finds a commit, then never moved.
  const baselineCommit = prev?.baselineCommit ?? headCommit(target);
  // Each nested repo's HEAD the first time the manifest sees it; a re-run keeps the original.
  const nestedBaselines = new Map(prev?.nestedBaselines ?? []);
  for (const rel of nestedRepos(target)) if (!nestedBaselines.has(rel)) nestedBaselines.set(rel, headOf(join(target, rel))!);
  const hashes = new Map(prev?.hashes ?? []);
  const report: CopyReport = { written: [], upgraded: [], drifted: [], unchanged: [], seeded: [] };

  const write = (dest: string, bytes: Buffer) => {
    if (dryRun) return;
    mkdirSync(dirname(join(target, dest)), { recursive: true });
    writeFileSync(join(target, dest), bytes);
    hashes.set(dest, sha256(bytes));
  };

  for (const [dest, kitBytes] of plan(kitDir, claudeMdBefore)) {
    let onDisk: Buffer | null = null;
    try {
      onDisk = readFileSync(join(target, dest));
    } catch {
      /* absent */
    }
    const recorded = hashes.get(dest);

    if (onDisk === null) {
      report.written.push(dest);
      write(dest, kitBytes);
    } else if (recorded === undefined) {
      if (sha256(onDisk) === sha256(kitBytes)) {
        // Already the kit's bytes: adopt it so later upgrades track it.
        report.unchanged.push(dest);
        hashes.set(dest, sha256(onDisk));
      } else report.drifted.push(dest);
    } else if (sha256(onDisk) !== recorded) {
      report.drifted.push(dest);
    } else if (sha256(kitBytes) !== recorded) {
      report.upgraded.push(dest);
      write(dest, kitBytes);
    } else {
      report.unchanged.push(dest);
    }
  }

  for (const [dest, text] of Object.entries(SEEDS)) {
    if (existsSync(join(target, dest))) continue;
    report.seeded.push(dest);
    if (dryRun) continue;
    mkdirSync(dirname(join(target, dest)), { recursive: true });
    writeFileSync(join(target, dest), text);
  }

  if (!dryRun) writeManifest(target, { version, hashes, baselineCommit, claudeMdBefore, nestedBaselines, installSpec } satisfies Manifest);
  return report;
}

/** Copy the kit into target; see the report for what was written, upgraded, left alone or unchanged. */
export function copyKit(kitDir: string, target: string, opts: CopyOptions): CopyReport {
  return run(kitDir, target, opts, false);
}

/** Same classification as copyKit, writing nothing; "written" and "upgraded" are what copyKit would do. */
export function check(kitDir: string, target: string): CopyReport {
  return run(kitDir, target, { version: readManifest(target)?.version ?? "" }, true);
}
