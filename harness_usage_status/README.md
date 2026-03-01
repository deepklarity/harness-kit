# Harness Usage status

Centralized usage quota and provider health viewer for AI model subscriptions.

Check remaining quotas and status across Claude Code, Codex, Gemini, Qwen, MiniMax, and GLM from one CLI.

---

## Prerequisites

- Python 3.9+
- `pip`
- For CLI-based providers: corresponding CLIs installed on `PATH` (`claude`, `codex`, `gemini`, `qwen`)
- For API-based providers: API keys for MiniMax/GLM when enabled

> All commands below assume you are in `harness_usage_status/`.

---

## Step-by-Step Setup

1. Move to project directory:

```bash
cd harness_usage_status
```

2. Create and activate a virtual environment (or activate existing one):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

3. Install package:

```bash
pip install -e .
```

---

## Install

> If you already completed **Step-by-Step Setup**, you can skip this section.

```bash
pip install -e .
```

Or install from Git:

```bash
pip install git+https://github.com/YOUR_ORG/harness-usage-status.git
```

---

## Quick Start

```bash
# Provider health for all enabled providers
harness-usage-status status

# Usage quota for all enabled providers
harness-usage-status quota

# Single provider
harness-usage-status status --provider claude_code

# JSON output
harness-usage-status quota --output json

# Show loaded config and provider enablement
harness-usage-status config
```

---

## Configuration

Config file search order:

1. Explicit path: `harness-usage-status --config /path/to/config.yaml`
2. Local override: `./config/config.yaml` (relative to current working directory)
3. Global default: `~/.config/harness_usage_status/config.yaml`

> If no config file is found, all providers are enabled with defaults.

### Local config setup

```bash
mkdir -p config
cp config/config.sample.yaml config/config.yaml
cp config/.env.example config/.env
```

Edit:

- `config/config.yaml` for provider enable/disable and provider options
- `config/.env` for API keys and tokens

### Global config setup (optional)

```bash
mkdir -p ~/.config/harness_usage_status
cp config/config.sample.yaml ~/.config/harness_usage_status/config.yaml
```

---

## Environment Variables

The app loads environment variables in this order:

1. `./.env` (project root, if present)
2. `./config/.env` (overrides project root values when present)

Common variables:

```dotenv
# MiniMax
MINIMAX_API_KEY=your_key_here
MINIMAX_GROUP_ID=your_group_id

# GLM
ZAI_API_KEY=your_key_here

# Optional token overrides for CLI-based providers
CLAUDE_CODE_OAUTH_TOKEN=...
CODEX_ACCESS_TOKEN=...
```

---

## Provider Support

| Provider | Type | Auth Source | Quota Source |
|---|---|---|---|
| Claude Code | OAuth/API | macOS Keychain, `~/.claude/.credentials.json`, or `CLAUDE_CODE_OAUTH_TOKEN` | `api.anthropic.com/api/oauth/usage` |
| Codex (OpenAI) | OAuth/API | `~/.codex/auth.json` or `CODEX_ACCESS_TOKEN` | `chatgpt.com/backend-api/wham/usage` |
| Gemini | CLI/OAuth | installed `gemini` CLI + OAuth creds | CLI/API-backed stats buckets (per-model) |
| Qwen | CLI | installed `qwen` CLI | session-level stats only (no account-level quota API) |
| MiniMax | API | `MINIMAX_API_KEY` + `MINIMAX_GROUP_ID` | Coding Plan `/remains` endpoint |
| GLM (Zhipu AI) | API | `ZAI_API_KEY` | Monitor `/quota/limit` endpoint |

### Provider notes

- **Claude Code** token lookup order:
  1. `CLAUDE_CODE_OAUTH_TOKEN`
  2. macOS Keychain entry `Claude Code-credentials`
  3. `~/.claude/.credentials.json`
- **Codex** token lookup order:
  1. `CODEX_ACCESS_TOKEN`
  2. `~/.codex/auth.json`

---

## Commands

| Command | Description |
|---|---|
| `status` | Show provider health (`online`/`offline`, latency, message) |
| `quota` | Show usage quota (used, remaining, limit, usage %) |
| `config` | Show loaded config source and provider settings |

### Common flags

| Flag | Description |
|---|---|
| `--config PATH` | Use a specific config YAML |
| `--provider NAME` | Query one provider only (example: `claude_code`, `minimax`) |
| `--output FORMAT` | `table` (default) or `json` |

---

## Config Overrides

Endpoints and provider settings are config-driven. Example:

```yaml
providers:
  minimax:
    enabled: true
    base_url: https://platform.minimaxi.com
    group_id: "12345"
    region: "cn"

  glm:
    enabled: true
    base_url: https://api.z.ai
    platform: "global"
```

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

---

## Troubleshooting

- **No providers configured or enabled**
  - Check `config/config.yaml` and provider `enabled` flags.
- **Provider appears offline**
  - Verify required CLI is installed on `PATH` or API keys are set correctly.
- **Wrong config loaded**
  - Run `harness-usage-status config` and verify the reported config source path.
- **JSON output needed for scripting**
  - Use `--output json` on `status` or `quota`.

---

## Project Links

- Monorepo root: [../README.md](../README.md)
- Contributing: [../CONTRIBUTING.md](../CONTRIBUTING.md)
- Security: [../SECURITY.md](../SECURITY.md)
