import { execFileSync } from "node:child_process";
import { appendFileSync, cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

const INITIATIVE = "agent-readiness";
const VERDICT = "Setting up harness-kit in this workspace: nothing assessed yet";

// One open task per remaining step of kit/entry.md. Columns: id, title, depends_on, closes_when, notes.
// Board rows are written by header name, so the template may grow columns.
const NO_CHANGE = "Show, ask, do, tell (entry.md, Workflow files); the default is no change, and a no closes the row.";
const STEPS: [string, string, string, string, string][] = [
  ["t-wf-skills", "Install the skill stubs the person chose", "",
    "The person was shown the skill categories and answered; each chosen stub is in .claude/skills/ (names already there skipped and reported) or the answer is none, and the change is told with its undo",
    NO_CHANGE],
  ["t-wf-guard", "Turn on the guard hook", "t-wf-skills",
    "The person was shown what the hook blocks and answered; if yes, .claude/hooks/guard.mjs, protected.txt and the PreToolUse entry in .claude/settings.json are in place with other settings untouched, and the change is told with its undo",
    NO_CHANGE],
  ["t-survey", "Look around the workspace and list each repo", "",
    "RESUME.md has one row per repo: git, language, framework, package manager, test runner, env templates, log paths, database URLs, compose services",
    "Tenet: observe before acting. Model tier: mechanical, cheap."],
  ["t-guard", "Name the repo folders the guard hook protects", "t-survey",
    "With the guard on, .claude/hooks/protected.txt lists every repo folder found, one per line, and an edit into one is blocked; with it off, nothing to write",
    "Model tier: mechanical, cheap."],
  ["t-checklist", "Score the twelve readiness areas", "t-survey",
    "Every area in docs/adoption/CHECKLIST.md has a 0 to 10 score in the Before scaffolding column of the page's Readiness table, the only copy of the scores, with the file that justifies it",
    "Model tier: mid, reading and judging code."],
  ["t-entrypoint", "Finish the entrypoint CLAUDE.md", "t-checklist",
    "CLAUDE.draft.md has no slot left, about 150 lines, and a subagent has checked each claim about the code against the code",
    "Work in writeup/agent-readiness/CLAUDE.draft.md until the decisions are in. Model tier: strong."],
  ["t-decisions", "Put the open questions on the board as decision rows", "t-entrypoint",
    "Each question is a decision row with a proposed answer, the page is rendered, and the person has been told how many wait on them",
    "Three or four, not twenty. Anything a cheap command settles is run, not asked."],
  ["t-wf-claude-md", "Install the entrypoint as CLAUDE.md", "t-decisions",
    "The person was shown the new file or a diff against theirs and answered; if yes, CLAUDE.md is in place (theirs merged by proposal, never replaced) and the change is told with its undo",
    NO_CHANGE],
  ["t-board", "Fill the board with a row for everything the checklist found missing", "t-decisions",
    "Every unchecked checklist item has its row, each task row names its proof, and the page is rendered",
    "Model tier: strong, design and synthesis."],
  ["t-build", "Work the board: build every doc, tool and audit row it holds", "t-board",
    "Every task row the checklist added (breadcrumbs, testing guide, debugging protocol, glossary, system guide, diagnostic tools, audits) is done with its proof; the harness has written tests for the riskiest interfaces (a mock tier first, strong assertions, wired into the gate) where the repo had none or too few; the gate is green; the readiness table is re-scored",
    "The proof measures this harness, so it waits for it. Setup's write lock is over by now, so tests and the small seams they need are ordinary work. Build from the rows as written; never shape a doc or a test around a scenario."],
  ["t-wf-stop", "Add AGENTS.md and the .gitignore lines, and remove the guard hook", "t-build",
    "AGENTS.md (only if missing) and the missing .gitignore lines were each shown and answered, the guard hook entry, guard.mjs and protected.txt are gone, and every workflow file setup changed is listed to the person with its undo",
    NO_CHANGE],
  ["t-harness-audit", "Audit the harness before proving: interfaces, breadcrumbs, tools, docs, tests", "t-build",
    "check_harness.mjs --html has run, the quiz rows are in writeup/agent-readiness/harness-audit.csv, and every area scores 7 or more or the person accepted the gap; each area below 7 is a board row",
    "Procedure in docs/adoption/HARNESS_AUDIT.md. Model tier: mechanical script, cheap subagents for the quiz."],
  ["t-scenarios", "Write the proof scenarios from the archetypes", "t-harness-audit",
    "writeup/scenarios/ holds as many as the profile asks, one per difficulty, each proof failing on the untouched workspace and passing on two good answers",
    "From docs/harnesskit/templates/scenario-archetypes.md. Feature scenarios carry hidden tests."],
  ["t-prove-confirm", "Ask the person to confirm the proof: the scenarios, more to add, and the budget", "t-scenarios",
    "The person answered in one question: run within the budget, add scenarios first, change the budget, or not now; the answer is in log.md and the budget in profile.md",
    "When the scenarios are written, set this row to status owner, owner_kind Decide, owner_action naming what the harness now holds (breadcrumbs, guides, tools, audits, by count) and the harness-audit scores by area, the scenario count and the dry-run estimate against the budget, owner_reply the four choices. Nothing paid runs before a yes."],
  ["t-prove", "Run the proof one scenario at a time and render the proof page", "t-prove-confirm",
    "Every confirmed scenario has both halves run, collected and judged, and writeup/proof/index.html is rendered",
    "Stays inside the confirmed budget; the ledger in writeup/proof/ledger.csv is the spend."],
];

const FILES: [string, string][] = [
  ["board.csv", "The board: the only place task and decision state is written"],
  ["RESUME.md", "Where a new session starts"],
  ["log.md", "One line per row worked"],
  ["kit-notes.md", "Notes about the harnesskit instructions themselves"],
];

const csvCell = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
const esc = (v: string) => v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const stepRow = (columns: string[], [id, title, dep, closes, notes]: [string, string, string, string, string]) => {
  const row: Record<string, string> = { id, kind: "task", title, status: "open", priority: "P0", depends_on: dep, closes_when: closes, notes };
  return columns.map((c) => csvCell(row[c] ?? "")).join(",");
};

/** On a re-run, append any seeded step the board lacks (a kit upgrade added it); every existing row is left as it is. */
function addMissingSteps(dest: string): void {
  const board = join(dest, "board.csv");
  if (!existsSync(board)) return;
  const text = readFileSync(board, "utf8");
  const [header, ...lines] = text.trimEnd().split("\n");
  const have = new Set(lines.map((l) => l.split(",")[0]));
  const missing = STEPS.filter(([id]) => !have.has(id));
  if (!missing.length) return;
  const columns = header.split(",");
  writeFileSync(board, text.trimEnd() + "\n" + missing.map((st) => stepRow(columns, st)).join("\n") + "\n");
}

/** Seed writeup/agent-readiness from the mission-control template, filled and rendered. On a re-run, only adds missing seeded steps. */
export function seedReadiness(kitDir: string, target: string): boolean {
  const dest = join(target, "writeup", INITIATIVE);
  if (existsSync(dest)) {
    addMissingSteps(dest);
    return false;
  }
  const src = join(kitDir, "templates/mission-control");
  mkdirSync(dest, { recursive: true });
  cpSync(src, dest, { recursive: true, filter: (f) => basename(f) !== "render-board.mjs" });

  const header = readFileSync(join(src, "board.csv"), "utf8").trimEnd();
  const columns = header.split(",");
  const rows = STEPS.map((st) => stepRow(columns, st));
  writeFileSync(join(dest, "board.csv"), [header, ...rows].join("\n") + "\n");

  const first = STEPS.find(([id]) => id === "t-survey")!;
  const resume = readFileSync(join(dest, "RESUME.md"), "utf8")
    .replace(/\{\{initiative\}\}/g, INITIATIVE)
    .replace(
      /\{\{A few sentences[^]*?\}\}/,
      "Adopting harness-kit in this workspace: finish the install `docs/harnesskit/entry.md` describes, score the workspace against the readiness checklist, and keep a board of the work that closes each gap. It is done when the entrypoint is final, every area is scored, and every missing item is a row.",
    )
    .replace(
      /\{\{The one thing[^]*?\}\}/,
      `${first[1]}. It needs no decision first: it is listing directories and reading headers. Closes when: ${first[3]}.`,
    )
    .replace(/\{\{The calls that[^]*?\}\}/, "None yet.");
  writeFileSync(join(dest, "RESUME.md"), resume);
  writeFileSync(join(dest, "log.md"), "# Log\n\nOne line per row worked, oldest first: the row, what it cost (model, tokens or turns), what it found.\n\n");
  writeFileSync(join(dest, "kit-notes.md"), "# Notes about the kit\n\nWhere setup found the harnesskit instructions unclear or wrong for this repo, one line each: the kit file, what happened, what should change. Kept here for you; nothing reads or sends this file.\n\n");

  const pagePath = join(dest, "mission-control.html");
  const page = readFileSync(pagePath, "utf8")
    .replace(/^<!-- Fill every \{\{token\}\}.*-->\n/m, "<!-- The sections between the BOARD markers are generated by the installed render-board.mjs; never hand-edit them. -->\n")
    .replace(/\{\{initiative\}\}/g, INITIATIVE)
    .replace(/\{\{verdict\}\}/g, esc(VERDICT))
    .replace(/\{\{done\}\}/g, "nothing yet; the kit was just installed and nothing is assessed.")
    .replace(/\{\{waiting_on_you\}\}/g, "nothing yet.")
    .replace(/\{\{next_for_me\}\}/g, `${esc(first[1])}, then the rest of the board in order.`)
    .replace(/\{\{broken\}\}/g, "nothing known.")
    .replace(/\{\{scores_summary\}\}/g, "not scored yet")
    .replace(
      /^.*\{\{area\}\}.*$/m,
      "    <tr><td>All twelve areas</td><td class=\"n\">not scored</td><td>The scoring row on the board: Score the twelve readiness areas</td></tr>",
    )
    .replace(
      /^.*\{\{link\}\}.*$/m,
      FILES.map(([f, what]) => `<tr><td><a href="${f}">${f}</a></td><td class="muted">${what}</td></tr>`).join("\n"),
    );
  writeFileSync(pagePath, page);

  execFileSync(process.execPath, [join(src, "render-board.mjs"), dest], { stdio: "pipe" });
  return true;
}

/** `.harnesskit/` holds proof worktrees; kept out of git through the clone's own .git/info/exclude, so no tracked file changes. */
export function excludeHarnesskit(target: string): void {
  const p = resolve(target, execFileSync("git", ["-C", target, "rev-parse", "--git-path", "info/exclude"], { encoding: "utf8" }).trim());
  const text = existsSync(p) ? readFileSync(p, "utf8") : "";
  if (text.split("\n").includes(".harnesskit/")) return;
  mkdirSync(dirname(p), { recursive: true });
  appendFileSync(p, (text && !text.endsWith("\n") ? "\n" : "") + ".harnesskit/\n");
}
