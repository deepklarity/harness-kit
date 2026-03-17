---
name: hk-local-diagnose
description: "Run diagnostic scripts on tasks, specs, boards, or reflections. Wraps testing_tools/ with proper working directory and output mode selection. Use this skill whenever the user wants to inspect, debug, or check the state of a task, spec, board, or reflection — even if they don't say 'inspect' explicitly. Triggers on: 'why did task X fail', 'show me spec Y', 'what's on the board', 'check task', 'inspect', 'diagnose', or /hk-local-diagnose."
argument-hint: <type> <id> [--brief | --full | --json] [--sections a,b]
allowed-tools: Bash, Read
---

# /hk-local-diagnose — Diagnostic Script Runner

Run the right diagnostic script with the right flags, from the right directory. No more remembering paths or cd-ing around.

## Usage

```
/hk-local-diagnose task 42
/hk-local-diagnose task 42 --brief
/hk-local-diagnose spec 15 --json --sections tasks,problems
/hk-local-diagnose board
/hk-local-diagnose board 3
/hk-local-diagnose reflection 8 --full
/hk-local-diagnose snapshot sp25 ../../tests/e2e_snapshots/smoke
```

## Arguments

Parse `$ARGUMENTS` to extract:
- **type** (required): `task`, `spec`, `board`, `reflection`, or `snapshot`
- **id** (required for all except `board`): the numeric ID or spec prefix
- **flags** (optional): `--brief`, `--full`, `--json`, `--slim`, `--sections <list>`

If no flags are provided, default to `--brief` — this is the token-efficient choice for LLM consumption. The user can always ask for `--full` if they need more.

## Script mapping

| Type | Script | Required args |
|------|--------|---------------|
| `task` | `task_inspect.py <id>` | id |
| `spec` | `spec_trace.py <id>` | id |
| `board` | `board_overview.py [id]` | id optional |
| `reflection` | `reflection_inspect.py <id>` | id |
| `snapshot` | `snapshot_extractor.py <id> <output_dir>` | id + output_dir |

## Execution

All scripts run from the `taskit/taskit-backend/` directory. The working directory for execution is always:

```
REPO_ROOT/taskit/taskit-backend/
```

Where `REPO_ROOT` is the git repository root (find it with `git rev-parse --show-toplevel`).

### Step 1: Resolve the repo root

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

### Step 2: Build and run the command

```bash
cd "$REPO_ROOT/taskit/taskit-backend" && python testing_tools/<script> <id> [flags]
```

Pass through any `--brief`, `--full`, `--json`, `--slim`, or `--sections` flags directly to the script.

### Step 3: Display the output

Print the script output directly. Do not summarize or interpret — the scripts already produce well-structured output with problem detection built in.

If the script exits with a non-zero code, show the error and suggest:
- Check that the ID exists: "Is task/spec/board {id} a valid ID?"
- Check that the Django app is set up: "Is the taskit-backend database accessible?"

## Error handling

If `$ARGUMENTS` is empty or missing the type, print usage help:

```
Usage: /hk-local-diagnose <type> <id> [flags]

Types: task, spec, board, reflection, snapshot
Flags: --brief (default), --full, --json, --slim, --sections <list>

Examples:
  /hk-local-diagnose task 42
  /hk-local-diagnose spec 15 --json --sections tasks,problems
  /hk-local-diagnose board
```
