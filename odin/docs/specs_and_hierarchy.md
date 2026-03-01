# Specs, Hierarchy, and a Day of Real Work

## The Problem

When `odin plan sample_specs/poem_spec.md` runs, tasks appear on the board but they don't know *why they exist*. The spec that created them is recorded in `.odin/run.json` — a singleton file that the next `odin plan` overwrites. Run two specs in a day and the first one's provenance is gone.

In a real project:
- Multiple specs land on the same board throughout the day
- A failing task needs to trace back to its spec to understand intent
- Re-planning a spec shouldn't nuke unrelated tasks
- `odin status` should be readable, not a wall of unrelated tasks

## The Key Insight: Spec Status is Derived, Not Stored

My first design was wrong. I proposed a `Spec` entity with its own stored status lifecycle (`planned → executing → done → failed`). That creates two sources of truth — the spec says "executing" but what do the tasks actually say? Now you have sync bugs.

**The corrected design:** Spec status is always computed from its tasks. The tasks are the single source of truth. A spec is a **tag** on tasks plus a **content archive**. Nothing more.

```
Spec status = f(tasks where spec_id == this spec)

  All tasks pending/assigned     →  planned
  Any task in_progress           →  active
  All tasks completed            →  done
  Any task failed, none running  →  blocked
  Flagged abandoned by human     →  abandoned
```

No stored status. No status transitions to manage. No sync bugs. You look at the tasks, you know the spec's state.

## What a Spec Actually Is

A spec is two things:

### 1. A tag on tasks (`spec_id`)

Every task gets a `spec_id` field. That's the grouping mechanism. It's just a string on the Task model — same as `assigned_agent` or `parent_task_id`. Taskit already supports filtering by fields; this is one more filter.

```python
class Task(BaseModel):
    id: str
    title: str
    spec_id: Optional[str] = None   # ← this is the entire hierarchy
    # ... everything else unchanged
```

### 2. A content archive

When you `odin plan sample_specs/poem_spec.md`, Odin snapshots the spec content so you can always answer: "what was the original intent?" This is a lightweight JSON file, not a stateful entity.

```
.odin/specs/
├── sp_a1b2c3.json    # { id, title, source_file, content, created_at, abandoned: false }
├── sp_d4e5f6.json
└── index.json        # { id: title } for quick listing
```

The archive stores:
- `id` — short hex, same format as task IDs
- `title` — derived from filename or first heading
- `source` — file path or `"inline"`
- `content` — full spec text, frozen at plan time
- `created_at` — when planned
- `abandoned` — boolean, only flag the human can set (overrides derived status)
- `metadata` — plan_agent, plan_duration_ms, etc.

**No `status` field. No `task_ids` list.** Status is derived from tasks. Task membership is derived by querying `task_mgr.list_tasks(spec_id=X)`.

## How Specs Come In

Specs arrive naturally through the existing `plan` command. Nothing changes about the entry point — the output just carries more structure.

```bash
# File-based (most common)
odin plan specs/user_profile_api.md

# Inline
odin plan --prompt "Fix the auth token refresh bug"

# Future: from an issue tracker, a webhook, etc.
# The spec archive makes this easy — it's just id + content + source
```

Each `plan` invocation creates a new spec archive entry and tags all resulting tasks with the spec_id. Multiple plans in a day = multiple specs on the board, each with their own tasks, coexisting peacefully.

## Views: How You Look at the Board

This is where the tag model pays off. You get multiple views of the same underlying data (tasks), sliced different ways.

### The Task View (default, backwards-compatible)

```bash
odin status
```

Flat list of all tasks. Same as today. But now each task shows its spec tag, so you can see at a glance which tasks belong together.

