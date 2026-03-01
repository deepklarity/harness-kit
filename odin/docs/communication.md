# Agent Communication

## The Problem with Silent Agents

An agent runs for 90 seconds. During that time, the human sees nothing. Maybe a spinner. Maybe a tmux pane filling with stream-json. When it finishes, there's a result — or a failure. Either way, the human experienced 90 seconds of darkness.

This is backwards. Imagine a junior developer who disappears into a room, works for an hour, then slides a finished PR under the door. No questions, no check-ins, no "hey, the API schema is different than the spec said — should I adapt or stop?" Just silence, then output.

Nobody works like that. Agents shouldn't either.

## The Principle

**Agents communicate as they work, not after.**

Every agent has a communication channel back to the human — the TaskIt MCP server. Through it, the agent can:

- **Post status updates** — "Starting work on the auth module," "Finished the data layer, moving to the API endpoints"
- **Ask blocking questions** — "The spec says 'use the standard auth flow' but there are two auth implementations in the codebase. Which one?" The agent pauses, the human answers on the dashboard, the agent resumes.
- **Submit proof of work** — "Here are the files I created, here's how to verify them, here's what the next person needs to know"

These aren't optional nice-to-haves. They're how work actually happens when humans collaborate. The MCP channel makes agents participate in the same way.

## Three Communication Actions

### 1. Status Updates

A status update is the agent saying "I'm here, I'm working, here's what's happening." It's the Slack message that says "picked this up, looking at it now." It makes work visible before it's done.

**When to use:** At the start of work, at significant milestones, on completion.

**Cadence:** 2-4 updates per task is the right range. Not every line of code, not every file touched — meaningful progress markers. The goal is human awareness, not a live transcript.

```
"Starting implementation — reviewing existing auth patterns first."
"Data layer complete. Moving to API endpoints. Found 3 existing endpoints that need updating."
"Implementation complete. All 12 tests passing. Created 2 new files, modified 3."
```

### 2. Questions

A question is the agent saying "I need a human decision before I continue." It's the most important communication action because it prevents wasted work.

**When to use:** When the agent encounters genuine ambiguity — something the spec doesn't cover, conflicting requirements, a decision that should be made by a human.

**What happens:**
1. Agent calls `taskit_add_comment(content="...", comment_type="question")`
2. The MCP tool call **blocks** — the agent freezes, consuming zero tokens
3. The question appears on the TaskIt dashboard with a reply input
4. A human reads it and replies
5. The reply flows back to the agent as the tool result
6. The agent resumes with the human's answer

This is the "raise your hand" mechanism. Without it, agents guess. And when agents guess wrong, you throw away work and re-run — costing more than the question would have.

### 3. Proof of Work

Proof is the agent saying "here's what I did, here's how to verify it, here's the handover." It's the PR description, not the code itself.

**When to use:** Before marking work as done. The proof comment is the last thing an agent posts.

**What it includes:**
- What was accomplished (summary, not raw output)
- How to verify it (commands to run, pages to check)
- What files were created or modified
- Anything the reviewer or downstream task needs to know

```
comment_type="proof", file_paths=["src/auth/handler.py", "tests/test_auth.py"]
"Implemented JWT auth handler with refresh token support.
 Verify: run `pytest tests/test_auth.py -v` (12 tests, all passing).
 Note: requires AUTH_SECRET env var — added to .env.example."
```

## The Communication Stack

```
Agent (claude, gemini, codex, qwen, ...)
  │
  │  MCP tool calls (stdio transport)
  │
  ▼
TaskIt MCP Server (child process per agent)
  │
  │  HTTP REST (with Bearer auth)
  │
  ▼
TaskIt Backend (Django)
  │
  │  Real-time updates
  │
  ▼
TaskIt Dashboard (React)
  │
  ▼
Human (reads updates, answers questions, reviews proof)
```

Each agent gets its own MCP server process, configured with:
- **TASKIT_TASK_ID** — which task the agent is working on
- **TASKIT_AUTH_TOKEN** — authentication for the TaskIt API
- **TASKIT_AUTHOR_EMAIL** — identity (e.g., `claude+sonnet-4-5@odin.agent`)

The agent never sees this plumbing. From the agent's perspective, `taskit_add_comment` is just another tool — like reading a file or running a command. The MCP server handles routing, auth, and polling transparently.

## Why This Matters

### Visibility during execution

