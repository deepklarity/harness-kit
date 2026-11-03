#!/usr/bin/env node
// PreToolUse guard: block writes into the folders this workspace builds
// around but never edits, and the git operations that lose other agents' work.
//
// The protected folders live in protected.txt beside this file, one per line;
// this file is generic and stays unedited so init can upgrade it. The README
// next to it says what it blocks and how to work with it.
import { realpathSync, readFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, sep } from "node:path";

/**
 * The folders an agent may read, search and run, but never write to, read
 * from protected.txt: one repo folder per line, relative to the workspace root.
 * @type {string[]}
 */
const PROTECTED = (() => {
  try {
    return readFileSync(new URL("./protected.txt", import.meta.url), "utf8")
      .split("\n").map((l) => l.trim()).filter(Boolean);
  } catch {
    return [];
  }
})();

/**
 * Commands blocked wherever they point. Add this workspace's paid-test
 * invocations here if it has any.
 * @type {Array<[RegExp, string]>}
 */
const COMMAND_PATTERNS = [
  [/\bgit\s+stash\b/,
    "git stash in a shared checkout loses other agents' work; use a branch or a temporary commit"],
  [/\bgit\s+reset\s+--hard\b/,
    "git reset --hard throws away work other agents may be holding in this checkout"],
  [/\bpush\b.*(?:--force\b|-f\b)/,
    "a force push rewrites history other people have already pulled"],
];

// Bash commands that write to a filesystem target.
const WRITE_CMD = /\b(?:sed\s+-i|cp|mv|rm|touch|tee)\b/;

// Redirection into a path: `... > path` or `>> path`. A `>` that follows
// `=` or another `>` is text (`==>` in a grep pattern), not a redirect.
const REDIRECT = /(?<![=>])>>?\s*([^\s;&|]+)/g;

// A heredoc body: from `<<WORD` (quoted or not) to the line holding WORD.
const HEREDOC = /<<-?\s*['"]?(\w+)['"]?[^\n]*\n.*?^\1\s*$/gms;

// Boundaries between simple commands: `;`, `&&`, `||`, `|`, newlines.
const SPLIT = /\|\||&&|[;|\n]/;

const EDIT_TOOLS = ["Edit", "Write", "MultiEdit", "NotebookEdit"];

/** @param {string} reason @returns {never} */
function block(reason) {
  process.stderr.write(`blocked by .claude/hooks/guard.mjs: ${reason}\n`);
  process.exit(2);
}

/** realpath that tolerates a path that does not exist yet.
 * @param {string} p @returns {string} */
function realpath(p) {
  try {
    return realpathSync(p);
  } catch {
    const parent = dirname(p);
    return parent === p ? p : join(realpath(parent), basename(p));
  }
}

/** @param {string} path @param {string} cwd @returns {string | null} */
function pathInProtected(path, cwd) {
  if (!isAbsolute(path)) path = join(cwd, path);
  const real = realpath(path);
  for (const repo of PROTECTED) {
    const repoReal = realpath(join(cwd, repo));
    if (real === repoReal || real.startsWith(repoReal + sep)) return repo;
  }
  return null;
}

/** @param {Record<string, unknown>} toolInput @param {string} cwd */
function checkEdit(toolInput, cwd) {
  const filePath = toolInput.file_path;
  if (typeof filePath !== "string" || !filePath) return;
  const repo = pathInProtected(filePath, cwd);
  if (repo) {
    block(
      `edits inside ${repo}/ are its owners' call; put a suggestion on ` +
        "the readiness board instead.",
    );
  }
}

/**
 * Paths a command may write to: every redirect target, plus, in each
 * simple command that has a write verb, the tokens after that verb which
 * name a protected folder. Heredoc bodies are text, not commands, and are
 * dropped first, so a script that merely mentions a repo goes through.
 * @param {string} command @returns {string[]}
 */
function candidatePaths(command) {
  command = command.replace(HEREDOC, "");
  const paths = [...command.matchAll(REDIRECT)].map((m) => m[1]);
  for (const simple of command.split(SPLIT)) {
    const verb = WRITE_CMD.exec(simple);
    if (!verb) continue;
    const rest = simple.slice(verb.index + verb[0].length);
    for (const raw of rest.split(/\s+/).filter(Boolean)) {
      const token = raw.replace(/^['"]+|['"]+$/g, "");
      if (PROTECTED.some((repo) =>
        token === repo || token.startsWith(repo + "/") ||
        token.includes(`/${repo}/`) || token.endsWith("/" + repo))) {
        paths.push(token);
      }
    }
  }
  return paths;
}

/** @param {Record<string, unknown>} toolInput @param {string} cwd */
function checkBash(toolInput, cwd) {
  const command = toolInput.command;
  if (typeof command !== "string" || !command) return;
  // A heredoc body is text: a doc that quotes "git stash" is not a stash.
  const bare = command.replace(HEREDOC, "");
  for (const [pattern, reason] of COMMAND_PATTERNS) {
    if (pattern.test(bare)) block(reason);
  }
  for (const path of candidatePaths(command)) {
    const repo = pathInProtected(path, cwd);
    if (repo) {
      block(
        `writing into ${repo}/ from Bash is its owners' call; put a ` +
          "suggestion on the readiness board instead.",
      );
    }
  }
}

function main() {
  /** @type {any} */
  let payload;
  try {
    payload = JSON.parse(readFileSync(0, "utf8"));
  } catch {
    process.exit(0);
  }
  if (!payload || typeof payload !== "object") process.exit(0);

  const toolName = payload.tool_name ?? "";
  const toolInput = payload.tool_input || {};
  const cwd = payload.cwd || process.cwd();

  if (EDIT_TOOLS.includes(toolName)) checkEdit(toolInput, cwd);
  else if (toolName === "Bash") checkBash(toolInput, cwd);

  process.exit(0);
}

main();
