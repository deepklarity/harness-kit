# Proof of Work

## The problem

A task says "completed." What does that mean?

Right now it means: the agent returned something and didn't crash. The status flipped to done. There's a result string somewhere. Maybe you can find it, maybe it's buried in a JSON blob.

That's not proof. That's a status flag.

When a human finishes a task on a kanban board — a real one, at a real company — they don't just drag the card to "Done." They leave a trail: a link to the PR, a screenshot of the UI, a note saying "run `npm test -- --filter auth` to verify," a comment explaining the tricky part. The next person picks up the card and knows exactly where things stand.

Agents should work the same way.

## What proof means

Proof is the answer to: **"Show me."**

Someone looks at a completed task and asks: show me it works. Show me what changed. Show me how to verify it myself.

Proof is not a boolean. It's not "tests passed: yes." It's the collection of artifacts that let a human (or another agent) trust and continue from the work.

This includes:

**The output itself.** What did the agent actually produce? Code, text, HTML, a config file — whatever was asked for. Viewable directly, not hidden behind a JSON key three levels deep.

**How to verify it.** A command to run. Steps to follow. "Run `pytest tests/auth/ -v` and check test_token_refresh passes." "Open `index.html` in a browser and verify the hero section renders." The reader shouldn't have to guess how to check correctness.

**What changed and why.** Which files were touched. What the diff looks like. If there was a non-obvious decision ("used retry logic because the API is flaky"), say so. Context that won't be obvious from the output alone.

**Visual evidence when it matters.** A screenshot of the rendered page. A terminal capture showing the test run. Not always needed, but when the work is visual or the output is complex, seeing is faster than reading.

**The handover.** If someone else has to pick this up — a reviewer, a downstream task, a human doing QA — what do they need to know? This is the kanban card note. "Ready for review. The migration needs to run before testing. See `data/migrations/003_add_columns.sql`."

### How proof is submitted

Agents submit proof through the MCP communication channel using `taskit_add_comment(comment_type="proof")`. This creates a first-class proof comment on the task board with distinct UI treatment — a proof badge, file links, and structured verification steps. The proof is visible on the dashboard immediately, not buried in a result field or discovered after execution completes.

This is part of the broader communication model: agents don't just produce output silently and hope someone finds it. They actively post status updates as they work, ask questions when uncertain, and submit proof before marking done. See [Agent Communication](communication.md) for the full model.

## Proof at three levels

### Task level — the agent proves its work

Every completed task should carry enough information for a human to evaluate it without re-running anything. The result field isn't just "what the agent returned" — it's the agent's proof package.

A good task completion looks like a good pull request: here's what I did, here's how to verify, here's what to watch out for.

A bad task completion is a green checkmark with a blob of text that might be the output or might be execution metadata.

### System level — the orchestration proves itself

The system has its own proof obligations. When Odin says "all tasks completed," that should be verifiable. Did every task actually run? Did any get silently dropped? Did the dependency order hold?

This is what structured logs and the task timeline are for. Not as noise in the UI, but as an auditable chain: task created → assigned → started at T1 → completed at T2 → result stored. If there's a gap in that chain, something went wrong and the proof is incomplete.

A spec that says "done" should mean: every task in it ran, every task produced output, and no task was skipped or phantom-completed.

### Human level — the reviewer can trust and act

The human looking at the board shouldn't need to run forensics to decide if work is done. The proof should be surfaced, not excavated.

This means:
- Results are readable in the UI, not raw JSON
- Verification steps are visible on the task card
- The timeline tells a clean story (started, finished, here's what happened)
- When something failed, the failure reason is front and center

Trust is built by making the work transparent. If the human has to dig to verify, they'll stop verifying. If it's right there, they'll review it. The design of proof is a UX problem as much as a data problem.

## How this connects to testing

Tests are one form of proof — an automated, repeatable one. A passing test suite is strong evidence. But tests alone are not proof, and proof doesn't always require tests.

Some work is test-provable: "the API returns 200 with the right schema." Run the test, see the green.

Some work is review-provable: "the landing page copy matches the brand voice." A human reads it and decides.

Some work needs both: "the auth flow works end-to-end." Tests prove the mechanics, a screenshot proves the UI, and a human confirms the flow makes sense.

The proof-of-work philosophy doesn't prescribe which type of proof applies. It says: **every completed task must carry sufficient evidence for its claims.** What "sufficient" means depends on the task. A code task needs test commands and diffs. A writing task needs the text and maybe a rendered preview. A refactor needs before/after evidence that behavior didn't change.

## What "done" means

A task is done when:

1. The work product exists and is viewable
2. There's a way to verify it (automated or manual)
3. The next person in the chain can pick it up without asking "what happened here?"

Until all three are true, it's not done. It's just finished running.

## The odin + taskit relationship

Odin orchestrates. TaskIt stores and surfaces. Proof lives in both.

**Odin's job:** make sure agents produce proof, not just output. The task description should tell the agent what proof to include. The executor should capture duration, exit codes, logs. The harness should structure the result so it's inspectable.

**TaskIt's job:** make proof visible. The result section is the proof surface. The timeline is the audit trail. The UI is where a human decides "yes, this is really done" or "no, this needs another pass."

Neither system alone provides proof. Odin generates it; TaskIt presents it. A result without a UI to read it is hidden proof. A pretty UI without real output is empty proof.

## The trust chain

```
Agent does work
    → produces output + verification steps + context
        → Odin captures result + timing + execution metadata
            → TaskIt stores and surfaces everything
                → Human reviews, verifies, accepts or rejects
```

Every link in this chain matters. If the agent produces output but no verification steps, the human has to figure out how to check it. If Odin captures the result but TaskIt buries it in JSON, nobody reads it. If the UI is clean but the underlying data is missing, it's theater.

Proof of work is the practice of keeping every link honest.
