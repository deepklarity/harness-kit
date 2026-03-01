# Task Proof Submission — Debug Guide

## 30-Second Triage Flowchart

When a task has no proof/screenshot, start here:

```
1. Did the agent call taskit_add_comment with comment_type="proof"?
   → Quick check: python testing_tools/task_inspect.py <task_id> --json --sections comments
   → Look for comment_type="proof" in output

   YES → Proof was submitted. Problem is downstream (upload, backend, frontend).
         Go to "Screenshot upload returns 400/404" in symptom table below.

   NO  → Agent never submitted proof. Two sub-cases:

   2. Did the agent capture screenshots at all?
      → Check: grep "mobile_take_screenshot\|mobile_save_screenshot" <working_dir>/.odin/logs/task_<id>.out

      YES, but no proof submitted → AGENT BEHAVIOR GAP
           The agent captured screenshots but never called taskit_add_comment(comment_type="proof").
           Common causes:
           - App didn't load (screenshots show loading spinners) → agent gave up
           - Agent hit token/turn limit before reaching proof step
           - Agent confused mobile_take_screenshot (capture) with proof submission (separate step)
           Fix: Improve prompt instructions to require text-only proof as fallback.
           See: orchestrator.py:_wrap_prompt() — the MCP instruction block.

      NO screenshots attempted → AGENT DIDN'T TRY
           Agent skipped the entire proof workflow.
           Check: was mobile_mcp_enabled? grep "mobile_mcp_enabled" .odin/logs/run_*.jsonl
           Check: did prompt include proof instructions? Look at _wrap_prompt() output.
```

## Log locations

| Layer | Log file | What's in it |
|-------|----------|-------------|
| Odin orchestrator | `.odin/logs/run_*.jsonl` | Task dispatch, MCP config generation, mobile_mcp_enabled flag, envelope parsing |
| Odin MCP server | `.odin/logs/mcp_server.log` (file) + stderr (WARNING+ in tmux) | MCP tool calls, auth resolution, HTTP requests to backend |
| Mobile MCP | Agent stderr / tmux pane | `@mobilenext/mobile-mcp` tool calls, device connection errors, screenshot save results |
| TaskIt backend | `taskit/taskit-backend/logs/taskit.log` | Comment creation, validation errors, HTTP responses |
| TaskIt detail log | `taskit/taskit-backend/logs/taskit_detail.log` | Request/response bodies, serializer errors |
| Frontend | Browser console | API fetch errors, comment rendering issues |

## What to search for

| Symptom | Where to look | Search term / action |
|---------|--------------|---------------------|
| Proof not appearing on task | Backend log | `grep "comment_type.*proof" taskit/taskit-backend/logs/taskit_detail.log` |
| MCP tool call failing | Odin run log | `grep "taskit_add_comment" .odin/logs/run_*.jsonl` |
| Auth token expired/invalid | MCP server log | `grep "401\|TaskItAuthError" .odin/logs/mcp_server.log` |
| Agent didn't call proof tool | Agent output (raw_output in execution_result) | Search for `taskit_add_comment` in agent text |
| Screenshots captured but proof never submitted | Task output log + task_inspect comments | Agent called `mobile_take_screenshot` but never `taskit_add_comment(proof)` — check if app loaded (screenshots may show spinners), check agent turn/token limits, check if prompt requires text-only fallback |
| App stuck loading during mobile verification | Task output log | `grep "mobile_take_screenshot\|mobile_launch_app" <working_dir>/.odin/logs/task_<id>.out` — if all screenshots show loading state, agent may have given up on proof |
| Proof submitted but no badge | Frontend CommentItem | Check `commentType` field in API response — may be `status_update` instead of `proof` |
| Attachments empty in DB | Backend | `python testing_tools/task_inspect.py <task_id> --json --sections comments` |
| MCP config not generated | Orchestrator log | `grep "mcp_config" .odin/logs/run_*.jsonl` |
| Screenshot upload returns 400 | Backend detail log | `grep "screenshots" taskit/taskit-backend/logs/taskit_detail.log` |
| Screenshot upload returns 404 | Odin MCP log | Task ID wrong or task deleted — check `_task_url()` in client |
| "No files provided" on upload | MCP server | Client not sending multipart correctly — check `upload_screenshots()` |
| "exceeds 10 MB" on upload | Backend | File too large — check `MAX_SCREENSHOT_SIZE` in views.py |
| Screenshot URL returns 404 | Django dev server | `DEBUG=True` needed for media serving; check `MEDIA_ROOT` exists |
| Images not rendering in UI | Browser console | Check `fileAttachments` in API response; check `contentType` starts with `image/` |
| `file_attachments` missing from API | Backend serializer | `TaskCommentSerializer` must include `CommentAttachmentSerializer` |
| Upload succeeds but no images in proof | MCP server flow | Screenshots are on the task, not auto-linked to comment — check `comment` FK is null |
| Mobile MCP not available to agent | Orchestrator log | `grep "mobile_mcp_enabled" .odin/logs/run_*.jsonl` — check if flag is set |
| `mobile_save_screenshot` tool not found | Agent output | Mobile MCP server not injected — check harness config generation and `context["mobile_mcp_enabled"]` |
| Mobile MCP npx fails to install | Agent stderr | `npx -y @mobilenext/mobile-mcp@latest` failed — check network, npm registry, node version |
| `mobile_save_screenshot` succeeds but file missing | Agent workspace | File path mismatch between `saveTo` param and `screenshot_paths` in `taskit_add_comment` |
| No mobile device found | Agent output / mobile MCP stderr | `mobile_list_available_devices` returns empty — check device connected, USB/WiFi debugging enabled |
| Agent says "app not installed" but app IS running | Task output log | Agent tried `mobile_launch_app` with guessed package name instead of screenshotting what's on screen. Expo apps run inside `host.exp.exponent`, not as standalone APKs. Check if orchestrator instructions include screenshot-first guidance. |
| Agent guesses wrong package name for Expo app | Task output log | `grep "mobile_launch_app" <working_dir>/.odin/logs/task_<id>.out` — agent should use `host.exp.exponent` or `mobile_open_url` with Expo dev URL, not a custom package name |
| Codex not getting mobile MCP flags | Orchestrator log | Check `context.get("mobile_mcp_enabled")` in codex.py — may not be set if mobile MCP config absent |

