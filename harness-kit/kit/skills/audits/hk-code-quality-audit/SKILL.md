---
name: hk-code-quality-audit
description: "Find what nobody would miss and what reads as generated, and write it up as one HTML page scored out of 10: dead code, pasted templates, wrappers with one caller, scratch files, hardcoded hosts, unused packages. Triggers on: 'find slop', 'AI slop', 'hygiene check', 'dead code', 'dead endpoints', 'unused dependencies', 'backend slop', 'frontend slop', or /hk-code-quality-audit."
argument-hint: "[backend | frontend | a directory | one category name, default: whole repo]"
allowed-tools: Bash, Read, Grep, Glob, Write, Agent
---

# Code quality audit

Reads the code and writes up, with the lines that show it, what could be deleted and what reads
as generated: dead code, duplicated logic, abstractions with one caller, files in the wrong place,
hardcoded hosts, packages nothing imports. It changes nothing. Secrets and auth are
`hk-security-audit`'s; this skill stops at a hardcoded localhost URL or port.

## Scope

<skill_context> $ARGUMENTS </skill_context>

Empty means the whole repo. `backend` means the API or worker service directory CLAUDE.md
names, `frontend` the frontend source tree it names. All six categories in step 2 run on every
scope. `backend` or `frontend` also adds that layer's list at the end of this file. A path means that path. A category name from step 2 means only that category, over the whole
repo. If the scope holds no source files, say so in one chat line and stop.

## Steps

**1. Map the layout.** Read the root `CLAUDE.md` and the `CLAUDE.md` of each project it lists.
A thing those files call deliberate is not a finding. Note the dependency manifests, the
folders to skip (`node_modules`, `.venv*`, `build`, `dist`, and any orchestration or worktree
state folder CLAUDE.md names), and the env-var fallback pattern the frontend uses. Done when
you can name each project's manifest and its skip list.

**2. Scan the six categories.** Dispatch one subagent per category in one message, each given
the scope, the category text below, and asked for candidates as `file:line`, what is wrong, and
the evidence line. Haiku for files, hosts and dependencies, which are pattern matches; sonnet for
dead code, duplication and abstraction, which need the caller graph. For a scope over roughly
fifty files, the sonnet ones may fan out per top-level directory. The main conversation re-judges every kept finding in step 3.

- **Files.** A Python script in the frontend tree, a test beside its source, a config at the
  wrong level; names like `temp_*`, `scratch_*`, `debug_*`, `old_*`, `backup_*`, one-off
  scripts (`populate_*`, `fix_*`, `quick_*`); logs, databases and build output tracked by git;
  tracked files that belong in `.gitignore`; `TODO`, `FIXME` and `HACK` comments older than
  six months by `git blame`.
- **Dead code.** Unused imports; functions, routes, serializers, tasks, components and hooks
  with no caller; state, props, fields and handlers set and never read; commented-out blocks;
  tests for deleted code; feature flags nothing reads; management commands and scheduled tasks
  nothing invokes.
- **Duplication.** The same logic in two files; one template pasted into several files with
  the names changed; the same validation or business rule written in two modules; the same
  fetch, formatting or empty-state logic in more than one component; two naming conventions in
  one directory; docs that describe behaviour the code no longer has.
- **Abstraction.** A helper, wrapper or service class with one caller or none; a service that
  only forwards to the data layer; a custom hook with one caller that could be inline; props passed
  through two or more components that never read them; a mock or stub standing in for a
  subsystem on a production path; try/except or null checks around code that cannot fail that
  way; a catch that re-raises with nothing added; a comment that restates the line under it; a
  suppressed lint or type error with no reason beside it; a file over 500 lines doing several
  jobs.
- **Dependencies.** Packages in a manifest that nothing imports; two packages for one job; dev
  packages in the runtime list.
- **Hosts.** `localhost`, `127.0.0.1` and fixed ports outside `.env.example`, settings, test
  fixtures, and the env-var fallbacks whose default `.env.example` documents.

Done when all six have reported, including the ones that came back empty. A category is empty when it was searched and nothing survived, never because the scope is a layer.