```
┌──────────┬────────────────────────┬──────────┬────────┬──────────┬────────────────────────┐
│ ID       │ Title                  │ Status   │ Agent  │ Spec     │ Deps                   │
├──────────┼────────────────────────┼──────────┼────────┼──────────┼────────────────────────┤
│ a1b2c3d4 │ Create Pydantic models │ done     │ gemini │ profile  │ -                      │
│ d4e5f6a1 │ GET /profile endpoint  │ done     │ codex  │ profile  │ a1b2c3d4               │
│ f6a1b2c3 │ PUT /profile endpoint  │ failed   │ codex  │ profile  │ a1b2c3d4               │
│ b2c3d4e5 │ Integration tests      │ assigned │ gemini │ profile  │ d4e5f6a1, f6a1b2c3     │
│ 1234abcd │ Diagnose token expiry  │ done     │ claude │ auth-fix │ -                      │
│ 5678ef01 │ Fix + regression test  │ done     │ claude │ auth-fix │ 1234abcd               │
│ 9abc0123 │ Write hero copy        │ done     │ qwen   │ landing  │ -                      │
│ 4567def8 │ Feature descriptions   │ done     │ gemini │ landing  │ -                      │
│ 890abcde │ Assemble HTML          │ done     │ codex  │ landing  │ 9abc0123, 4567def8     │
└──────────┴────────────────────────┴──────────┴────────┴──────────┴────────────────────────┘
```

The **Spec** column is the tag. It shows a short label (derived from spec title). This is the "spec as tag in a task view" — you see specs inline with tasks, as a grouping/filtering dimension.

### The Spec View (new, summary level)

```bash
odin specs
```

One row per spec. Status is derived by looking at the tasks.

```
┌────────────┬──────────────────────────┬──────────┬───────────────────────────────┐
│ Spec       │ Title                    │ Status   │ Tasks                         │
├────────────┼──────────────────────────┼──────────┼───────────────────────────────┤
│ sp_a1b2c3  │ User Profile API         │ blocked  │ 2 done, 1 failed, 1 waiting  │
│ sp_d4e5f6  │ Fix Auth Token Refresh   │ done     │ 2 done                       │
│ sp_789abc  │ Landing Page Refresh     │ done     │ 3 done                       │
└────────────┴──────────────────────────┴──────────┴───────────────────────────────┘
```

**"blocked"** — not "failed". Because failure is a human decision. The spec has a failed task, but the human might retry it. The system says "blocked" (something needs attention), the human decides if it's actually failed.

### The Drilldown View (spec → tasks)

```bash
odin spec show sp_a1b2c3
```

Shows the spec content + all its tasks. This is the "spec → task drilldown."

```
Spec sp_a1b2c3 — "User Profile API"
Source: specs/user_profile_api.md
Planned: 2026-02-16 09:03:12
Status: blocked (1 failed, 1 waiting)

Original Spec:
  Build GET and PUT endpoints for /api/v1/profile with Pydantic
  validation. Include integration tests. Use the existing User model
  from models.py...

Tasks:
  a1b2c3d4  Create Pydantic models       gemini  done      -
  d4e5f6a1  GET /profile endpoint         codex   done      deps: a1b2
  f6a1b2c3  PUT /profile endpoint         codex   failed    deps: a1b2   ← needs attention
  b2c3d4e5  Integration tests             gemini  assigned  deps: d4e5, f6a1  (blocked)
```

### Filtered Task View (by spec)

```bash
odin status --spec sp_a1b2c3
```

Same as the flat task view, but only showing tasks for this spec. Also available:

```bash
odin status --agent claude       # Cross-spec: all Claude tasks
odin status --status failed      # Cross-spec: all failed tasks
```

These are just filters on the task list. The spec is one filter dimension among several.

## Spec Status Derivation Rules

Since status is computed, the rules need to be precise:

| Condition | Derived Status | Meaning |
|-----------|---------------|---------|
| `abandoned` flag is true | **abandoned** | Human killed it. Overrides everything. |
| All tasks `completed` | **done** | All work finished successfully. |
| Any task `in_progress` | **active** | Work is happening right now. |
| Any task `failed`, none `in_progress` | **blocked** | Something broke, needs human attention. |
| All tasks `assigned` (none started) | **planned** | Ready to execute, waiting for `odin exec`. |
| All tasks `pending` | **draft** | Tasks created but not assigned yet. |
| Mix of `completed` + `assigned` (none failed, none running) | **partial** | Some waves done, more to go. Between waves. |