Without MCP, the human's experience of a 5-task spec execution is:

```
odin plan --quick spec.md
[spinner for 3 minutes]
Done. 4/5 tasks completed, 1 failed.
```

Three minutes of nothing, then a summary. What happened during those three minutes? Which task is running? Is the agent stuck? Is it making progress? No idea.

With MCP, the dashboard updates in real time:

```
Task 1 (scaffold):  "Created project structure with 4 files."
Task 2 (auth):      "Starting implementation — reviewing existing auth patterns."
Task 2 (auth):      "Question: Two auth implementations found — JWT in /auth/jwt.py
                      and session-based in /auth/session.py. Which should I use?"
  → Human replies:  "Use JWT. The session auth is deprecated."
Task 2 (auth):      "JWT auth implemented. 8 tests passing."
Task 3 (api):       "Building API endpoints. Using existing patterns from /api/v1/."
Task 4 (tests):     "Writing integration tests. Found 2 edge cases not in spec."
Task 5 (assemble):  "Failed — task 4 produced test files in unexpected location."
```

The human sees work happening. They intervene at the right moment (answering the auth question). They understand the failure immediately (file location issue) instead of reading a stack trace after the fact.

### Preventing wasted work

The auth question above is the key case. Without MCP, the agent picks one auth approach (probably wrong), builds on it for 60 seconds, and produces output the human rejects. With MCP, the agent asks, waits 10 seconds for a reply, and builds the right thing. The question costs 10 seconds; the wrong guess costs a re-run.

### Building trust

Humans trust what they can see. An agent that posts updates feels like a teammate. An agent that disappears and returns with output feels like a vending machine. The difference isn't about the quality of output — it's about whether the human feels in control.

When agents communicate as they work, humans learn to trust the system. They start reviewing during execution, not just after. They answer questions quickly because they see the agent is waiting. They notice problems early. The whole workflow becomes collaborative, not sequential.

## The Prompt Integration

Odin doesn't just provide MCP tools — it tells agents how and when to use them. The orchestrator injects a communication guide into every agent's prompt:

```
TaskIt MCP Tools — your communication channel to the task board.

Your task ID: {task_id}

Communication lifecycle:
1. Start: Post what you're about to do
2. Milestones: Post significant progress (not every step)
3. Questions: ASK when you're uncertain — don't guess
4. Proof: Before finishing, post verification evidence
5. Completion: Summarize what you accomplished

The human sees your updates in real time on the dashboard.
When you ask a question, you'll pause until they reply.
```

This is essential. Giving an agent a tool without telling it the expected cadence results in either zero updates (agent ignores the tool) or a flood (agent posts after every line). The prompt sets the norm.

## Comment Types as a Taxonomy

Comments on the task board are typed to enable different treatment in the UI and in downstream processing:

| Type | Purpose | UI Treatment | Blocking? |
|------|---------|-------------|-----------|
| `status_update` | Progress visibility | Standard comment | No |
| `question` | Human decision needed | Highlighted, reply input | Yes — agent freezes |
| `proof` | Verification evidence | Proof badge, file links | No |
| `debug` | Effective input/output | Hidden by default | No |

The taxonomy isn't about bureaucracy — it's about letting the dashboard show the right things at the right time. Questions get a distinct visual treatment because they need action. Proof comments get a badge because they're the deliverable. Debug comments hide by default because they're for post-hoc inspection, not live review.

## What This Replaces

Before MCP, agent communication was post-hoc. Odin captured the agent's output after it finished and posted a summary comment. The human saw:

```
"Completed in 12.3s · 8,420 tokens. Assembled final HTML."
```

That's a receipt, not communication. It tells you the work is done but nothing about the journey. There's no record of decisions made, questions that should have been asked, or progress that was visible during execution.

The post-hoc summary still exists — Odin posts it as the final comment after execution. But it's the epilogue, not the whole story. The MCP channel provides the chapters.

## Relationship to Other Docs

- **[MCP Technical Reference](mcp.md)** — config generation, tool parameters, auth flow, per-CLI formats
- **[Activity and Comments](activity-and-comments.md)** — comment model, actor identity, UI treatment
- **[Proof of Work](proof-of-work.md)** — what "done" means, verification philosophy
- **[Philosophy](philosophy.md)** — tenet #10 (Questions to Human) and #15 (Digital Twin)
- **[Execution](execution.md)** — how tasks run, status lifecycle, DAG executor
