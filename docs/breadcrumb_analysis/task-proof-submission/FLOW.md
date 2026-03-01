# Task Proof Submission

Trigger: Odin agent finishes executing a task and calls `taskit_add_comment(comment_type="proof")`
End state: Proof text + file path metadata + inline screenshot images visible on task detail modal in taskit frontend

## Flow: Text-only proof (original path)

```
odin/src/odin/orchestrator.py :: _wrap_prompt()
  → Injects MCP instructions telling agent to call taskit_add_comment with comment_type="proof"
  → Passes TASKIT_TASK_ID, TASKIT_AUTH_TOKEN, TASKIT_URL via env vars

Agent (Claude/Gemini/Qwen/Codex)
  → Calls MCP tool: taskit_add_comment(content="...", comment_type="proof", file_paths=[...])

odin/src/odin/mcps/taskit_mcp/server.py :: taskit_add_comment()
  → Detects comment_type="proof"
  → No screenshot_paths → delegates directly to submit_proof()

odin/src/odin/tools/core.py :: TaskItToolClient.submit_proof()
  → Builds proof attachment: {"type": "proof", "summary": ..., "files": [...]}
  → POST /tasks/{task_id}/comments/
    {content, comment_type: "proof", attachments: [{type: "proof", ...}], author_email, author_label}

taskit/taskit-backend/tasks/views.py :: TaskViewSet.comments() [POST]
  → Validates via CreateTaskCommentSerializer
  → TaskComment.objects.create(task=task, comment_type="proof", attachments=[...], ...)
  → Returns HTTP 201 with serialized comment (includes file_attachments: [])
```

## Flow: Mobile screenshot capture + proof (end-to-end path)

```
odin/src/odin/orchestrator.py :: _wrap_prompt()
  → When mobile MCP is available, injects mobile proof workflow instructions:
    1. Do work (write code, create files)
    2. BUILD GATE: run project's build/typecheck command, fix errors until clean,
       then verify dev server responsive (curl localhost:<port>)
    3. mobile_list_available_devices to find a device
    4. Launch app (Expo: host.exp.exponent + mobile_open_url with exp://localhost:8081)
    5. Navigate to relevant screen using mobile tools
    6. mobile_save_screenshot to capture proof
    7. taskit_add_comment(comment_type="proof", screenshot_paths=["/tmp/proof_<task_id>.png"])

odin/src/odin/orchestrator.py :: _execute_task()
  → Sets context["mobile_mcp_enabled"] = True when mobile MCP is available
  → Passed to harness execute()

odin/src/odin/harnesses/codex.py :: execute()
  → If context["mobile_mcp_enabled"]:
    cmd.extend(["-c", 'mcp_servers.mobile.command="npx"'])
    cmd.extend(["-c", 'mcp_servers.mobile.args=["-y", "@mobilenext/mobile-mcp@latest"]'])

odin/src/odin/mcps/mobile_mcp/config.py :: get_server_fragment() / get_tool_names()
  → Provides per-harness server config for @mobilenext/mobile-mcp@latest
  → 19 tools including mobile_save_screenshot — no auth/env vars needed
  → Claude: standard npx; Gemini/Qwen: trust=true; Codex: -c flag pairs; Kilo: alwaysAllow

Agent (Claude/Gemini/Qwen/Codex)
  → Launches app on device
    For Expo/RN: mobile_launch_app(packageName="host.exp.exponent") + mobile_open_url(url="exp://localhost:8081")
    Never guess custom package names — Expo apps run inside Expo Go
  → Navigates to relevant screen using mobile tools (tap, swipe, type)
  → Captures screenshot: mobile_save_screenshot(device="...", saveTo="/tmp/proof.png")
  → File written to agent workspace at /tmp/proof.png
```

## Flow: Proof with screenshots (upload path)

