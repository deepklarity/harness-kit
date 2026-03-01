# MCP Servers: Technical Reference

This is the **technical reference** for MCP servers used in Odin — config formats, tool parameters, auth flow, per-CLI setup. For the philosophy of *why* agents communicate during execution (not after), see [Agent Communication](communication.md).

## What It Is

taskit-mcp is an MCP (Model Context Protocol) server that gives AI agents a live communication channel back to the TaskIt task board. Without it, agents are opaque black boxes until they finish — the human sees nothing during execution.

With MCP, agents can:
- Post status updates during long-running work
- Ask blocking questions when stuck or uncertain
- Submit proof of work before marking done

## The Communication Loop

```
odin orchestrator
  └─► agent CLI (claude/gemini/qwen) in tmux session
        └─► taskit-mcp (child process, stdio transport)
              └─► TaskIt backend (REST API)
                    └─► human (dashboard, sees comments in real time)
                          └─► replies to questions on dashboard
                                └─► agent receives reply (poll unblocks)
```

Each agent gets its own MCP server process. The orchestrator generates a per-task MCP config with environment variables (`TASKIT_TASK_ID`, `TASKIT_AUTH_TOKEN`, `TASKIT_AUTHOR_EMAIL`) baked in. The agent CLI spawns `taskit-mcp` as a child process using this config.

## Tool Reference

### taskit_add_comment

Post a status update or ask a blocking question.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | str | required | The comment text or question |
| `task_id` | str \| None | `TASKIT_TASK_ID` env | Task ID (auto-resolved from env in Odin context) |
| `comment_type` | enum | `status_update` | `status_update`, `question`, or `telemetry` |
| `metadata` | dict \| None | None | Optional metadata dict |

**comment_type="status_update"**: Posts and returns immediately.

**comment_type="question"**: Posts a question and **blocks** until a human replies on the TaskIt dashboard. The agent's context is frozen — no tokens consumed while polling.

**comment_type="telemetry"**: For automated metrics/diagnostics. Treated as low-priority in the UI.

### taskit_add_attachment

Attach file references or proof of work.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | str | required | Description or summary |
| `task_id` | str \| None | `TASKIT_TASK_ID` env | Task ID |
| `file_paths` | list[str] \| None | None | File paths or URLs (stored as metadata, not uploaded) |
| `attachment_type` | enum | `file` | `file` or `proof` |

**attachment_type="proof"**: Creates a proof-of-work comment with file references. Used before completion to demonstrate the work was done correctly.

## The Question/Reply Mechanism

When an agent calls `taskit_add_comment(content="...", comment_type="question")`:

1. The MCP server posts a comment with `comment_type=question` to TaskIt
2. TaskIt creates a pending question (visible on the dashboard with a reply input)
3. The MCP tool call **blocks** — the agent is frozen, consuming no tokens
4. A human sees the question on the dashboard and types a reply
5. The MCP server polls the TaskIt API until the reply appears
6. The reply is returned to the agent as the tool result
7. The agent resumes work with the human's answer

Currently `timeout=0` means poll indefinitely.

## How Odin Configures It

### Per-CLI Config Generation

`_generate_mcp_config()` in the orchestrator writes config files tailored to each CLI:

| CLI | Config Path | Format | Discovery |
|-----|-------------|--------|-----------|
| Claude Code | `.odin/logs/mcp_<task_id>.json` | JSON | `--mcp-config` flag |
| Gemini CLI | `<working_dir>/.gemini/settings.json` | JSON | Auto-discovery |
| Qwen CLI | `<working_dir>/.qwen/settings.json` | JSON | Auto-discovery |
| Codex | `<working_dir>/.codex/config.toml` | TOML | Auto-discovery |
| Kilo Code | `<working_dir>/.kilocode/mcp.json` | JSON | Auto-discovery |
| OpenCode | `<working_dir>/opencode.json` | JSON | Auto-discovery |

### Environment Variables

Each config embeds these env vars for the MCP server process:

