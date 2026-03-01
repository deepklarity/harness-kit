# Activity and Comments

## The problem

Open a task that just finished running. What do you see?

```
odin@harness.kit — metadata changed from {"complexity": "low", "quota_snapshot":
    {"remaining_pct": 100.0, "usage_pct": 0.0}, "reasoning": "Simple file assembly
    task. MiniMax is low cost with 100% remaining quota...
odin@harness.kit — metadata changed from {"complexity": "low"...} to {...}
odin@harness.kit — depends_on changed from [] to ["72"]
odin@harness.kit — assignee_id changed from None to minimax
odin@harness.kit — status changed from BACKLOG to TODO
odin@harness.kit — created changed from to Task created
```

Eleven entries. Five are JSON blobs. Everything says `odin@harness.kit`. Where's the actual work? What agent ran? How long did it take? What did it produce?

The result field is worse. It contains the raw stream-json from the agent session — every `step_start`, `tool_use`, `step_finish` event, session IDs, tool input/output payloads. Thousands of characters of machine trace dumped into a single `TaskHistory` entry.

This isn't an activity feed. It's a database changelog.

---

## Two things, not one

There are two fundamentally different actions on a task:

**Mutations** — automatic recordings of field changes. Status moved from TODO to IN_PROGRESS. Assignee changed from None to minimax. Metadata got a new key. These happen mechanically every time a field is updated. They're useful for audit, flow measurement, and debugging.

**Comments** — deliberate notes left by an agent or human. "Completed in 12.3s, 8,420 tokens. Assembled final HTML from both section files into mini_poem.html." Or a human: "Looks good but change the tone." These are intentional communication. Someone chose to say something.

Right now, both are crammed into `TaskHistory` — a flat table of field diffs. Comments don't exist as a concept. Odin has a `Comment` model in its Pydantic layer, but those never reach Django. When an agent finishes and Odin calls `add_comment(task_id, agent_name, summary)`, the comment gets written to a local JSON file and silently dropped when syncing to TaskIt.

The agent's execution summary — the proof of work, the metrics, the human-readable explanation of what happened — vanishes.

---

## Comments

A comment is a deliberate message attached to a task. Agents leave them. Humans leave them. They're the primary content a person reads when they open a task.

When an agent finishes a task, it posts a comment:

```
Completed in 12.3s · 8,420 tokens (5,200 in / 3,220 out)

Assembled final HTML from both section files into mini_poem.html.
The MiniMax and Qwen paragraphs are combined with proper HTML structure.
```

The first line carries execution metrics — duration, token usage. The rest is the agent's summary. Together, this is the proof of work in human-readable form.

When a human has something to say, they type a comment:

```
The poem flows well. Can you make the closing paragraph more hopeful?
```

Comments are append-only. You don't edit them, you don't delete them. If an agent posts its summary and then the task gets retried, the new attempt posts a new comment. Both stay. The history is the history.

Comments carry a `comment_type` that determines their UI treatment and behavior:

| Type | Purpose | UI Treatment |
|------|---------|-------------|
| `status_update` | Progress visibility during execution | Standard comment |
| `question` | Agent needs a human decision (blocks until reply) | Highlighted, reply input |
| `proof` | Verification evidence before completion | Proof badge, file links |
| `debug` | Effective input/output for post-hoc inspection | Hidden by default |

This isn't bureaucracy — it's how agents communicate as they work, not just after. The taxonomy lets the dashboard show the right things: questions need action (highlight them), proof is the deliverable (badge it), debug logs are for forensics (hide them). See [Agent Communication](communication.md) for the philosophy behind live agent communication.

### The model

```python
class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author_email = models.EmailField()
    author_label = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    attachments = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

That's it. No type enums, no threading, no reactions. The simplest thing that works.

### The bridge

Odin already calls `task_mgr.add_comment()` after execution. The `TaskItBackend` just needs to POST it to Django instead of silently dropping it:

```
POST /tasks/:id/comments/
{
    "author_email": "minimax+MiniMax-M2.5@odin.agent",
    "author_label": "minimax (MiniMax-M2.5)",
    "content": "Completed in 12.3s · 8,420 tokens\n\nAssembled final HTML..."
}
```

The orchestrator composes the comment with metrics from `TaskResult`:

```python
metrics_parts = []
if result.duration_ms:
    metrics_parts.append(f"{result.duration_ms / 1000:.1f}s")
usage = result.metadata.get("usage", {})
total = usage.get("total_tokens")
if total:
    input_t = usage.get("input_tokens") or usage.get("prompt_tokens")
    output_t = usage.get("output_tokens") or usage.get("completion_tokens")
    metrics_parts.append(f"{total:,} tokens ({input_t:,} in / {output_t:,} out)")

