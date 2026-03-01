# Task Proof Submission — Detailed Trace

## 1. Prompt Wrapping with MCP Instructions

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_wrap_prompt()` (line ~1399)
**Called by**: `_execute_task()` before agent dispatch
**Calls**: Nothing — returns modified prompt string

Key logic:
- Injects MCP tool usage instructions into the agent's prompt
- Tells agent to call `taskit_add_comment` with `comment_type="proof"` and `file_paths`
- Proof submission is the **completion signal** — task without proof is marked failed
- Required sequence: status_update → do work → proof → ODIN-STATUS envelope
- When mobile MCP is available, injects mobile proof workflow: launch app → navigate → screenshot → submit proof
- Expo/React Native guidance: apps run inside Expo Go (`host.exp.exponent`), not as standalone APKs
- Launch sequence for Expo: `mobile_launch_app(packageName="host.exp.exponent")` then `mobile_open_url(url="exp://localhost:8081")`
- Agents should never guess package names — always use `host.exp.exponent` for Expo apps

Data in: raw prompt string, `mcp_task_id`, mobile MCP availability
Data out: wrapped prompt with MCP instructions prepended (including mobile proof workflow if applicable)

---

## 2. MCP Environment & Config Generation

**File**: `odin/src/odin/orchestrator.py`
**Function**: `_get_mcp_env()` (line ~902), `_generate_mcp_config()` (line ~926)
**Called by**: `_execute_task()`
**Calls**: `TaskItAuth.get_token()` for auth token

Key logic:
- `_get_mcp_env()` resolves auth token (explicit token > credentials login > empty)
- `_generate_mcp_config()` creates per-CLI config files (`.mcp.json` for Claude, `.gemini/settings.json` for Gemini, etc.)
- Config includes env vars: `TASKIT_URL`, `TASKIT_AUTH_TOKEN`, `TASKIT_TASK_ID`, `TASKIT_AUTHOR_EMAIL`, `TASKIT_AUTHOR_LABEL`
- Sets `context["mobile_mcp_enabled"] = True` when mobile MCP is available — this flag propagates to harness `execute()` to inject mobile MCP server config

Data in: task_id, agent_name, model name, mobile MCP availability
Data out: env dict + config file path for the target CLI + mobile_mcp_enabled context flag

---

## 2a. Mobile MCP: Config & Harness Injection

**File**: `odin/src/odin/mcps/mobile_mcp/config.py`
**Functions**: `get_tool_names()`, `get_server_fragment(harness_name)`
**Called by**: `_generate_mcp_config()` in orchestrator, harness `execute()` methods
**Calls**: Nothing — returns static config dicts

Key logic:
- 19 hardcoded tool names from `@mobilenext/mobile-mcp@latest` (external npm package)
- Key tools for proof: `mobile_save_screenshot`, `mobile_launch_app`, `mobile_click_on_screen_at_coordinates`, `mobile_get_element_tree`
- Per-harness server config generation:
  - Claude Code: `{"command": "npx", "args": ["-y", "@mobilenext/mobile-mcp@latest"]}`
  - Gemini/Qwen: same with `trust: true`
  - Codex: returns `-c` flag pairs (no `.mcp.json` support)
  - Kilo Code: includes `alwaysAllow` array for auto-approval
  - OpenCode (minimax/glm): `{"type": "local", "command": [...]}` format
- No env vars or auth tokens needed — mobile-mcp is stateless
- `get_server_fragment()` returns a copy to prevent shared mutable state

**File**: `odin/src/odin/harnesses/codex.py`
**Function**: `execute()` (line ~40)
**Called by**: orchestrator `_execute_task()`

Key logic (mobile injection):
- When `context.get("mobile_mcp_enabled")` is True:
  - `cmd.extend(["-c", 'mcp_servers.mobile.command="npx"'])`
  - `cmd.extend(["-c", 'mcp_servers.mobile.args=["-y", "@mobilenext/mobile-mcp@latest"]'])`
- Other harnesses receive mobile config via `_generate_mcp_config()` in the config file

Data in: `context["mobile_mcp_enabled"]` flag, harness name
Data out: MCP server config (embedded in CLI config file or as CLI flags)

---

## 3. MCP Tool: taskit_add_comment

**File**: `odin/src/odin/mcps/taskit_mcp/server.py`
**Function**: `taskit_add_comment()` (line 102)
**Called by**: Agent via MCP protocol
**Calls**: `TaskItToolClient.upload_screenshots()` then `TaskItToolClient.submit_proof()` when comment_type is proof

Key logic:
- Parameters: `content`, `task_id`, `comment_type`, `file_paths`, `screenshot_paths`, `metadata`
- `task_id` defaults to `TASKIT_TASK_ID` env var if not provided
- Routes by comment_type:
  - `"status_update"` → `client.post_comment()` (screenshot_paths ignored)
  - `"question"` → `client.ask_question()` (screenshot_paths ignored)
  - `"proof"` → screenshot upload flow (see below)
- Proof path with screenshots:
  1. If `screenshot_paths` is not None: call `client.upload_screenshots(screenshot_paths)`
  2. Collect URLs from response: `[att["url"] for att in uploaded]`
  3. Call `client.submit_proof(summary=content, files=file_paths, screenshot_urls=urls)`
- Upload failure handling: catches `FileNotFoundError`, `ValueError`, and generic `Exception` → returns `{"error": "Screenshot upload failed: ..."}` — proof is NOT submitted
- Returns `{"comment_id": result["id"]}`

There is also `taskit_add_attachment()` (line ~170) which duplicates the proof path when `attachment_type="proof"` but does NOT support screenshots.

Data in: MCP tool call with content, file_paths, screenshot_paths, comment_type
Data out: `{"comment_id": int}` or `{"error": str}`

---

## 4a. HTTP Client: TaskItToolClient.upload_screenshots

**File**: `odin/src/odin/tools/core.py`
**Function**: `upload_screenshots()` (line ~152)
**Called by**: `taskit_add_comment()` in MCP server (proof path only)
**Calls**: `httpx.post()` to taskit backend screenshots endpoint

Key logic:
- Validates: `file_paths` must not be empty (ValueError), each path must exist (FileNotFoundError)
- Reads each file into memory via `Path.read_bytes()`
- Guesses MIME type via `mimetypes.guess_type()`, defaults to `application/octet-stream`
- Constructs multipart files list: `[("files", (name, bytes, mime))]`
- Uses `_auth_headers()` (auth only, no Content-Type — httpx sets multipart boundary)
- POST to `{base_url}/tasks/{task_id}/screenshots/`
- Timeout: 60s (longer than normal 30s for large uploads)

Data in: `file_paths: list[str]`
Data out: `list[dict]` — `[{id, url, original_filename, content_type, file_size, uploaded_by, created_at}]`

---

## 4b. HTTP Client: TaskItToolClient.submit_proof

**File**: `odin/src/odin/tools/core.py`
**Function**: `submit_proof()` (line ~115)
**Called by**: `taskit_add_comment()` in MCP server
**Calls**: `httpx.post()` to taskit backend comments endpoint

Key logic:
- Builds proof attachment object: `{"type": "proof", "summary": ..., "files": [...], "steps": [...], "handover": ..., "screenshots": [...]}`
- `screenshots` key only present if `screenshot_urls` is provided (not None)
- Wraps content as `"Proof: {summary}"` for human-readable display
- Sets `comment_type: "proof"` explicitly
- Sends as JSON (Content-Type: application/json) — not multipart
- Auth via Bearer token in headers

Data in: `summary`, `steps`, `files`, `handover`, `screenshot_urls`
Data out: HTTP response JSON (comment object with id)

**HTTP payload with screenshots**:
```json
{
  "author_email": "claude+sonnet-4@odin.agent",
  "author_label": "claude (sonnet-4)",
  "content": "Proof: All tests pass",
  "comment_type": "proof",
  "attachments": [{
    "type": "proof",
    "summary": "All tests pass",
    "files": ["src/main.py"],
    "screenshots": [
      "http://localhost:8000/media/screenshots/2026/02/proof.png"
    ]
  }]
}
```

---

## 5a. Backend: Screenshot Upload Endpoint

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `TaskViewSet.screenshots()` (line ~895)
**Called by**: HTTP POST /tasks/{id}/screenshots/ (multipart)
**Calls**: `CommentAttachment.objects.create()`

Key logic:
- `@action(detail=True, methods=["post"], url_path="screenshots")`
- Reads files from `request.FILES.getlist("files")`
- Validates: at least 1 file required (400), each ≤ 10 MB (400)
- `author_email` from `request.data.get("author_email", "agent@odin.agent")`
- Creates `CommentAttachment` per file with `comment=None` (not linked to any comment yet)
- File stored via Django `FileField(upload_to="screenshots/%Y/%m/")` — date-partitioned
- Returns serialized list via `CommentAttachmentSerializer` with `context={"request": request}` for absolute URLs
- HTTP 201

Data in: multipart form with `files` (1+), optional `author_email`
Data out: `[{id, url, original_filename, content_type, file_size, uploaded_by, created_at}]`

---

## 5b. Backend: Comment Creation

**File**: `taskit/taskit-backend/tasks/views.py`
**Function**: `TaskViewSet.comments()` POST handler (line ~866)
**Called by**: HTTP POST /tasks/{id}/comments/
**Calls**: `TaskComment.objects.create()`

Key logic:
- Validates via `CreateTaskCommentSerializer`
- `attachments` field: `ListField(child=JSONField())` — accepts any JSON structure, no schema validation
- Creates `TaskComment` with all validated fields
- Returns serialized comment (HTTP 201) via `TaskCommentSerializer`
- `TaskCommentSerializer` now includes `file_attachments = CommentAttachmentSerializer(many=True, read_only=True)`
- For newly created comments, `file_attachments` is always `[]` (screenshots are on the task, not auto-linked to the comment)

Data in: JSON body with content, comment_type, attachments, author_email, author_label
Data out: Serialized TaskComment (id, task_id, content, attachments, comment_type, created_at, file_attachments)

---

## 6. Backend: Data Models

**File**: `taskit/taskit-backend/tasks/models.py`

### TaskComment (line 162)

Fields:
- `task` — FK to Task (CASCADE)
- `author_email` — EmailField
- `author_label` — CharField(255)
- `content` — TextField (the human-readable text)
- `attachments` — JSONField(default=list) (structured metadata — proof type, files, screenshots URLs)
- `comment_type` — CharField with choices: status_update, question, reply, proof, summary, reflection
- `created_at` — DateTimeField(auto_now_add)

### CommentAttachment (line ~185)

New model for binary file storage.

Fields:
- `comment` — FK to TaskComment (CASCADE), **nullable** — screenshots uploaded before proof comment exists
- `task` — FK to Task (CASCADE) — ensures cleanup on task deletion
- `file` — FileField(upload_to="screenshots/%Y/%m/") — actual binary stored on disk
- `original_filename` — CharField(255) — what the uploader called it
- `content_type` — CharField(100) — MIME type (default: application/octet-stream)
- `file_size` — BigIntegerField — bytes
- `uploaded_by` — EmailField — who uploaded it
- `created_at` — DateTimeField(auto_now_add)

Table: `comment_attachments`. Related name: `file_attachments` (from TaskComment), `attachments` (from Task).

The nullable `comment` FK enables the two-phase workflow: upload first (comment=null), then optionally link later.

---

## 7. Backend: Serializers

**File**: `taskit/taskit-backend/tasks/serializers.py`

### CommentAttachmentSerializer (line ~199)

- `url` — SerializerMethodField: uses `request.build_absolute_uri(obj.file.url)` for absolute URLs
- Read-only: id, url, original_filename, content_type, file_size, uploaded_by, created_at
- Requires `context={"request": request}` for URL building

### TaskCommentSerializer (line ~219)

- Now includes `file_attachments = CommentAttachmentSerializer(many=True, read_only=True)`
- Uses the `file_attachments` related_name from CommentAttachment → TaskComment FK

---

## 8. Backend: Settings & URLs

**File**: `taskit/taskit-backend/config/settings.py`
- `MEDIA_ROOT = BASE_DIR / "media"` — disk storage location
- `MEDIA_URL = "/media/"` — URL prefix
- `MultiPartParser` added to `DEFAULT_PARSER_CLASSES` alongside `JSONParser`

**File**: `taskit/taskit-backend/config/urls.py`
- `static(MEDIA_URL, document_root=MEDIA_ROOT)` when `DEBUG=True` — serves uploaded files in dev
- Production would need nginx/S3 serving

---

## 9. Frontend: API Fetch

**File**: `taskit/taskit-frontend/src/services/harness/HarnessTimeService.ts`
**Function**: `fetchTaskDetail()` (line ~420)
**Called by**: TaskDetailModal when opened
**Calls**: `GET /api/tasks/{taskId}/`

Key logic:
- Maps snake_case API response to camelCase TypeScript
- `attachments` passed through as-is (`c.attachments || []`)
- `commentType` falls back to `inferTypeFromAttachments()` for legacy comments
- `fileAttachments` mapped from `c.file_attachments`: id, url, originalFilename, contentType, fileSize, uploadedBy, createdAt
- `HarnessTaskComment` interface extended with `file_attachments?: Array<Record<string, unknown>>`

Data in: task ID
Data out: `DashTask` object with `comments: TaskComment[]` (each with `fileAttachments`)

---

## 10. Frontend: Types

**File**: `taskit/taskit-frontend/src/types/index.ts`

### CommentFileAttachment (new interface)

```typescript
interface CommentFileAttachment {
    id: number;
    url: string;
    originalFilename: string;
    contentType: string;
    fileSize: number;
    uploadedBy: string;
    createdAt: string;
}
```

### TaskComment (updated)

Added `fileAttachments?: CommentFileAttachment[]` — optional to not break existing code that creates TaskComment objects without it.

---

## 11. Frontend: Proof Rendering with Screenshots

**File**: `taskit/taskit-frontend/src/components/TaskDetailModal.tsx`
**Component**: `CommentItem` (line ~1287)
**Called by**: TaskDetailModal comment list

Key logic:
- Proof detection unchanged: `commentType === 'proof'` OR `attachments[].type === 'proof'`
- Styling unchanged: cyan left border + cyan background tint + "proof" badge
- Content rendering: still plain text for the proof summary

New screenshot gallery (after content, before trace/debug section):
- Condition: `comment.fileAttachments && comment.fileAttachments.length > 0`
- Filters to `contentType.startsWith('image/')` — non-image attachments skipped
- Each image: `<a href={url} target="_blank"><img src={url} lazy max-w-[300px] max-h-[200px] object-contain>`
- Filename shown below: `<span class="text-[10px] font-mono truncate">{originalFilename}</span>`
- Hover effect: `hover:border-cyan-500/50` — cyan to match proof theme
- Layout: `flex flex-wrap gap-2` — responsive image grid

Data in: `TaskComment` object with `fileAttachments`
Data out: React JSX with styled proof display + optional image gallery