**Priority order** (first match wins):
1. abandoned (flag)
2. active (any in_progress)
3. blocked (any failed, none in_progress)
4. done (all completed)
5. partial (some completed, some assigned)
6. planned (all assigned)
7. draft (all pending)

This is a pure function: `derive_spec_status(tasks: List[Task], abandoned: bool) -> str`. No stored state to get stale.

## The Abandoned Flag: The One Exception

"Abandoned" is the only piece of spec state that's stored rather than derived, because it's a human override. You can't derive "the human gave up" from task statuses — a spec with 3 assigned tasks and 1 failed task could be "blocked" (human will retry) or "abandoned" (human moved on). Only the human knows.

```bash
odin spec abandon sp_a1b2c3
# Sets abandoned=true in the spec archive
# Does NOT delete or modify tasks — they're historical evidence
# Future `odin specs` shows it as "abandoned"
```

## What Happens to run.json?

It goes away. Currently `run.json` stores:
```json
{ "spec": "...", "spec_file": "...", "working_dir": "...", "task_ids": [...], "status": "..." }
```

With the spec model:
- `spec` + `spec_file` → in the spec archive
- `task_ids` → derived by `list_tasks(spec_id=X)`
- `status` → derived from tasks
- `working_dir` → stored in spec archive metadata

For `odin exec` (no arguments), the orchestrator gathers all tasks across all non-abandoned specs that are ready to run. No manifest needed.

---

## A Day of Real Work: Simulation

### 9:00 AM — Morning Planning

Alice has three pieces of work:

```bash
alice$ odin plan specs/user_profile_api.md
# Creates spec sp_a1b2, tags 4 tasks with spec_id=sp_a1b2

alice$ odin plan specs/fix_auth_bug.md
# Creates spec sp_d4e5, tags 2 tasks with spec_id=sp_d4e5

alice$ odin plan specs/landing_page.md
# Creates spec sp_789a, tags 3 tasks with spec_id=sp_789a
```

### 9:10 AM — The Board at a Glance

```bash
alice$ odin specs

  sp_a1b2  User Profile API          planned   4 tasks (0 done)
  sp_d4e5  Fix Auth Token Refresh     planned   2 tasks (0 done)
  sp_789a  Landing Page Refresh       planned   3 tasks (0 done)
```

Three specs, nine tasks. Each task knows its spec. Each spec's status is just a readout of its tasks.

### 9:15 AM — Review Assignments, Adjust

```bash
alice$ odin status
```

Flat task list with spec tags. Alice scans, reassigns two tasks:

```bash
alice$ odin assign t_007 claude    # Landing page hero → claude
alice$ odin assign t_008 claude    # Landing page features → claude
```

### 9:30 AM — Execute Everything

```bash
alice$ odin exec
```

Odin gathers all assigned tasks across all specs. Dependencies are respected within specs (and across specs if explicit, but usually not). Waves execute:

- **Wave 1**: t_001 (profile/models), t_005 (auth/diagnose), t_007 (landing/hero), t_008 (landing/features)
- **Wave 2**: t_002, t_003 (profile/endpoints), t_006 (auth/fix), t_009 (landing/assemble)
- **Wave 3**: t_004 (profile/tests)

During execution, `odin specs` shows:

```
  sp_a1b2  User Profile API          active    4 tasks (1 done, 2 running, 1 waiting)
  sp_d4e5  Fix Auth Token Refresh     active    2 tasks (1 done, 1 running)
  sp_789a  Landing Page Refresh       active    3 tasks (2 done, 1 running)
```

### 10:00 AM — Something Goes Wrong

t_003 (PUT /profile) fails.

```bash
alice$ odin specs

  sp_a1b2  User Profile API          blocked   4 tasks (2 done, 1 failed, 1 waiting)
  sp_d4e5  Fix Auth Token Refresh     done      2 tasks (2 done)
  sp_789a  Landing Page Refresh       done      3 tasks (3 done)
```

"blocked" — not "failed". The spec isn't dead, it just needs attention. Alice drills down:

```bash
alice$ odin spec show sp_a1b2
```

Sees the full spec content, sees t_003 failed, reads the error. Fixes the underlying file. Retries:

```bash
alice$ odin exec t_003     # Retry just the failed task
alice$ odin exec t_004     # Now run the unblocked test task
```

