# Question Flow Smoke Test

Manual E2E test for the human-in-the-loop question/reply flow. Each task instructs the agent to ask a question, wait for a human reply, then use the reply in its work.

## Prerequisites

- TaskIt backend running at `http://localhost:8000`
- MCP config generated in the working directory (`odin mcp-setup`)
- TaskIt UI open in browser at `http://localhost:5173`

## Tasks

### Task 1: Ask about naming convention

Write a Python function that greets someone by name. Before writing the function, ask the human what naming convention to use for the function (e.g., `greet_user`, `sayHello`, `welcome_person`). Wait for their reply, then write the function using the name they chose.

**Expected behavior:**
1. Agent posts a question via MCP (`comment_type=question`)
2. UI shows the question with a pulsing "PENDING" badge and a "Reply" button
3. Human types a reply (e.g., "Use `welcome_person`")
4. Agent receives the reply and writes the function with that name
5. Agent posts a status update confirming completion

### Task 2: Ask about output format

Create a simple data report. Before writing the report, ask the human whether they want the output as JSON, YAML, or plain text. Wait for their reply, then generate the report in the chosen format.

**Expected behavior:**
Same question/reply flow as Task 1. Verify the agent uses the human's format choice.

### Task 3: Ask about error handling

Write a function that reads a file and returns its contents. Ask the human how they want errors handled: raise exceptions, return None, or return a default string. Wait for their reply, then implement accordingly.

**Expected behavior:**
Same flow. Verifies the agent can use nuanced human guidance in its implementation.

## Verification Checklist

- [ ] Questions appear in the TaskIt UI with amber "PENDING" badges
- [ ] The "Reply" button works and sends the reply
- [ ] After replying, the question shows "ANSWERED" and the reply appears inline
- [ ] The agent continues working after receiving the reply
- [ ] The sidebar shows "Agent waiting for reply" banner while a question is pending
- [ ] `has_pending_question` metadata clears after reply
- [ ] Comments refresh immediately after posting (no need to close/reopen modal)

## Cross-Harness Testing

Run each task with a different harness to verify MCP works across all agents:

| Task | Harness | Model |
|------|---------|-------|
| Task 1 | claude | sonnet-4-5 |
| Task 2 | gemini | gemini-2.0-flash |
| Task 3 | qwen | qwen3-coder |

For harnesses that don't support MCP natively (codex), verify graceful degradation — the agent should still work but won't ask questions.
