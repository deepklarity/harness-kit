# Agent Harnesses

Odin executes tasks by shelling out to AI coding CLI tools. Each harness wraps one CLI and is registered via `@register_harness("<name>")` in `odin/src/odin/harnesses/`.

The default binary for each harness can be overridden with `cli_command` in `.odin/config.yaml`.

## Quick Reference

| Harness   | CLI binary  | Execute command                                                        |
|-----------|-------------|------------------------------------------------------------------------|
| claude    | `claude`    | `claude -p "PROMPT" --output-format stream-json --verbose`             |
| gemini    | `gemini`    | `gemini -p "PROMPT" --output-format stream-json --yolo`                |
| codex     | `codex`     | `codex exec --skip-git-repo-check --full-auto "PROMPT"`               |
| qwen      | `qwen`      | `qwen -p "PROMPT" --output-format stream-json --yolo`                  |
| minimax   | `kilo`      | `kilo run --format json --auto "PROMPT"`                               |
| glm       | `opencode`  | `opencode run --format json "PROMPT"`                                  |

---

## Install & Upgrade

### Claude Code (Anthropic)

**Install (recommended — native installer):**
```bash
# macOS / Linux
curl -fsSL https://claude.ai/install.sh | bash

# Homebrew
brew install claude-code
```

**Upgrade:**
```bash
claude update
# or
brew upgrade claude-code
```

**Verify:** `claude --version` / `claude doctor`

**Prerequisites:** Active billing on Anthropic Console.

> The npm package `@anthropic-ai/claude-code` is deprecated as of v2.1.15. Use the native installer.

---

### Gemini CLI (Google)

**Install:**
```bash
npm install -g @google/gemini-cli
```

**Upgrade:**
```bash
npm install -g @google/gemini-cli@latest
```

**Release channels:** `@latest` (stable), `@preview`, `@nightly`.

**Prerequisites:** Node.js 18+, Google Gemini API key.

---

### Codex CLI (OpenAI)

**Install:**
```bash
npm install -g @openai/codex

# or Homebrew
brew install --cask codex
```

**Upgrade:**
```bash
npm install -g @openai/codex@latest
# or
brew upgrade codex
```

**Prerequisites:** Node.js 18+, ChatGPT Plus/Pro/Business/Edu/Enterprise plan or OpenAI API key.

---

### Qwen Code (Alibaba)

**Install:**
```bash
npm install -g @qwen-code/qwen-code@latest

# or via script
curl -fsSL https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen.sh | bash
```

**Upgrade:**
```bash
npm install -g @qwen-code/qwen-code@latest
```

**Auth:** Run `qwen` then `/auth` — choose Qwen OAuth (2,000 free daily API calls) or API key.

**Prerequisites:** Node.js 20+.

---

### Kilo Code / MiniMax

**Install:**
```bash
npm install -g @kilocode/cli
```

**Upgrade:**
```bash
npm install -g @kilocode/cli@latest
```

**Auth:** Run `kilo` then `/connect` to add provider credentials (MiniMax API key, etc.).

**Prerequisites:** Node.js 18+.

---

### OpenCode / GLM (Zhipu AI)

**Install:**
```bash
curl -fsSL https://opencode.ai/install | bash
```

**Upgrade:**
```bash
opencode upgrade
```

**Auth:** `opencode auth login` → select Z.AI provider, then `/models` to pick GLM-4.7 / GLM-5.

**Prerequisites:** None (standalone binary — no Node.js required).

---

## MCP Integration

Each CLI discovers MCP servers from a project-local config file. **Each CLI has its own config location and format** — there is no universal standard.

`odin init` generates all 6 config files automatically. `odin mcp_config [task_id]` regenerates them (optionally scoped to a specific task).

### Config File Reference

| CLI | Config file (project-local) | Format | Top-level key | `--mcp-config` flag? |
|-----|---------------------------|--------|---------------|---------------------|
| Claude Code | `.mcp.json` | JSON | `mcpServers` | Yes |
| Gemini CLI | `.gemini/settings.json` | JSON | `mcpServers` | No |
| Qwen CLI | `.qwen/settings.json` | JSON | `mcpServers` | No |
| Codex CLI | `.codex/config.toml` | TOML | `[mcp_servers.<name>]` | No |
| Kilo Code | `.kilocode/mcp.json` | JSON | `mcpServers` | No |
| OpenCode | `opencode.json` | JSON | `mcp` (different structure) | No |

### Format Examples

**Claude / Gemini / Qwen / Kilo** (same JSON structure, different file paths):
```json
{
  "mcpServers": {
    "taskit": {
      "command": "taskit-mcp",
      "env": {
        "TASKIT_URL": "http://localhost:8000"
      }
    }
  }
}
```

**Codex** (TOML format in `.codex/config.toml`):
```toml
[mcp_servers.taskit]
command = "taskit-mcp"

[mcp_servers.taskit.env]
TASKIT_URL = "http://localhost:8000"
```

**OpenCode** (different JSON structure in `opencode.json`):
```json
{
  "mcp": {
    "taskit": {
      "type": "local",
      "command": ["taskit-mcp"],
      "env": {
        "TASKIT_URL": "http://localhost:8000"
      }
    }
  }
}
```

### How Odin Uses MCP Configs

During `odin exec`, the orchestrator writes the correct config file to the task's working directory based on the assigned agent:

- **Claude**: Generates a temp JSON file in `.odin/logs/` and passes `--mcp-config <path>` (Claude Code is the only CLI that supports this flag).
- **All others**: Writes the config directly to the working directory for auto-discovery. No CLI flag is needed.

The config includes task-scoped env vars (`TASKIT_TASK_ID`, `TASKIT_AUTH_TOKEN`, `TASKIT_AUTHOR_EMAIL`) so the MCP server operates on the correct task with the correct identity.

---

## Verify All Harnesses

Quick check that all CLIs are on PATH:

```bash
for cmd in claude gemini codex qwen kilo opencode; do
  printf "%-10s " "$cmd"
  command -v $cmd >/dev/null 2>&1 && echo "✓ $(command -v $cmd)" || echo "✗ not found"
done
```
