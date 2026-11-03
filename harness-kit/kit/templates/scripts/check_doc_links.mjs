#!/usr/bin/env node
// Lists relative markdown links that point nowhere. Zero dependencies.
// Usage: node check_doc_links.mjs [root] [file-or-folder ...]
//   root defaults to the current folder; targets default to CLAUDE.md, AGENTS.md and docs/.
// Exit 1 when any link is broken. Links inside code fences and external links are skipped.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const root = resolve(process.argv[2] ?? ".");
const targets = process.argv.length > 3 ? process.argv.slice(3) : ["CLAUDE.md", "AGENTS.md", "docs"];

const files = [];
const collect = (p) => {
  if (!existsSync(p)) return;
  if (statSync(p).isDirectory()) {
    for (const e of readdirSync(p)) if (e !== "node_modules" && !e.startsWith(".")) collect(join(p, e));
  } else if (p.endsWith(".md")) files.push(p);
};
for (const t of targets) collect(join(root, t));

let broken = 0;
for (const file of files) {
  let fence = false;
  readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    if (/^\s*(```|~~~)/.test(line)) fence = !fence;
    if (fence) return;
    for (const m of line.replace(/`[^`]*`/g, "").matchAll(/\]\(([^)\s]+)\)/g)) {
      const href = m[1].split("#")[0];
      if (!href || /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//")) continue;
      const dest = href.startsWith("/") ? join(root, href) : join(dirname(file), href);
      if (!existsSync(dest)) {
        console.log(`${file.slice(root.length + 1)}:${i + 1}: broken link ${m[1]}`);
        broken++;
      }
    }
  });
}
console.log(broken ? `${broken} broken link(s) in ${files.length} file(s)` : `clean: ${files.length} file(s) checked`);
process.exit(broken ? 1 : 0);
