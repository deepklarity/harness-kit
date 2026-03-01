# GEMINI.md

## Project Purpose

A CLI tool that provides a centralized view of remaining usage quotas across multiple AI model/plan providers (subscription-based). Supports decision-making for model/task routing.

## Supported Providers

### CLI-based (subscription via installed CLI tools)
- **Claude Code** — `claude` CLI for status; web API for usage (`claude.ai/api/organizations/{orgId}/usage`)
- **Codex (OpenAI)** — `codex` CLI for status; web API for usage (`chatgpt.com/backend-api/wham/usage`)
- **Gemini** — `gemini` CLI for status; OAuth-backed quota API
- **Qwen** — `qwen` CLI for status; FIXME: no account-level quota API

### API-based (subscription via API keys)
- **MiniMax** — Coding Plan `/remains` endpoint
- **GLM (Zhipu AI)** — Monitor endpoints (`/api/monitor/usage/quota/limit`, etc.)

## Architecture

- **python-fire** CLI with `quota`, `status`, and `config` commands
- Provider pattern: `BaseProvider` ABC with `get_usage()` and `get_status()` methods
- Auto-registration via `@register_provider` decorator
- Config-driven: `~/.config/harness_usage_status/config.yaml`
- All endpoints, URLs, CLI paths configurable — easy to update when things change

## Key Files

- `src/harness_usage_status/cli.py` — CLI entry point (python-fire)
- `src/harness_usage_status/providers/base.py` — Provider ABC
- `src/harness_usage_status/providers/registry.py` — Auto-registration
- `src/harness_usage_status/config.py` — Config loading
- `src/harness_usage_status/cli_runner.py` — CLI subprocess runner
- `src/harness_usage_status/models.py` — UsageInfo, StatusInfo models

## Development Guidelines

- Keep provider integrations modular — each provider is its own module
- Prefer programmatic access over browser automation
- All URLs/endpoints should be configurable in YAML
- Use python-fire for clean CLI interface
- Output: rich tables (default) or JSON (`--output json`)
