---
name: hk-breadcrumb-creator
description: "Trace a flow through the code and write its breadcrumb (FLOW.md, DETAILS.md, DEBUG.md) under docs/breadcrumb_analysis/, with the index row. Triggers on: 'write a breadcrumb', 'add a breadcrumb', 'trace this flow', 'document this flow', 'write a debug guide for', or /hk-breadcrumb-creator."
argument-hint: "<flow name or description, e.g. 'quota failover' or 'proof upload from agent to task page'>"
---

# /hk-breadcrumb-creator — Write a breadcrumb

A breadcrumb is a debugging reference for people and agents who need to find where a flow breaks; it is not onboarding or architecture prose.

<flow_context> $ARGUMENTS </flow_context>

If the context is empty, take the flow from the conversation; ask only when there is none.

## Step 0: Check what exists

Read `docs/breadcrumb_analysis/_INDEX.md`, then the index of the area the flow belongs to (the same walk `/hk-follow-breadcrumb` does), and decide:

1. **Already covered.** Say which breadcrumb covers it, then extend that one rather than opening a new folder: read its `FLOW.md` first and add only what is missing.
2. **Partly covered.** Extend the existing breadcrumb (new sections, or a new sub-flow folder), or create a new one for the uncovered part with a "See also" line in each direction.
3. **New territory.** Go on to Step 1.

State the decision in chat and proceed:

```
Existing breadcrumbs checked: <folders and what they cover>
Overlap with <X>: <areas>
Decision: <extend X / new breadcrumb for Y / already covered>
```

## Step 1: Scope

```
FLOW: <kebab-case name, used as the folder name>
TRIGGER: <what starts it — button, API call, CLI command, celery beat>
LAYERS: <frontend, api, worker, db, cli, harness>
END STATE: <what the user or system sees when it finishes>
```

Post the block and keep going; the user can narrow it while you trace. If the flow is large, split it into sub-flows (rule and layout under "Splitting into sub-flows" below).

## Step 2: Trace

Work through each layer in execution order, reading the code rather than guessing from file names; one haiku Explore subagent per layer, dispatched in a single message, keeps the reads out of the main conversation. For each hop record the source file and function, what it does (one line), what it passes on (payload shape, ids, status values), and where it goes next.

Where the hops live is workspace-specific; check the workspace's CLAUDE.md for the seams named there, and otherwise expect to find them in places like these:

- **Frontend → backend**: the frontend's API client folder, matched to the backend's URL routing, then the view.
- **Backend → task queue**: enqueue calls in the backend's task/worker package.
- **Backend → CLI**: the CLI package's dispatch and harness modules, and any MCP servers agents call back through.
- **Side effects**: framework signals, create/update hooks, model save overrides — wherever this workspace's ORM or framework puts them.

## Step 3: Write the three files

Folder: `docs/breadcrumb_analysis/<flow-name>/`.

### `FLOW.md`

The 30-second map of where data goes.

```markdown
# <Flow name>

Trigger: <what starts it>
End state: <the outcome>
Hops cite file:line; open only the ranges you will change or quote.

## Flow

<file>:<start>-<end> :: <function>
  → <what it sends or does>
<next file>:<start>-<end> :: <function>
  → ...

## Sub-flows

(only if split — link the sub-flow folders)
```

Every hop cites `path:start-end` and the symbol, so a reader opens only the range. A hop names the exact file, lines and function, then the one or two lines that matter about what it does:

```
<repo>/<file>:<start>-<end> :: <function>()
  → <condition it checks, if any>
  → <what it sends or delegates to next>

<repo>/<next-file>:<start>-<end> :: <function>()
  → <call it makes: method, path, payload shape>

<repo>/<final-file>:<start>-<end> :: <function>() [<http method, if a handler>]
  → <what it validates, what it returns>
```

Keep it linear; label each branch on its own indented block (`[mode=direct]`, `[mode=tmux]`).

### `DETAILS.md`