## Quick commands

```bash
# FASTEST: Did proof get submitted? (one-liner answer)
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --brief
# If status=REVIEW but no proof count → agent never submitted proof

# FASTEST: Did agent even try screenshots?
grep -c "mobile_take_screenshot\|mobile_save_screenshot\|screenshot_paths" <working_dir>/.odin/logs/task_<task_id>.out
# 0 = never tried, >0 = captured but check if submitted

# Check proof comments on a specific task
cd taskit/taskit-backend && python testing_tools/task_inspect.py <task_id> --sections comments

# Check all proof comments and their file attachments
cd taskit/taskit-backend && python -c "
import django; import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import TaskComment, CommentAttachment
comments = TaskComment.objects.filter(task_id=<task_id>, comment_type='proof')
for c in comments:
    print(f'Comment {c.id}: {c.content[:80]}')
    print(f'  JSON attachments: {c.attachments}')
    fas = c.file_attachments.all()
    print(f'  File attachments: {fas.count()}')
    for fa in fas:
        print(f'    {fa.original_filename} ({fa.content_type}, {fa.file_size}b) → {fa.file.url}')
    print()
"

# Check all uploaded screenshots for a task (including unlinked ones)
cd taskit/taskit-backend && python -c "
import django; import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import CommentAttachment
for a in CommentAttachment.objects.filter(task_id=<task_id>):
    linked = f'comment={a.comment_id}' if a.comment_id else 'unlinked'
    print(f'{a.id}: {a.original_filename} ({a.content_type}) [{linked}] → {a.file.url}')
"

# Verify media directory exists and has files
ls -la taskit/taskit-backend/media/screenshots/

# Verify MCP config was generated for a task run
ls -la <working_dir>/.mcp.json 2>/dev/null || echo "No MCP config found"

# Check if proof was in agent's raw output
cd taskit/taskit-backend && python -c "
import django; import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tasks.models import Task
t = Task.objects.get(id=<task_id>)
er = t.metadata.get('execution_result', {})
output = er.get('raw_output', '')
print('Proof mentioned:', 'proof' in output.lower())
print('screenshot_paths mentioned:', 'screenshot_paths' in output)
print('taskit_add_comment mentioned:', 'taskit_add_comment' in output)
"

# Tail backend log for screenshot uploads
grep -i "screenshot" taskit/taskit-backend/logs/taskit_detail.log | tail -20

# Test screenshot upload manually via curl
curl -X POST http://localhost:8000/tasks/<task_id>/screenshots/ \
  -F "files=@/tmp/test.png" \
  -F "author_email=test@example.com"

# Run screenshot-specific tests
cd taskit/taskit-backend && USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python manage.py test tests.test_screenshots -v2
cd odin && python -m pytest tests/unit/test_tool_client_screenshots.py tests/unit/test_taskit_mcp_screenshots.py -v

# Run mobile MCP config tests
cd odin && python -m pytest tests/unit/test_mobile_mcp_config.py -v

# Check if mobile MCP config is being generated for a harness
cd odin && python -c "
from odin.mcps.mobile_mcp.config import get_tool_names, get_server_fragment
print('Tools:', len(get_tool_names()), '— includes mobile_save_screenshot:', 'mobile_save_screenshot' in get_tool_names())
for h in ['claude_code', 'gemini_cli', 'codex', 'kilo_code', 'qwen']:
    frag = get_server_fragment(h)
    print(f'{h}: {list(frag.keys()) if frag else \"None\"}')"

# Verify Codex harness injects mobile MCP flags (dry run)
cd odin && python -c "
from odin.mcps.mobile_mcp.config import get_server_fragment
frag = get_server_fragment('codex')
print('Codex mobile flags:', frag)"
```

