#!/usr/bin/env node
// Static pass of hk-surface-design's verify step: the checks a regex can answer,
// so the model spends its reading on judgment. Zero dependencies.
//
//   node check_surface.mjs <file.html|file.md> [--json]
//
// Prints one line per finding: `<item> <file>:<line> <message>`, then a count.
// Exit 0 when clean, 1 when anything was flagged, 2 on a usage error.
// Item ids are stable labels kept from the first checklist; on the current
// checklist 2-fresh is item 3, 11-system is 9, 12-theme and 13-filled are 10, and
// 13-narrow counts only when the brief says the surface is read on a phone.

import { readFileSync } from "node:fs";
import { basename } from "node:path";

const WIDE_TABLE = 5; // more columns than this needs a narrow layout
const UPPER_RULES = 3; // more uppercase rules than this is overuse
const LONG_LABEL = 3; // words; uppercase on more than this is a sentence shouting

export function checkSurface(text, name = "surface") {
  const findings = [];
  const lineOf = (index) => text.slice(0, index).split("\n").length;
  const add = (item, index, message) => findings.push({ item, line: index < 0 ? 1 : lineOf(index), message });
  const isHtml = /<(html|body|div|table|p)\b/i.test(text);

  // Unfilled template tokens: {{name}}, or the renderer's "not written yet" span.
  // Comments hold instructions that name tokens; blank them, keeping offsets.
  const uncommented = text.replace(/<!--[\s\S]*?-->/g, (c) => c.replace(/[^\n]/g, " "));
  for (const m of uncommented.matchAll(/\{\{\s*[\w.-]+\s*\}\}/g)) add("13-filled", m.index, `unfilled token ${m[0]}`);
  for (const m of text.matchAll(/class="unfilled" data-slot="([\w.-]+)"/g)) add("13-filled", m.index, `unfilled slot ${m[1]}`);

  // Freshness: a stamp that says when the surface was produced.
  if (!/<time\b|\b(rendered|generated|updated|last run|as of|produced)\b[^\n<]{0,40}\d/i.test(text))
    add("2-fresh", -1, "no last-updated stamp (when it was produced, from what)");

  if (!isHtml) {
    for (const m of text.matchAll(/^\|(.+)\|\s*\n\|[\s:|-]+\|\s*$/gm)) {
      const cols = m[1].split("|").length;
      if (cols > WIDE_TABLE) add("13-narrow", m.index, `markdown table has ${cols} columns; at most ${WIDE_TABLE} survive a terminal`);
    }
    return findings;
  }

  const css = [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/gi)].map((m) => m[1]).join("\n");

  if (!/<meta[^>]+name=["']viewport["']/i.test(text)) add("12-theme", -1, "no viewport meta; phones render it at desktop width");

  const hard = text.match(/<html[^>]*data-theme=["']light["']/i);
  if (hard) add("12-theme", hard.index, 'hardcoded data-theme="light" on <html>; the system theme is ignored');
  if (!/prefers-color-scheme\s*:\s*dark/i.test(css)) add("12-theme", text.indexOf("<style"), "no @media (prefers-color-scheme: dark); dark mode renders light");

  // Uppercase labels: count the rules, then find long text set in them.
  const upperClasses = new Set();
  let upperRules = 0;
  for (const m of css.matchAll(/([^{}]+)\{[^}]*text-transform\s*:\s*uppercase[^}]*\}/gi)) {
    upperRules++;
    for (const c of m[1].matchAll(/\.([\w-]+)/g)) upperClasses.add(c[1]);
  }
  if (upperRules > UPPER_RULES)
    add("11-system", text.search(/text-transform\s*:\s*uppercase/i), `${upperRules} rules set uppercase; keep it to a few one- or two-word labels`);
  const longUpper = new Map();
  for (const m of text.matchAll(/<(\w+)[^>]*class=["']([^"']+)["'][^>]*>([^<]*)</g)) {
    const cls = m[2].split(/\s+/).find((c) => upperClasses.has(c));
    if (cls && m[3].trim().split(/\s+/).length > LONG_LABEL && !longUpper.has(cls)) longUpper.set(cls, m);
  }
  for (const [cls, m] of longUpper)
    add("11-system", m.index, `.${cls} sets a ${m[3].trim().split(/\s+/).length}-word phrase in uppercase: "${m[3].trim().slice(0, 50)}"`);

  // Colour-only status: a status-looking element with no word in it.
  for (const m of text.matchAll(/<(span|div|i|td)[^>]*class=["'][^"']*\b(dot|status|badge|pill|indicator|light|led)\b[^"']*["'][^>]*>\s*<\/\1>/gi))
    add("12-theme", m.index, `empty .${m[2]}: status shown by colour alone`);

  // Wide tables with no narrow layout.
  const narrowCss = /@media[^{]*max-width/i.test(css);
  for (const m of text.matchAll(/<table\b[\s\S]*?<\/tr>/gi)) {
    const cols = (m[0].match(/<th\b/gi) || []).length;
    if (cols > WIDE_TABLE && !narrowCss) add("13-narrow", m.index, `table with ${cols} columns and no @media (max-width) layout; it scrolls sideways on a phone`);
  }

  return findings;
}

const isMain = process.argv[1] && import.meta.url.endsWith(basename(process.argv[1]));
if (isMain) {
  const args = process.argv.slice(2);
  const file = args.find((a) => !a.startsWith("--"));
  if (!file) {
    console.error("usage: node check_surface.mjs <file.html|file.md> [--json]");
    process.exit(2);
  }
  const findings = checkSurface(readFileSync(file, "utf8"), file);
  if (args.includes("--json")) console.log(JSON.stringify(findings, null, 2));
  else {
    for (const f of findings) console.log(`${f.item.padEnd(11)} ${file}:${f.line}  ${f.message}`);
    console.log(findings.length ? `${findings.length} static finding(s)` : "static pass clean");
  }
  process.exit(findings.length ? 1 : 0);
}