The file-and-function trace with the logic that matters for debugging. Keep the `**File**:` / `**Function**:` labels exactly: if the workspace has a breadcrumb drift checker (CLAUDE.md, Debugging), it parses these labels to detect drift.

```markdown
# <Flow name> — Detailed trace

## 1. <Layer or step>

**File**: `path/to/file.py`
**Function**: `function_name()`
**Called by**: <what triggers this>
**Calls**: <what it triggers next>

Key logic:
- <conditional, validation or transformation that changes behaviour>
- <side effects: signals, async tasks, external calls>
- <error path: what is raised, what is logged>

Data in: <shape>
Data out: <shape>

---

## 2. <Next step>
```

Skip imports, standard framework validation and plain CRUD.

### `DEBUG.md`

Where to look when this flow breaks.

````markdown
# <Flow name> — Debug guide

## Log locations

| Layer | Where | What's in it |
|-------|-------|--------------|
| <layer> | <log location, per the workspace's CLAUDE.md Debugging section> | <what's in it> |

(only the rows this flow touches)

## What to search for

| Symptom | Where to look | Search term |
|---------|---------------|-------------|
| <failure mode> | <file or log> | <grep pattern> |

## Quick commands

```bash
# <what this checks>
<command>
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|

## Common breakpoints

- `path/to/file.py:function_name()` — <why here>
````

Quick commands are the ones that answer this flow's failures, built from the workspace's diagnostic scripts and log commands (CLAUDE.md, Debugging section).

Write in the house voice (`docs/playbook/tone_and_taste.md`): tables for log locations, env vars and search patterns; code formatting for paths, functions and commands; `[?]` on anything you could not verify.

### Where it goes: the index tree

Breadcrumbs are folders, and the indexes are the tree an agent walks down, reading one branch:

```
docs/breadcrumb_analysis/
├── _INDEX.md              # entry: areas only, one line each
└── <area>/                # a group of flows that share a component or a question family
    ├── _INDEX.md          # area index: one row per page
    └── <flow-name>/       # the page folder: FLOW.md, DETAILS.md, DEBUG.md
```

1. **Pick the area.** Reuse an existing area when the flow shares its component or its questions. A new area (a new folder with its own `_INDEX.md`) is justified only when no existing one fits. Then the flow goes under it as `<area>/<flow-name>/`.
2. **Add the area index row** to `<area>/_INDEX.md`: one line per question or symptom a person would actually type, with the answer in a clause and the page that holds the detail (`path:line` goes in the page, not in the entry file).

```
| Question or symptom | Answer in one clause | Page |
|---|---|---|
| Why is the weekly report empty? | the job reads `reports` before the sync writes it | [reports](reports/DEBUG.md) |
```

3. **Add the area to the entry file** only if it is new: `| [reports](reports/_INDEX.md) | Report building, scheduling and empty-result questions |`. The entry file lists areas only, 5 to 12 rows, no answers and no `path:line`; at 12, merge two areas instead of adding a thirteenth. An entry file with a leaf row, or an area folder without its own `_INDEX.md`, is a depth fault.

A new page is not done until its area index has a row for it and the entry file reaches the area. `check_harness.mjs` (areas `index` and `connections`) reports leaf rows in the entry file, areas without an index, rows without an answer clause or a page, and any page no area index reaches.

### Cover how components connect

A breadcrumb that stays inside one component is the easy half. The links between components are what later work needs most: a feature rarely lives in one folder. While tracing, name every crossing: the frontend call and the route it hits, the code that enqueues a task and the worker that defines it, the table one component writes and another reads, an import between sibling packages. Put both ends in the same page, each as a `path:line` plus the symbol (`client/main.py:12` sends `process_order`, defined in `service/tasks.py:5`). For a feature that spans components, trace the whole path end to end in one flow and give the hand-offs their own rows in the area index. The `connections` area of `check_harness.mjs` detects such links mechanically and lists each one no page names; run it after writing and close what it lists for the flows you touched.

## Step 3.5: Claims check

Before the draft counts as done, run a claims check: give a subagent the draft `FLOW.md` and `DETAILS.md` and have it verify every statement about the code against the code, file by file: the file and function named for each hop, status names, conditions, and any numbers such as poll intervals and retry counts. Fix the lines it finds wrong and record in `DEBUG.md` or the breadcrumb's index row that the check ran and what it changed.

## Step 4: Check completeness

- [ ] Every layer in the scope block has at least one hop in `FLOW.md`, and every hop cites `path:start-end`.
- [ ] `DETAILS.md` has a section for every hop in `FLOW.md`.
- [ ] `DEBUG.md` has a log row for every layer and at least three search-for entries from real failure modes.
- [ ] Every quick command was run once; any that failed is fixed or marked `[?]`.
- [ ] If the workspace has a breadcrumb drift checker (CLAUDE.md, Debugging), run it and confirm it lists no line under `docs/breadcrumb_analysis/<flow-name>/`; drift it reports elsewhere is not this task.
- [ ] The flow sits in an area folder; that area's `_INDEX.md` has a row (answer clause, page link) for the flow and for each symptom in `DEBUG.md`, and the entry `_INDEX.md` lists the area. Every cross-component hop in the flow names both ends.

## Step 5: Report

```
Breadcrumb written for: <flow name>
docs/breadcrumb_analysis/<area>/<flow-name>/
  FLOW.md    — <N> hops
  DETAILS.md — <N> sections
  DEBUG.md   — <N> log sources, <N> search patterns, <N> commands