**3. Verify every candidate by opening the file.** Before calling code dead, grep for the name
as a string: dynamic imports, registries, URL maps, management commands, task names. Before
calling a file misplaced, check the nearest `CLAUDE.md`. Fixtures, examples and templates are
there on purpose. A finding is kept only when you can quote the lines and say who is confused by
them. Done when every kept finding has its quoted lines and every dropped one has a reason.

**4. Set the severity and derive the score.** Severity is 1 to 5, 5 worst, by what it costs the
next reader:

| Severity | Finding |
|---|---|
| 5 | a hardcoded host production code reads; a mock or stub on a production path; two implementations of one rule that already disagree |
| 4 | a file in the wrong place; one template pasted into three or more files; dead state, endpoints or handlers in code people edit weekly; docs that describe behaviour the code no longer has |
| 3 | a wrapper with one caller; two implementations of one rule that still agree; an unused dependency; a stale config |
| 2 | a naming inconsistency; an oversized file; a guard around code that cannot fail; a comment that restates the line |
| 1 | a suppressed lint with no reason; a docstring that names the function again; an old comment |

Score 0 to 10 and the band for each set of findings: [../README.md](../README.md). Inside the band, go to the top when the findings sit in files nobody has touched for months, and to the bottom when a pattern repeats across files.

**5. Build the page.** Read `docs/playbook/tone_and_taste.md` and
`docs/skills/knowledge/hk-writeup/SKILL.md`, then copy
`docs/skills/knowledge/hk-writeup/SHELL.html` to
`$(git rev-parse --show-toplevel)/writeup/code-quality-<scope with slashes replaced by dashes>.html`
(`repo` when the scope is the whole repo) and replace only its `<!-- CONTENT -->` line and its
`<title>`. The shell's style block stays as it is, so this page looks like every other page
under `writeup/`; a stylesheet of your own is the wrong page. In this order:

1. Kicker "Code quality audit", the scope as the title, chips for the commit and the file count
   scanned. The score is a sentence in the Overview, never a chip.
2. **Overview.** Two sentences: what the scoped code does, and the score out of 10 with the
   sentence that derives it; then the finding count per severity, which categories came back
   empty (so the reader knows they were checked); then the
   band table as a `.tbl` in `.tblwrap` so the reader sees which row the findings landed on.
3. **Findings**, one `section` per category, worst first inside it. Severity 3 and up is one
   `details.row` each, numbered F1 onward with the number in a `<span class="sn">`: `.row-name`
   is the severity in words, `.row-status` the title as a sentence; the body holds
   `file:start-end` in `.mono`, the literal four to fifteen lines in a `pre`, one sentence on
   what is wrong, one on what it costs the next reader, one on the fix. Severity 1 and 2 go in
   one `.tbl` per category: file, what, fix.
4. **Patterns seen more than once.** One sentence each, only for patterns that repeat across
   files. Omit the section if none.
5. Footer: read by subagents, verified by hand at the named commit; how many candidates were raised and how many
   dropped in step 3.

**6. Report back** in chat: where the page went, the score, the count per severity, and the
five fixes with the best effort-to-impact ratio. Done when that message is sent.

## Backend scope, added to the categories

Routes registered in a URL map that nothing links to or calls; querysets, serializers, tasks and
models with no caller; the same rule written in a view and again in a serializer; a service class
that only forwards to the data layer; exception handling that catches and re-raises with no added
information.

## Frontend scope, added to the categories

Components exported but never rendered; new object, array or function values created in render
and passed to memoised children or effect dependencies; inline styles that repeat a class already
in the shared stylesheet; `useEffect` that mirrors props into state.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example

- List the projects, for example `backend/`, `frontend/`, `cli/`.
- Say what `backend` and `frontend` mean as paths, and where the shared stylesheet is.
- List the manifests (`requirements.txt`, `package.json`, `pyproject.toml`).
- Name any env-var fallback to leave alone, such as `API_URL || 'http://localhost:8000'`.
- The orchestration and worktree state folder to skip.