```
Agent (Claude/Gemini/Qwen/Codex)
  → Calls MCP tool: taskit_add_comment(content="...", comment_type="proof",
      screenshot_paths=["/tmp/proof.png", "/tmp/ui.png"])

odin/src/odin/mcps/taskit_mcp/server.py :: taskit_add_comment()
  → Detects comment_type="proof" AND screenshot_paths is not None
  → Step 1: client.upload_screenshots(screenshot_paths)
  → Step 2: collects URLs from response
  → Step 3: client.submit_proof(summary=content, files=file_paths, screenshot_urls=urls)
  → On upload failure: returns {"error": "Screenshot upload failed: ..."}, no proof submitted

odin/src/odin/tools/core.py :: TaskItToolClient.upload_screenshots()
  → Reads each file from disk (raises FileNotFoundError if missing)
  → Guesses MIME type via mimetypes
  → POST /tasks/{task_id}/screenshots/ (multipart/form-data)
    files=[("files", (name, bytes, mime))], data={"author_email": ...}
  → Uses _auth_headers() (no Content-Type — httpx sets multipart boundary)
  → Returns list of attachment dicts: [{id, url, original_filename, content_type, file_size}]

taskit/taskit-backend/tasks/views.py :: TaskViewSet.screenshots() [POST]
  → Validates: at least 1 file, each ≤ 10 MB
  → Creates CommentAttachment per file (comment=null, task=task)
  → File stored via Django FileField to media/screenshots/YYYY/MM/
  → Returns serialized attachments (HTTP 201) via CommentAttachmentSerializer

odin/src/odin/tools/core.py :: TaskItToolClient.submit_proof()
  → Builds proof attachment: {"type": "proof", "summary": ..., "screenshots": [url1, url2]}
  → POST /tasks/{task_id}/comments/ (JSON, same as text-only proof)

taskit/taskit-backend/tasks/views.py :: TaskViewSet.comments() [POST]
  → Creates TaskComment (same as text-only proof)
  → file_attachments are NOT auto-linked — they're on the task, not the comment
```

## Flow: Frontend rendering

```
taskit/taskit-frontend/src/services/harness/HarnessTimeService.ts :: fetchTaskDetail()
  → GET /api/tasks/{taskId}/ → maps comments with file_attachments
  → file_attachments mapped: snake_case → camelCase (id, url, originalFilename, contentType, ...)

taskit/taskit-frontend/src/components/TaskDetailModal.tsx :: CommentItem
  → Detects isProof via commentType === "proof" OR attachments[].type === "proof"
  → Renders with cyan border styling + "proof" badge
  → If comment.fileAttachments has image/* entries:
    → Renders image gallery: <a><img lazy max-w-[300px]></a> per image
    → Click opens full-size in new tab
    → Filename shown below thumbnail
```

## Parallel path: Execution result recording

After the agent outputs the ODIN-STATUS envelope (separate from proof):

```
odin/src/odin/orchestrator.py :: _execute_task()
  → Parses ODIN-STATUS envelope from agent output
  → Calls TaskManager.record_execution_result()

odin/src/odin/taskit/manager.py :: TaskManager.record_execution_result()
  → Delegates to TaskItBackend.record_execution_result()

odin/src/odin/backends/taskit.py :: TaskItBackend.record_execution_result()
  → POST /tasks/{task_id}/execution_result/
    {execution_result: {success, raw_output, duration_ms, ...}, status: "REVIEW"|"FAILED"}
```

## Screenshot capture methods

The upload/storage/display pipeline is complete. Capture is available via multiple methods:

| Capture method | Availability | Notes |
|----------------|-------------|-------|
| Mobile MCP `mobile_save_screenshot` | When mobile device connected | Primary method for mobile testing — captures device screen via `@mobilenext/mobile-mcp` |
| Agent generates image (PIL, SVG, matplotlib) | Always | Agent creates artifact programmatically |
| `screencapture -x file.png` | macOS only | Shell command agent can run |
| Headless browser screenshot | If playwright/puppeteer installed | Best for web UI proof |
| `wkhtmltoimage page.html proof.png` | If installed | HTML → PNG |
| Placeholder PNG for testing | Always | Exercises the upload flow |