<area>/_INDEX.md: <N> rows added (entry _INDEX.md: area row added / unchanged)
```

If you extended an existing breadcrumb, name the files touched and what was added instead.

## Splitting into sub-flows

Split when the flow has phases that fail independently (planning vs execution vs assembly), when different agents own different parts, or when `FLOW.md` would pass about 50 lines of flow notation. Layout:

```
docs/breadcrumb_analysis/<area>/<parent-flow>/
├── _INDEX.md          # sub-flows in execution order, one line each, shared env and config (an area index too: the area index row points here)
├── 01-<sub-flow>/
│   ├── FLOW.md
│   ├── DETAILS.md
│   └── DEBUG.md
└── 02-<sub-flow>/
    └── ...
```

`planning-flow/` and `spec-task-lifecycle/` are the existing examples.

## Example

- Where the hops live (Step 2): frontend to backend API calls are in the frontend's API client folder, matched to the backend URL conf, then the view. Backend to background worker is `.delay(` and `.apply_async(` calls. Backend to CLI is the CLI package's dispatch module and the folder per agent harness. Side effects are framework signals, serializer `perform_create` / `perform_update` overrides, model `save()`.
- Drift checker (Step 3, `DETAILS.md`, and Step 4): the breadcrumb checker script in your testing-tools folder, run from the repo root.
- Log locations table (Step 3, `DEBUG.md`):

  | Layer | Where | What's in it |
  |-------|-------|--------------|
  | Backend | the app log file | request/response, view errors |
  | Worker | worker stdout | task dispatch, status transitions |
  | CLI | the CLI's log command | dispatch, agent output, tracebacks |
  | Frontend | browser console | API errors, state updates |

- Quick commands (Step 3, `DEBUG.md`), built from the repo's diagnostics:

  ```bash
  <inspect-script> <id> --brief
  <trace-script> <job_id>
  tail -f backend/logs/app.log
  ```

- A real `FLOW.md` hop, in the shape to copy:

  ```
  cli/mcp/server.py:212-240 :: add_comment()
    → Detects comment_type="proof"
    → No screenshot_paths → delegates directly to submit_proof()

  cli/tools/core.py:88-110 :: ToolClient.submit_proof()
    → POST /items/{id}/comments/ {content, comment_type: "proof", attachments: [...]}

  backend/views.py:301-330 :: ItemViewSet.comments() [POST]
    → Validates via CreateCommentSerializer, creates the comment, returns 201
  ```
