#!/usr/bin/env node
// The repo's front page is harness-kit/README.md (the npm package's README) with its links
// made relative to the repo root, plus a pointer to odin and taskit. One source, generated copy.
// Usage: node scripts/sync-readme.mjs          write README.md
//        node scripts/sync-readme.mjs --check  exit 1 if README.md is out of date
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(join(root, "harness-kit/README.md"), "utf8");

const relinked = source.replace(/(\]\()([^)\s]+)\)/g, (whole, open, target) =>
  /^(https?:|#|mailto:)/.test(target) || target === "LICENSE" ? whole : `${open}harness-kit/${target})`,
);
const footer =
  "\n---\n\nThis repository also holds odin, a multi-agent orchestration CLI, and taskit, a task board: [their README](docs/odin-and-taskit/README.md).\n";
const want =
  "<!-- Generated from harness-kit/README.md by scripts/sync-readme.mjs; edit that file, then run the script. -->\n\n" +
  relinked +
  footer;

for (const [, target] of want.matchAll(/\]\(([^)#\s]+)/g)) {
  if (!/^(https?:|mailto:)/.test(target) && !existsSync(join(root, target))) {
    console.error(`sync-readme: link target missing: ${target}`);
    process.exit(1);
  }
}

const path = join(root, "README.md");
if (process.argv.includes("--check")) {
  const have = existsSync(path) ? readFileSync(path, "utf8") : "";
  if (have !== want) {
    console.error("sync-readme: README.md is out of date; run node scripts/sync-readme.mjs");
    process.exit(1);
  }
  console.log("sync-readme: README.md matches harness-kit/README.md");
} else {
  writeFileSync(path, want);
  console.log("sync-readme: wrote README.md");
}
