# Manual Test: MCP Question/Reply Flow

Test the taskit-mcp server's question/reply flow using MCP Inspector.

## Prerequisites

1. TaskIt backend running: `cd taskit/taskit-backend && python manage.py runserver 0.0.0.0:8000`
2. Odin installed with MCP extras: `cd odin && pip install -e ".[mcp]"`
3. A task exists (note its ID)

## Setup

```bash
# From the odin working directory (e.g., odin/temp_test_dir/)
cd odin/temp_test_dir

# Launch MCP Inspector
npx @modelcontextprotocol/inspector taskit-mcp
```

Open the Inspector URL in your browser.

## Test Steps

### 1. Post a status update

Call `taskit_add_comment`:
- `task_id`: your task ID
- `content`: "Testing MCP comment taxonomy"
- `comment_type`: `status_update` (default)

**Expected**: Returns `{"comment_id": <id>}`. Check the task in TaskIt — comment has `comment_type: "status_update"`.

### 2. Post proof of work

Call `taskit_add_attachment`:
- `task_id`: your task ID
- `content`: "All 15 tests passed. Files created: output.log"
- `attachment_type`: `proof`
- `file_paths`: `["tests/output.log"]`

**Expected**: Returns `{"comment_id": <id>}`. In TaskIt, comment has attachment `{"type": "proof", "summary": "..."}`. UI shows green "proof" badge.

### 3. Ask a blocking question

Call `taskit_add_comment`:
- `task_id`: your task ID
- `content`: "Should I refactor the auth module or add a wrapper?"
- `comment_type`: `question`

**Expected**: The tool **blocks** (polls indefinitely). In TaskIt:
- Comment has `comment_type: "question"`, `attachments: [{"type":"question","status":"pending"}]`
- Task card shows amber pulsing `?` badge
- Task metadata has `has_pending_question: true`

### 4. Reply from TaskIt UI

Open the task detail in the TaskIt frontend. You should see:
- The question with amber left-border and `?` icon
- A "Reply" button below the question

Click **Reply**, type "Add a wrapper — less risk.", and submit.

### 5. Verify MCP unblocks

Back in MCP Inspector, the `taskit_add_comment` call should return:
```json
{"comment_id": <question_id>, "reply": "Add a wrapper — less risk."}
```

### 6. Verify state cleanup

In TaskIt:
- Question shows "ANSWERED" badge, green reply indented below
- `has_pending_question` cleared from task metadata
- Amber badge on TaskCard is gone

## Verification Checklist

- [ ] `status_update` comment stored with correct type, UI shows "status-via-mcp" badge for agent authors
- [ ] `proof` attachment stored correctly, UI shows green "proof" badge
- [ ] `question` comment blocks MCP, sets metadata flag, shows amber badge
- [ ] Reply from UI unblocks MCP, clears flag, shows "answered" state
- [ ] `?type=` filter works: `GET /tasks/<id>/comments/?type=question`
