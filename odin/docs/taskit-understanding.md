# TaskIt

## TLDR

- TaskIt is the digital twin of the development process. Humans and AI agents work on the same board. Everything that happens is visible.
- The board is a control surface, not a monitor. Humans steer from it — edit tasks, reassign agents, review results.
- Every completed task carries proof — the actual output, not just a status flag.
- History exists to measure flow (time-in-status) and trace accountability. Not to dump machine logs at people.
- The UI should serve human eyes. If it looks like JSON, it's wrong.

---

## What TaskIt is

Picture a team building something — could be a mobile app, a backend service, anything. Some of the team are humans, some are AI agents. Tasks live on a shared board. Some get delegated to agents, some are paired human+AI, some are pure human work. The board is where all of it converges.

TaskIt is that board — the digital twin of the development process. Like Trello/Jira, but built for a world where part of your team is AI.

The Odin CLI is one way to put work on the board (decompose a spec into tasks, assign agents, execute). But the board stands on its own. A human can create tasks, drag them around, edit them, review agent output — all without touching the CLI.

## What matters

### The board shows reality

If an agent is working, you see it. If something failed, you see it. If a task has been stuck in TODO for hours, you see it. The board is a live picture of what's happening across humans and agents.

This is the "digital twin" idea — development happens through the board, not beside it.

### Proof over status

A green checkmark means nothing. What did the agent actually produce?

The `result` field holds the real output — code, text, HTML, whatever the agent wrote. You read it and decide: is this good? Does it match what was asked? Does it need another pass?

Without viewable proof, you're trusting a status flag. With it, you're reviewing actual work.

### The human steers

Plans are suggestions. Agent assignments are suggestions. The human overrides freely.

That means the UI has to let you act: edit a task description before the agent reads it, reassign from gemini to claude, change priority, adjust scope. If you can only look but not touch, the board is a dashboard, not a control surface.

### Visual ease for humans, not LLM-friendly data

The UI serves human eyes. If something reads like a JSON dump or a database changelog, it doesn't belong in the main view. Machine data goes behind toggles or into dedicated sections.

---

## Examples

### 1. Agent writes a poem section, human reviews

Spec: "Write a collaborative poem as HTML." Odin breaks it into 5 tasks — scaffold, write intro, write middle, write closing, assemble.

Task "Write intro paragraph" gets assigned to gemini. Gemini runs, writes the paragraph, Odin stores the output in `result`. On the board, the task moves from TODO → IN_PROGRESS → DONE.

The human opens the task. They should see:
- **Description**: the prompt gemini received ("Write a 2-line paragraph about technology...")
- **Result**: the actual HTML gemini produced, readable, in its own section
- **Timeline**: created → assigned to gemini → started → completed. Three clean lines.

What they currently see: the timeline has 11 entries including a massive JSON blob with sessionIDs, tool_use events, and file operation traces. The result is buried inside an "activity" entry that starts with "result changed from to {..." and scrolls off the screen.

### 2. Human intervenes mid-execution

Odin planned 4 tasks for a landing page. Wave 1 (scaffold) completed. The human opens task 3 ("write feature descriptions") and realizes the description is wrong — it references an old product name.

They edit the description directly in the modal, fixing the product name. The agent hasn't picked it up yet (it's still TODO, waiting for wave 1). When the agent eventually runs, it reads the corrected description.

The timeline should show: "Description updated by alice@team.com" — one line. Not a diff of the old and new description text as raw strings.

### 3. Agent communicates during execution

Task "Implement auth module" is assigned to claude. The agent starts and posts a status update: "Starting implementation — reviewing existing auth patterns." Two minutes later, another update: "Found two auth implementations (JWT and session-based). Which should I use?" — this is a `question` type comment that blocks the agent until the human replies.

The human sees the question on the dashboard, types "Use JWT — session auth is deprecated," and the agent resumes immediately. The agent finishes, posts proof: "JWT auth implemented. 8 tests passing. Verify: `pytest tests/test_auth.py -v`."

The human experienced the execution as a conversation, not a black box. They steered the agent at the right moment. See [Agent Communication](communication.md) for the full model.

### 4. Debugging a failure

Task "Assemble final HTML" failed. The human opens it. They need to understand what happened.

The timeline tells the story: created → assigned to codex → started at 10:27 → failed at 10:28. One minute of work, then failure.

The result section is empty (agent failed before producing output). But there's a failure reason or log the human can expand to see what went wrong — not mixed into the same timeline as status changes.

---

## History — why it exists

Every field change gets recorded: who changed what, from what value, to what value, when.

Three reasons:

**Flow measurement.** Time-in-status is computed from status change timestamps. How long in TODO (stuck?), how long in IN_PROGRESS (agent struggling?), how fast through REVIEW (rubber-stamping?). This is the pulse of the project.

**Accountability.** A shared board with humans and agents means anyone could have changed anything. History answers "who did this?"

**Debugging.** When something breaks, you reconstruct the sequence of events.

### What's wrong now

The activity timeline treats every field change as equal:

```
status changed from TODO to IN_PROGRESS          ← useful
status changed from IN_PROGRESS to DONE          ← useful
result changed from to {"type":"step_start",...}  ← 3KB of machine noise
metadata changed from {...} to {...}              ← internal plumbing
```

All four show up in the same timeline, same visual weight. The human has to parse through agent execution traces to find "oh, the task completed at 10:28."

### What it should be

**Timeline** — human-readable activity: created, assigned, started, completed, failed, edited. Clean lines, natural language.

**Result section** — separate from the timeline. The work product. Expandable, inspectable. This is where proof of work lives.

**Technical details** — metadata, depends_on, complexity changes. Behind a toggle or hidden entirely. These are for debugging, not for the main view.

---

## What needs to change

1. **Separate activity from output from plumbing.** Three different things, three different treatments.
2. **Make the modal a real control surface.** Title, description, status, priority, assignees, labels, time budget — all editable.
3. **Fix the modal opening via URL.** `/kanban?taskId=34` should work.