```bash
alice$ odin specs

  sp_a1b2  User Profile API          done      4 tasks (4 done)     ← healed
  sp_d4e5  Fix Auth Token Refresh     done      2 tasks (2 done)
  sp_789a  Landing Page Refresh       done      3 tasks (3 done)
```

### 11:00 AM — New Work Arrives

```bash
alice$ odin plan specs/dark_mode_toggle.md
# New spec appears on the board alongside the completed ones
```

```bash
alice$ odin specs

  sp_a1b2  User Profile API          done      4 tasks (4 done)
  sp_d4e5  Fix Auth Token Refresh     done      2 tasks (2 done)
  sp_789a  Landing Page Refresh       done      3 tasks (3 done)
  sp_cc01  Dark Mode Toggle           planned   5 tasks (0 done)

alice$ odin exec --spec sp_cc01    # Execute just the new spec
```

### 2:00 PM — Quota Pressure

Claude at 82%. Alice checks cross-spec:

```bash
alice$ odin status --agent claude

  1234abcd  Diagnose token expiry     claude  done      auth-fix
  5678ef01  Fix + regression test     claude  done      auth-fix
  t_007     Write hero copy           claude  done      landing
  t_008     Feature descriptions      claude  done      landing
  t_014     Validate dark mode        claude  assigned  dark-mode  ← hasn't run yet
```

She reassigns the pending Claude task to gemini:

```bash
alice$ odin assign t_014 gemini
```

### 3:00 PM — Abandon Stale Work

Yesterday's spec that never got executed:

```bash
alice$ odin spec abandon sp_old_one
# Flag set. Tasks untouched. Shows as "abandoned" in odin specs.
```

### 5:00 PM — End of Day

```bash
alice$ odin specs

  sp_a1b2  User Profile API          done       4/4 done
  sp_d4e5  Fix Auth Token Refresh     done       2/2 done
  sp_789a  Landing Page Refresh       done       3/3 done
  sp_cc01  Dark Mode Toggle           done       5/5 done
  sp_old1  Refactor logging (Feb 15)  abandoned  0/3 done
```

Clean board. Every task traces to its spec. Every spec's status is a truthful readout of its tasks.

---

## Failure Modes

### 1. Task Fails

Spec status → **blocked**. Human retries, reassigns, or abandons the spec. The spec never auto-transitions to "failed" because that's a human judgment.

### 2. Agent Unavailable

Task can't execute. Stays `assigned`. Spec status → **planned** or **partial** (depending on whether other tasks completed). Human reassigns agent.

### 3. Bad Decomposition

```bash
odin spec replan sp_a1b2
```

This:
1. Reads the spec content from the archive
2. Gathers completed task results as context ("these were already done: ...")
3. Re-decomposes with the enriched context
4. Creates new tasks tagged with the same spec_id
5. Marks old incomplete tasks as `abandoned` (a task-level status, not spec-level)

The spec_id doesn't change. The spec is the same intent — just better decomposed now.

### 4. Conflicting Specs

Two specs touch the same file. Odin warns during planning (heuristic: check if task descriptions mention the same file paths). Human sequences them or accepts the risk.

No cross-spec dependencies. Specs are independent. If you need ordering between specs, you run them sequentially: `odin exec --spec A && odin exec --spec B`.

### 5. Stale Board

```bash
odin specs --stale             # Specs with no task activity in 24h+
odin cleanup --abandoned       # Delete abandoned spec archives + their task files
```

### 6. Silent Wrong Output

Can't detect automatically. Mitigations:
- Planner should include review/validation tasks for important work
- `odin show <task>` makes inspection easy
- Comments on tasks provide a feedback channel between review tasks and work tasks

---

## Design Decisions

### Spec is a Tag, Not an Entity with a Lifecycle

The spec archive exists for content preservation (the original intent document) and for the abandoned flag. Everything else — status, task membership, progress — is derived from tasks. This means:

- No status sync bugs
- Taskit stays the single source of truth
- Adding spec support is additive (one new field on Task, one new directory for archives)
- All existing task operations work unchanged

