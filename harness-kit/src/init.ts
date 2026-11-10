import { chmodSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { check, copyKit, type CopyReport } from "./copy.js";
import { excludeHarnesskit, seedReadiness } from "./seed.js";

/** The packaged kit, beside src/ or dist/ in both a checkout and an npm install. */
/** The one command init tells the person to run next; also in README.md. */
/** Stage names from the journey table in kit/templates/journey.md, in order. */
export function journeyStages(kitDir: string): string[] {
  const text = readFileSync(join(kitDir, "templates", "journey.md"), "utf8");
  return [...text.matchAll(/^\| (\d+) \| ([^|]+?) \|/gm)].map((m) => m[2]);
}

export const SETUP_PROMPT = 'claude "Set up this repo with harness-kit - follow docs/harnesskit/entry.md"';

export const defaultKitDir = resolve(dirname(fileURLToPath(import.meta.url)), "../kit");

export const packageVersion = (): string =>
  JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../package.json"), "utf8")).version;

const REGISTRY_SPEC = "@deepklarity/harnesskit";
export const WRAPPER_REL = "docs/harnesskit/harnesskit";

/**
 * How this init was run, so the wrapper can run the kit the same way. `npx --package=<spec>` sets npm_config_package
 * (a tarball, path or git URL); a project install lives in node_modules (outside npx's _npx cache) and is recorded as
 * `project`, so the wrapper finds it relative to the repo, never by a machine path; anything else is the registry.
 */
export function detectInstallSpec(env: NodeJS.ProcessEnv = process.env, pkgRoot: string = resolve(dirname(fileURLToPath(import.meta.url)), "..")): string {
  if (env.npm_config_package) return env.npm_config_package;
  const p = pkgRoot.replace(/\\/g, "/");
  if (p.includes("/node_modules/") && !p.includes("/_npx/")) return "project";
  return REGISTRY_SPEC;
}

const shq = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

/** The POSIX sh wrapper agents run as docs/harnesskit/harnesskit: the same kit version on every clone. */
export function wrapperScript(spec: string, version: string = packageVersion()): string {
  const run =
    spec === "project"
      ? `exec "$(dirname "$0")/../../node_modules/.bin/harnesskit" "$@"`
      : spec === REGISTRY_SPEC
        ? `exec npx --yes ${REGISTRY_SPEC}@${version} "$@"`
        : `exec npx --yes --package=${shq(spec)} harnesskit "$@"`;
  return `#!/bin/sh\n# Written by harnesskit init (install_spec in docs/harnesskit/manifest); rewritten on every init.\n${run}\n`;
}

export interface InitResult {
  code: number;
  output: string;
  report: CopyReport;
}

export function runInit({
  target,
  check: checkOnly,
  kitDir = defaultKitDir,
}: {
  target: string;
  check: boolean;
  kitDir?: string;
}): InitResult {
  const dir = resolve(target);
  const isGit = existsSync(join(dir, ".git"));

  const lines: string[] = [];
  let report: CopyReport;
  const wrote: string[] = [];
  if (checkOnly) {
    report = check(kitDir, dir);
  } else {
    const installSpec = detectInstallSpec();
    report = copyKit(kitDir, dir, { version: packageVersion(), installSpec });
    mkdirSync(join(dir, "docs/harnesskit"), { recursive: true });
    writeFileSync(join(dir, WRAPPER_REL), wrapperScript(installSpec));
    chmodSync(join(dir, WRAPPER_REL), 0o755);
    const board = join(dir, "writeup/agent-readiness");
    const seeded = !existsSync(board);
    seedReadiness(kitDir, dir);
    if (seeded) wrote.push(...readdirSync(board, { recursive: true }).map((f) => `writeup/${f}`));
    if (isGit) excludeHarnesskit(dir);
  }

  const verb = checkOnly ? "would be " : "";
  lines.push(
    `harnesskit init${checkOnly ? " --check" : ""}: ${report.written.length} ${verb}written, ${report.upgraded.length} ${verb}upgraded, ${report.drifted.length} drifted (edited, left alone), ${report.unchanged.length} left alone`,
  );
  if (!checkOnly) {
    const perFolder = new Map<string, number>();
    for (const p of [...report.written, ...report.upgraded, ...report.seeded, ...wrote]) {
      const top = p.split("/")[0];
      perFolder.set(top, (perFolder.get(top) ?? 0) + 1);
    }
    for (const [top, n] of [...perFolder].sort()) lines.push(`  ${top}/  ${n} file${n === 1 ? "" : "s"}`);
  }
  if (report.drifted.length) {
    lines.push("Drifted:", ...report.drifted.slice(0, 5).map((p) => `  ${p}`));
    if (report.drifted.length > 5) lines.push(`  ... and ${report.drifted.length - 5} more`);
  }
  if (!checkOnly) {
    lines.push(
      "",
      "Nothing else changed. Setup will show you each change to CLAUDE.md, AGENTS.md, .claude/ and .gitignore before making it.",
      ...(isGit
        ? []
        : ["", "This folder is not a git repository. Setup works; to measure before and after later, run git init, commit, and run init again."]),
      "",
      "The journey (setup shows this list, updated, in every message):",
      ...journeyStages(kitDir).map((stage, i) => `  ${i === 0 ? "✓" : "○"} ${i + 1}. ${stage}`),
      "",
      "Next, hand the repo to your agent:",
      "",
      `  ${SETUP_PROMPT}`,
      "",
    );
  }

  return { code: checkOnly && report.drifted.length ? 1 : 0, output: lines.join("\n") + "\n", report };
}