## Env vars that affect this flow

| Variable | Effect | Default |
|----------|--------|---------|
| `TASKIT_URL` | Backend URL for MCP HTTP calls | `http://localhost:8000` |
| `TASKIT_AUTH_TOKEN` | Bearer token for MCP → backend auth | Empty (unauthenticated) |
| `TASKIT_TASK_ID` | Task ID injected into agent env, used as default for MCP tool calls | None (required) |
| `TASKIT_AUTHOR_EMAIL` | Author identity for proof comments and screenshot uploads | Generated from agent name + model |
| `TASKIT_AUTHOR_LABEL` | Human-readable author label | Generated from agent name + model |
| `ODIN_ADMIN_USER` | Fallback: email for token acquisition | None |
| `ODIN_ADMIN_PASSWORD` | Fallback: password for token acquisition | None |
| `MEDIA_ROOT` | Where uploaded files are stored on disk | `BASE_DIR / "media"` |
| `MEDIA_URL` | URL prefix for serving uploaded files | `/media/` |
| `DEBUG` | Must be True for Django dev server to serve media files | `True` |

## Common breakpoints

- `odin/src/odin/mcps/mobile_mcp/config.py:get_server_fragment()` — verify correct config shape for target harness
- `odin/src/odin/orchestrator.py:_execute_task()` — verify `context["mobile_mcp_enabled"]` is set when expected
- `odin/src/odin/harnesses/codex.py:execute()` — verify mobile MCP `-c` flags are appended to command
- `odin/src/odin/orchestrator.py:_wrap_prompt()` — verify MCP instructions are injected into prompt (including mobile proof workflow)
- `odin/src/odin/mcps/taskit_mcp/server.py:taskit_add_comment()` — verify tool receives correct params from agent; check screenshot_paths branching
- `odin/src/odin/tools/core.py:upload_screenshots()` — verify files are read, multipart payload constructed, response parsed
- `odin/src/odin/tools/core.py:submit_proof()` — verify HTTP payload shape; check `screenshot_urls` is present when expected
- `taskit/taskit-backend/tasks/views.py:TaskViewSet.screenshots()` — verify files received, size validation, CommentAttachment creation
- `taskit/taskit-backend/tasks/views.py:TaskViewSet.comments()` — verify serializer validation and TaskComment creation
- `taskit/taskit-frontend/src/components/TaskDetailModal.tsx:CommentItem` — verify `fileAttachments` is populated and image gallery renders

## Data flow through mobile screenshot capture + upload

```
mobile_save_screenshot(device="iPhone", saveTo="/tmp/proof.png")
  → @mobilenext/mobile-mcp captures device screen → writes /tmp/proof.png
    → Agent calls taskit_add_comment(screenshot_paths=["/tmp/proof.png"])
      → MCP server reads file via upload_screenshots()
        → multipart POST to /tasks/{id}/screenshots/
          → Django FileField saves to media/screenshots/2026/02/proof.png
            → CommentAttachment record created (comment=null, task=task)
              → URL returned: http://localhost:8000/media/screenshots/2026/02/proof.png
                → URL embedded in proof attachment JSON: {"screenshots": ["http://..."]}
                  → Frontend reads file_attachments from comment serializer
                    → <img src={url}> rendered in CommentItem
```
