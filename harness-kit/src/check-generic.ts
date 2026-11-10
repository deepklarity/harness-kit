/**
 * Scan kit-copied files for workspace-specific names leaking into the kit
 * (tenet 6: the kit knows nothing about a workspace until it looks).
 *
 * Default mode honours example markers ("Example: in harness-kit" sections,
 * "e.g." lines) for an internal fork. --strict honours none of them: the
 * release gate for a public kit. Workspace-tier skills are skipped in both.
 *
 * Usage: node dist/check-generic.js [--list] [--strict]
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// The denylist lives one name per line in the gitignored .generic-deny, so the
// checker never ships the names it looks for.
const PRIVATE_DENY_FILE = ".generic-deny";

function privateDenylist(): string[] {
  const p = join(ROOT, PRIVATE_DENY_FILE);
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8").split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
}
const DENYLIST = privateDenylist();
const SCAN_ROOTS = ["kit/entry.md", "kit/docs", "kit/skills", "kit/templates", "kit/proof", ".claude/skills", "README.md"];
// A line is an example when it says so, a quoted anti-example, kit
// bookkeeping in frontmatter, or a link target.
const SKIP_LINE_MARKERS = ["for example", "e.g.", "looks wrong when", "generated_from:", "href=", "in harness-kit,"];
const SKIP_SUFFIXES = new Set([".csv"]);
const ALLOWED_HTML_ONE = "kit/docs/adoption/report-template.html";
const SKILLS_CSV_REL = "kit/skills/skills.csv";

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const compile = (terms: string[]): [string, RegExp][] => terms.map((t) => [
  t,
  new RegExp(/^[A-Za-z0-9_ -]+$/.test(t) ? `\\b${escapeRe(t)}\\b` : escapeRe(t), "i"),
]);
const PATTERNS = compile(DENYLIST);


// The package scope, and the README's one credit line.
const ALLOWED = /@deepklarity\/harnesskit|Made by \[DeepKlarity\]\(https:\/\/deepklarity\.com\)/gi;

// Python str.splitlines() line boundaries.
const LINE_BREAK = new RegExp("\\r\\n|[\\n\\r\\v\\f\\x1c-\\x1e\\x85\\u2028\\u2029]");

export type Hit = [line: number, term: string];

function parseCsvRows(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      rows.push(row); row = [];
    } else cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  const [header, ...body] = rows.filter((r) => r.length > 1 || r[0] !== "");
  if (!header) return [];
  return body.map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
}

function workspaceSkills(): Set<string> {
  const p = join(ROOT, SKILLS_CSV_REL);
  if (!existsSync(p)) return new Set();
  return new Set(
    parseCsvRows(readFileSync(p, "utf8")).filter((r) => r.tier === "workspace").map((r) => r.name),
  );
}

function suffixOf(rel: string): string {
  const base = rel.split("/").pop() ?? "";
  const i = base.lastIndexOf(".");
  return i > 0 ? base.slice(i).toLowerCase() : "";
}

function walk(dir: string, out: string[]): void {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    let st;
    try { st = statSync(p); } catch { continue; }
    if (st.isDirectory()) {
      if (name === "__pycache__" || name === "tests") continue;
      walk(p, out);
    } else if (st.isFile() && name !== "check_generic.py" && name !== "check-generic.ts") {
      out.push(p);
    }
  }
}

function collectFiles(): string[] {
  const seen = new Set<string>();
  for (const rel of SCAN_ROOTS) {
    const p = join(ROOT, rel);
    if (!existsSync(p)) continue;
    if (statSync(p).isFile()) seen.add(p);
    else {
      const found: string[] = [];
      walk(p, found);
      found.forEach((f) => seen.add(f));
    }
  }
  return [...seen].sort();
}

function shouldSkipFile(rel: string, skills: Set<string>): boolean {
  const parts = rel.split("/");
  if (parts.includes("skills") && [...skills].some((n) => parts.includes(n))) return true;
  const suffix = suffixOf(rel);
  return (
    SKIP_SUFFIXES.has(suffix) ||
    (suffix === ".html" && rel !== ALLOWED_HTML_ONE) ||
    rel === SKILLS_CSV_REL
  );
}

export function findHits(path: string, rel: string, opts: { strict?: boolean; terms?: string[] } = {}): Hit[] {
  const strict = opts.strict ?? false;
  const patterns = opts.terms ? compile(opts.terms) : PATTERNS;
  let lines: string[];
  try {
    lines = readFileSync(path, "utf8").split(LINE_BREAK);
    if (lines[lines.length - 1] === "") lines.pop();
  } catch {
    return [];
  }

  let inDetails = false;
  let inExample = false;
  let inWrongSection = false;
  const isToneFile = rel.endsWith("tone_and_taste.md");
  const hits: Hit[] = [];

  lines.forEach((line, idx) => {
    const low = line.trim().toLowerCase();
    if (low.includes("<details")) inDetails = true;
    if (low.startsWith("#") && low.includes("example: in harness-kit")) inExample = true;
    else if (low.startsWith("#") && inExample) inExample = false;
    if (isToneFile && low.startsWith("## when we got this wrong")) inWrongSection = true;
    else if (isToneFile && inWrongSection && low.startsWith("## ")) inWrongSection = false;

    const skipBlock = strict ? false : inDetails || inExample || inWrongSection;
    if (low.includes("</details>")) inDetails = false;
    if (skipBlock) return;
    if (!strict && SKIP_LINE_MARKERS.some((m) => line.toLowerCase().includes(m))) return;

    const scanned = line.replace(ALLOWED, "");
    for (const [term, pattern] of patterns) {
      if (pattern.test(scanned)) hits.push([idx + 1, term]);
    }
  });
  return hits;
}

/** Runs the scan, prints to stdout, returns the exit code. */
export function main(argv: string[]): number {
  const doList = argv.includes("--list");
  const strict = argv.includes("--strict");
  const skills = workspaceSkills();
  let anyHits = false;

  for (const path of collectFiles()) {
    const rel = relative(ROOT, path).split(sep).join("/");
    if (shouldSkipFile(rel, skills)) continue;
    if (doList) process.stdout.write(`${rel}\n`);
    for (const [lineno, term] of findHits(path, rel, { strict })) {
      anyHits = true;
      process.stdout.write(`${rel}:${lineno}: ${term}\n`);
    }
  }
  if (anyHits) return 1;
  process.stdout.write(
    DENYLIST.length
      ? "generic: clean\n"
      : `generic: clean (public names only; no ${PRIVATE_DENY_FILE}, private names unchecked)\n`,
  );
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main(process.argv.slice(2)));
}