### Status is "blocked", Not "failed"

A spec with a failed task is "blocked", not "failed". Failure is a human decision — the system can't know if the human will retry, reassign, or replan. "Blocked" is a factual description: something needs attention. The human owns the "abandon" action.

### No Cross-Spec Dependencies

Specs are independent units of intent. Making specs depend on each other couples them, which breaks the ability to plan/execute/abandon them independently. If two specs truly depend on each other, they should be one spec. If they conflict on shared resources, the human sequences them.

### Two Levels Only

Spec → Task. No nesting of specs, no sub-tasks-of-tasks through the spec system. If a task is too big, split the spec. The DAG (`depends_on`) handles ordering within a spec. This eliminates rollup-complexity and keeps the mental model simple.

### The Spec Title as a Short Tag

In the task view, the spec column shows a short tag (not the full ID). Derived from:
1. Filename without extension: `specs/user_profile_api.md` → `profile-api`
2. Or first heading: `# Fix Auth Token Refresh` → `auth-fix`
3. Or truncated title: `Dark Mode Toggle` → `dark-mode`

This makes the task view scannable. The full spec ID is used for commands (`odin spec show sp_a1b2`).

---

## Implementation

### What changes in Taskit

**Task model** — one new optional field:

```python
class Task(BaseModel):
    # ... existing fields ...
    spec_id: Optional[str] = None
```

**TaskManager** — `list_tasks` already supports keyword filters. Add `spec_id`:

```python
def list_tasks(self, status=None, agent=None, parent_id=None, spec_id=None):
    # ... existing filters ...
    if spec_id:
        tasks = [t for t in tasks if t.spec_id == spec_id]
```

**TaskStore** — index gets a `spec_id` column:

```python
index[task.id] = {
    "title": task.title,
    "status": task.status.value,
    "assigned_agent": task.assigned_agent,
    "spec_id": task.spec_id,    # ← new
}
```

### What's new

**Spec archive** — lightweight store in `.odin/specs/`:

```python
class SpecArchive(BaseModel):
    id: str
    title: str
    source: str              # file path or "inline"
    content: str             # full spec text, frozen at plan time
    created_at: datetime
    abandoned: bool = False
    metadata: Dict[str, Any] = {}

class SpecStore:
    """Read/write spec archives. No status — that's derived from tasks."""

    def save(self, spec: SpecArchive) -> None: ...
    def load(self, spec_id: str) -> Optional[SpecArchive]: ...
    def load_all(self) -> List[SpecArchive]: ...
    def set_abandoned(self, spec_id: str) -> None: ...
```

**Derived status function:**

```python
def derive_spec_status(tasks: List[Task], abandoned: bool) -> str:
    if abandoned:
        return "abandoned"
    if not tasks:
        return "empty"

    statuses = [t.status for t in tasks]

    if all(s == TaskStatus.COMPLETED for s in statuses):
        return "done"
    if any(s == TaskStatus.IN_PROGRESS for s in statuses):
        return "active"
    if any(s == TaskStatus.FAILED for s in statuses):
        return "blocked"
    if any(s == TaskStatus.COMPLETED for s in statuses):
        return "partial"
    if all(s == TaskStatus.ASSIGNED for s in statuses):
        return "planned"
    return "draft"
```

### What changes in the Orchestrator

- `plan()` creates a SpecArchive, sets `spec_id` on all tasks it creates
- `exec_all()` accepts optional `spec_id` filter
- `run.json` is replaced by spec-based discovery

### What changes in the CLI

- `odin status` adds a Spec column (the short tag)
- `odin specs` — new command, lists spec summaries with derived status
- `odin spec show <id>` — new command, drilldown view
- `odin spec abandon <id>` — new command
- `odin spec replan <id>` — new command
- `odin status --spec <id>` — filter flag
- `odin status --agent <name>` — filter flag
- `odin exec --spec <id>` — filter flag

---

## What Does NOT Change

- Task model (minus one new optional field)
- DAG execution (waves, dependencies, fail-fast)
- Agent assignment (suggestive defaults, human override)
- Harness system
- Config system
- "Everything is a task" — assembly, review, testing are still tasks. They just know their spec now.