metrics_line = " · ".join(metrics_parts)
summary_text = summary or "Completed successfully"
comment = f"Completed in {metrics_line}\n\n{summary_text}" if metrics_line else summary_text
```

Metrics that currently live in `CostStore` (local disk, never reaches Django) or are buried in `task.metadata` JSON diffs — now they're in the comment, readable, right there on the task card.

---

## Actor identity

Everything says `odin@harness.kit`. That's wrong. When minimax writes a poem section using MiniMax-M2.5, the history should say minimax, not odin. When claude plans the decomposition using sonnet-4-5, the history should say claude, not odin.

### The format

`{agent}+{model}@odin.agent`

Examples:
- `claude+sonnet-4-5@odin.agent` — claude planned the decomposition
- `minimax+MiniMax-M2.5@odin.agent` — minimax executed the task
- `codex+codex-mini@odin.agent` — codex ran a code task
- `odin@harness.kit` — Odin itself, for orchestration actions with no specific agent (creating the spec, initial task creation)

The `+` delimiter in email local parts is RFC 5321 compliant. Django's `EmailField` validates it fine.

### Where it flows

The identity appears in `TaskHistory.changed_by` and `TaskComment.author_email`. It's a string, not a foreign key. No User records are created per agent+model combo — models change too frequently for that. The frontend parses the string:

```typescript
function parseActor(email: string) {
  if (email.endsWith("@odin.agent")) {
    const [agent, model] = email.split("@")[0].split("+");
    return { agent, model, display: `${agent} (${model})` };
  }
  if (email === "odin@harness.kit") {
    return { agent: "odin", display: "odin" };
  }
  return { display: email };  // human
}
```

### Context matters

Not every mutation has agent context. The flow:

- **Planning** — Odin calls the planner LLM (e.g., claude). Tasks get created. `changed_by` is `claude+sonnet-4-5@odin.agent` because claude did the planning.
- **Orchestration** — Odin sets `depends_on`, adjusts metadata, assigns agents. `changed_by` is `odin@harness.kit` because this is Odin's own logic, not an agent.
- **Execution** — An agent runs and produces output. Status changes, result updates. `changed_by` is `minimax+MiniMax-M2.5@odin.agent` because minimax did the work.
- **Human** — A person edits the description or leaves a comment. `changed_by` is their email.

This means the `_created_by` field in `TaskItBackend` can't be a single fixed value anymore. It needs to be set per-operation based on who's actually doing the work.

---

## Activity: what to show

TaskHistory records everything. Every field change, every metadata diff, every depends_on update. That's correct — the audit trail is sacred. The question is what to surface.

### The human view

Two things matter when you open a task:

1. **Comments** — what agents and humans said. The proof, the feedback, the notes.
2. **Status and assignment changes** — the lifecycle: created, assigned to minimax, started, completed. Or: created, assigned to gemini, started, failed, reassigned to claude, started, completed.

That's it. Those are the events that tell the story.

### Behind the toggle

Everything else — metadata diffs, depends_on changes, result field mutations, dev_eta_seconds updates — goes behind a "Show all history" toggle. It's there when you need to debug. It's not in your face when you're reviewing work.

The classification is purely frontend:

```typescript
const VISIBLE_FIELDS = new Set(["created", "status", "assignee_id"]);

function isVisible(mutation: TaskHistory): boolean {
  return VISIBLE_FIELDS.has(mutation.field_name);
}
```

Three fields visible by default. Everything else collapsed. Simple rule, no ambiguity.

---

## What changes where

### Django (TaskIt backend)

- New `TaskComment` model and migration
- `POST /tasks/:id/comments/` and `GET /tasks/:id/comments/` endpoints
- Dashboard serializer includes `comments` alongside existing `history`
- No changes to `TaskHistory` — it keeps recording everything exactly as before

### Odin

- `TaskItBackend.add_comment()` — POSTs comments to Django instead of dropping them
- `add_comment()` signature gets `model_name` parameter
- Orchestrator composes metrics-inline comments after execution
- `changed_by` / `updated_by` uses `{agent}+{model}@odin.agent` format
- `_created_by` becomes contextual, not a fixed `odin@harness.kit`

### Frontend

- Comments section above activity, with "Add a comment" input for humans
- Activity section shows only status + assignee changes
- "Show all history" toggle for the full mutation log
- Actor parsing: extract agent name and model from email format

### What doesn't change

- `TaskHistory` records all mutations (audit trail untouched)
- `task.result` field stays (machine-readable, used by DAG/assembly)
- `task.metadata` stays (Odin continues writing to it)
- `CostStore` stays as local disk fallback
- Odin's local `Comment` Pydantic model stays for the disk backend

---

## Scenarios

### Agent completes a task

Minimax finishes assembling an HTML file. The orchestrator:

1. Sets `task.result` to the clean output (existing behavior, unchanged)
2. Sets `task.status` to DONE (existing behavior)
3. Posts a comment: "Completed in 12.3s · 8,420 tokens\n\nAssembled final HTML from both section files into mini_poem.html"

On the task card, the human sees the comment with metrics and summary. The activity shows "DONE ← IN_PROGRESS · minimax (MiniMax-M2.5) · 4:25 PM". The result is in the result section. Everything is where it should be.

### Task fails and gets retried

Gemini fails a coding task. The orchestrator posts a comment: "Failed in 23.1s · 15,200 tokens\n\nError: syntax error in generated Python file."

The human reassigns to claude. Claude runs, succeeds. The orchestrator posts: "Completed in 45.0s · 28,100 tokens\n\nFixed syntax and completed implementation."

Now the task has two comments — the failure and the success. The activity shows: assigned to gemini → started → failed → reassigned to claude → started → completed. Both agents' identities are visible. The human reads the story top to bottom.

### Human leaves feedback

A human opens the task, reads the result, and types: "The HTML structure is good but the poem text is too generic. Can we make it more specific to the project?"

The comment appears with their email. If the task gets retried (status back to TODO, reassigned), the feedback is right there for the next agent to read in the task description or for the human to reference.

### Debugging a stuck task

A task has been IN_PROGRESS for 10 minutes. Something is wrong. The human opens it.

Comments section: empty (agent hasn't finished yet). Activity: "IN_PROGRESS ← TODO · codex (codex-mini) · 4:15 PM". That's enough to know who's running and when it started.

If they need more: "Show all history" reveals metadata changes — started_at timestamp, tmux session ID, subprocess PID. Debugging info, available when needed, not cluttering the default view.