- `TASKIT_URL` — TaskIt backend base URL
- `TASKIT_AUTH_TOKEN` — Bearer token (from TaskIt's `/auth/login/` endpoint)
- `TASKIT_TASK_ID` — The task this agent is executing
- `TASKIT_AUTHOR_EMAIL` — Actor identity for comments (e.g., `claude-sonnet-4-5-20250929@odin.agent`)
- `TASKIT_AUTHOR_LABEL` — Human-readable label (e.g., `claude / sonnet-4-5-20250929`)

### Auth Flow

1. Orchestrator extracts a Bearer token from its `TaskItAuth` handler
2. Token is embedded in the MCP config's env dict
3. MCP server uses the token for all API calls
4. If auth extraction fails, token defaults to empty string (for local dev without auth)

## Prompt Integration

When TaskIt is configured, `_wrap_prompt()` injects a "TaskIt MCP Tools" section into the agent's prompt. This tells the agent:

- Its task ID
- What tools are available and when to use them
- The communication cadence (2-4 updates per task, not every line)
- How to ask blocking questions
- How to submit proof of work

The ODIN-STATUS envelope remains as the **programmatic** channel — the orchestrator parses it to determine success/failure. MCP comments are the **human visibility** channel.

## Gaps and Limitations

### 1. No structured progress protocol

Agents post free-text updates. There's no way to parse "50% done" from a comment programmatically. A future `progress` field on comment metadata could enable progress bars on the dashboard.

### 2. Question timeout behavior

`timeout=0` polls forever. If a human never replies, the agent hangs indefinitely. There's no maximum timeout or cancellation mechanism. The human must reply or cancel the task from the dashboard.

### 3. MCP failure is silent

If the MCP server can't reach TaskIt (network issue, auth failure), the tool call fails and the agent continues without communication. The human sees nothing. The agent doesn't retry or abort — it simply loses the communication channel. Future: agents should detect MCP failures and fall back to ODIN-STATUS envelope notes.

### 4. No read-back

Agents can write (comments, attachments) but can't read other agents' outputs via MCP. If task B depends on task A, B gets A's output via prompt injection from the orchestrator — not through MCP. MCP is write-only during execution.

### 5. Auth token lifetime

MCP config embeds a token at execution start. TaskIt tokens have a 1-hour lifetime (Firebase ID tokens). If a task runs longer than 1 hour, MCP calls will fail mid-execution with 401 errors. There's no refresh mechanism — the token is baked into the config file.

### 6. Single-task scoping

Each MCP instance is scoped to one task. An agent can't post to a different task (e.g., a sibling in the DAG). This is by design (isolation) but limits cross-task coordination.

### 7. No MCP for the planning phase

`_decompose()` uses the base agent but doesn't generate MCP config. The planning agent can't ask questions during decomposition. If the plan is ambiguous, it guesses — there's no human-in-the-loop during planning via MCP.

---

## Chrome DevTools MCP

chrome-devtools-mcp is an external NPM package (`chrome-devtools-mcp`) that gives agents browser automation, debugging, performance tracing, and network inspection capabilities.

### What It Is

Unlike TaskIt MCP (which communicates back to the task board), Chrome DevTools MCP gives agents direct browser control — clicking elements, navigating pages, inspecting network requests, taking screenshots, and running performance traces.

### Configuration

Enable in `.odin/config.yaml`:

```yaml
mcps:
  - taskit
  - chrome-devtools
```

**NPM package**: `chrome-devtools-mcp` (run via `npx -y chrome-devtools-mcp@latest`)

No environment variables or auth needed — the package connects to a local Chrome/Chromium instance via the DevTools Protocol.

### Tool Reference (28 tools)

| Category | Tools |
|----------|-------|
| **Input** | `click`, `drag`, `fill`, `fill_form`, `hover`, `press_key`, `type_text`, `upload_file` |
| **Navigation** | `navigate_page`, `new_page`, `close_page`, `list_pages`, `select_page`, `resize_page`, `wait_for` |
| **Inspection** | `take_screenshot`, `take_snapshot`, `evaluate_script`, `handle_dialog` |
| **Emulation** | `emulate` |
| **Performance** | `performance_start_trace`, `performance_stop_trace`, `performance_analyze_insight`, `take_memory_snapshot` |
| **Network** | `list_network_requests`, `get_network_request`, `list_console_messages`, `get_console_message` |

### Per-CLI Config

Chrome DevTools MCP uses the same per-CLI config generation as other MCP servers:

| CLI | Approval mechanism | Config location |
|-----|-------------------|-----------------|
| Claude Code | `--allowedTools` CLI flag | `.odin/logs/mcp_<task_id>.json` |
| Gemini | `"trust": true` | `.gemini/settings.json` |
| Qwen | `"trust": true` | `.qwen/settings.json` |
| Codex | `-c` flag injection | `.codex/config.toml` |
| Kilo Code | `"alwaysAllow": [...]` | `.kilocode/mcp.json` |
| OpenCode | `"permission": {"tool": "allow"}` | `opencode.json` |

### Implementation

Config module: `odin/src/odin/mcps/chrome_devtools_mcp/config.py`

Follows the same pattern as `mobile_mcp/config.py` — hardcoded tool names (external NPM package, not introspectable), per-CLI server fragments, dispatcher function.
