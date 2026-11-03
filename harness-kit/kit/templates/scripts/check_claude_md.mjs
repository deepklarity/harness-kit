#!/usr/bin/env node
// Runs the mechanical items of docs/harnesskit/adoption/CLAUDE_MD_AUDIT.md on a CLAUDE.md. Zero dependencies.
// Usage: node check_claude_md.mjs [path/to/CLAUDE.md]   (default ./CLAUDE.md; paths resolve from its folder)
// Prints one line per failure and a last line "passed N of M checks"; exits 1 on any failure.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const MAX_LINES = 160;
const MAX_WORDS = 1500;
const BANNED = [/\bnone yet\b/i, /\bnot (written|built|created|documented) yet\b/i, /\bno breadcrumbs? (yet|exists?)\b/i,
  /\bno debug scripts ship\b/i, /\bTBD\b/, /\bTODO\b/, /\bcoming soon\b/i, /\bto be (written|added|filled)\b/i];
const HEADINGS = [["Read before you act", /^##\s+Read before you act/im], ["Methodical Problem-Solving", /^##\s+Methodical/im],
  ["Subagent Strategy", /^##\s+Subagent/im], ["Testing", /^##\s+Testing/im], ["Stop and Ask", /^##\s+Stop and ask/im], ["Tool Rules", /^##\s+Tool rules/im]];

const file = resolve(process.argv[2] ?? "CLAUDE.md");
if (!existsSync(file)) { console.error(`${file}: not found`); process.exit(2); }
// Paths in CLAUDE.md are relative to the repo root, wherever the draft sits.
const base = (() => {
  for (let d = dirname(file); ; d = dirname(d)) {
    if (existsSync(join(d, ".git"))) return d;
    if (dirname(d) === d) return dirname(file);
  }
})();
const raw = readFileSync(file, "utf8").split("\n");

// Mark lines inside HTML comments so they count as neither content nor claims.
let inComment = false, inFence = false;
const lines = raw.map((text, i) => {
  const wasComment = inComment;
  if (!inComment && text.includes("<!--")) inComment = true;
  const commentLine = wasComment || inComment;
  if (inComment && text.includes("-->")) inComment = false;
  let fenced = false;
  if (!commentLine) { if (/^\s*(```|~~~)/.test(text)) { inFence = !inFence; fenced = true; } else fenced = inFence; }
  return { n: i + 1, text, comment: commentLine, fenced };
});
const content = lines.filter((l) => !l.comment);
const prose = content.filter((l) => !l.fenced);

const slug = (h) => h.toLowerCase().replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");
const anchorsOf = (p) => new Set(readFileSync(p, "utf8").split("\n").filter((l) => /^#{1,6}\s/.test(l)).map((l) => slug(l.replace(/^#+\s*/, ""))));

const checks = [];
const check = (name, fn) => checks.push({ name, fails: fn() });

check("no unfilled slots", () => content.flatMap((l) => [...l.text.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)].map((m) => `${l.n}: unfilled slot {{${m[1]}}}`)));
check(`at most ${MAX_LINES} lines`, () => (content.length > MAX_LINES ? [`0: ${content.length} lines outside comments, budget ${MAX_LINES}; move a section into a linked doc`] : []));
check(`at most ${MAX_WORDS} words`, () => {
  const words = content.reduce((s, l) => s + l.text.split(/\s+/).filter(Boolean).length, 0);
  return words > MAX_WORDS ? [`0: ${words} words outside comments, budget ${MAX_WORDS}; every token here is paid every session, so move detail into an index or a page`] : [];
});
check("no status phrases", () => prose.flatMap((l) => BANNED.filter((b) => b.test(l.text)).map((b) => `${l.n}: banned phrase ${b} (drop the line, or state what exists)`)));
check("required sections present", () => HEADINGS.filter(([, re]) => !re.test(content.map((l) => l.text).join("\n"))).map(([h]) => `0: missing section "${h}"`));
check("read-before-you-act table has 3+ rows", () => {
  const i = prose.findIndex((l) => /^##\s+Read before you act/i.test(l.text));
  if (i < 0) return [];
  const rows = [];
  for (const l of prose.slice(i + 1)) { if (/^##\s/.test(l.text)) break; if (/^\|/.test(l.text) && !/^\|[-\s|]+\|?$/.test(l.text)) rows.push(l); }
  return rows.length - 1 >= 3 ? [] : [`${prose[i].n}: table has ${Math.max(rows.length - 1, 0)} rows, need at least 3`];
});
check("every routing row targets an existing file", () => {
  const i = prose.findIndex((l) => /^##\s+Read before you act/i.test(l.text));
  if (i < 0) return [];
  const fails = [];
  const rows = [];
  for (const l of prose.slice(i + 1)) { if (/^##\s/.test(l.text)) break; if (/^\|/.test(l.text) && !/^\|[-\s|]+\|?$/.test(l.text)) rows.push(l); }
  for (const l of rows.slice(1)) {
    const cell = l.text.split("|").slice(2).join("|");
    const refs = [...cell.matchAll(/`([^`]+)`/g)].map((m) => m[1]).concat([...cell.matchAll(/\]\(([^)\s]+)\)/g)].map((m) => m[1]))
      .map((r) => r.split("#")[0].split(/[,\s]/)[0]).filter((r) => r && /[./]/.test(r) && !/^[a-z][a-z0-9+.-]*:|[<>{}*]/i.test(r) && !/^\//.test(r));
    if (!refs.length) { fails.push(`${l.n}: routing row names no file in its right column`); continue; }
    for (const r of refs) if (!/^writeup\//.test(r) && !existsSync(join(base, r))) fails.push(`${l.n}: routing row targets ${r}, which does not exist`);
  }
  return fails;
});
check("routing table names each entry index that exists", () => {
  const i = prose.findIndex((l) => /^##\s+Read before you act/i.test(l.text));
  if (i < 0) return [];
  const rows = [];
  for (const l of prose.slice(i + 1)) { if (/^##\s/.test(l.text)) break; if (/^\|/.test(l.text)) rows.push(l.text); }
  const table = rows.join("\n");
  // The three entry files of the disclosure tree: flows, tools, guides. CLAUDE.md routes to these, never past them.
  return ["docs/breadcrumb_analysis/_INDEX.md", "scripts/README.md", "docs/_INDEX.md"]
    .filter((f) => existsSync(join(base, f)) && !table.includes(f))
    .map((f) => `${prose[i].n}: routing table does not name the entry index ${f}`);
});
check("find-the-row rule present", () => (/find the row first/i.test(prose.map((l) => l.text).join("\n")) ? [] : ["0: no 'Find the row first' rule in Tool Rules (open a matching row's page or run its tool before any grep)"]));
check("breadcrumb-first and diagnostics-first rules present", () => {
  const t = prose.map((l) => l.text).join("\n");
  return [/breadcrumb-first/i.test(t) ? null : "0: no 'Breadcrumb-first' rule", /(diagnostics? before|reproduc)/i.test(t) ? null : "0: no diagnostics-first (or how-to-reproduce) rule"].filter(Boolean);
});
check("verify command in a fenced block under Testing", () => {
  const i = content.findIndex((l) => /^##\s+Testing/i.test(l.text));
  if (i < 0) return [];
  let fence = false;
  for (const l of content.slice(i + 1)) {
    if (/^##\s/.test(l.text) && !l.fenced) break;
    if (/^\s*(```|~~~)/.test(l.text)) { fence = !fence; continue; }
    if (fence && l.text.trim() && !l.text.trim().startsWith("#")) return [];
  }
  return [`${content[i].n}: no command in a fenced block under Testing`];
});
check("no setup-time rules", () => prose.filter((l) => /protected\.txt|while harness setup runs/i.test(l.text)).map((l) => `${l.n}: setup-time rule (delete it; it ended with setup)`));
check("every named path and anchor exists", () => {
  const fails = [];
  const seen = new Set();
  for (const l of prose) {
    const refs = [...l.text.matchAll(/`([^`\s]+)`/g)].map((m) => m[1]).concat([...l.text.matchAll(/\]\(([^)\s]+)\)/g)].map((m) => m[1]));
    for (const ref of refs) {
      if (/^[a-z][a-z0-9+.-]*:|^\/\/|^~|[<>*{}$|=@]|^-|^\.\.?$/i.test(ref)) continue;
      const [p, anchor] = ref.split("#");
      if (!p || p.startsWith("/") || !p.includes("/") || !(p.endsWith("/") || /\.[a-z]{1,5}$/i.test(p))) continue;
      if (/\.env$|^writeup\/|^\.git\//.test(p) || /\(|\)/.test(p)) continue;
      const key = ref + "@" + l.n;
      if (seen.has(key)) continue;
      seen.add(key);
      const dest = join(base, p);
      if (!existsSync(dest)) { fails.push(`${l.n}: ${ref} does not exist`); continue; }
      if (anchor && p.endsWith(".md") && !anchorsOf(dest).has(anchor)) fails.push(`${l.n}: ${ref} has no heading "${anchor}"`);
    }
  }
  return fails;
});

let failed = 0;
for (const c of checks) for (const f of c.fails) { failed++; const [n, ...rest] = f.split(": "); console.log(`${file}:${n}: [${c.name}] ${rest.join(": ")}`); }
const passed = checks.filter((c) => !c.fails.length).length;
console.log(`passed ${passed} of ${checks.length} checks`);
process.exit(failed ? 1 : 0);
