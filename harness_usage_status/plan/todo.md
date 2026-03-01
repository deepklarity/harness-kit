# Harness Usage Status — Global Task Tracker

| # | Task | Status | Notes |
|---|------|--------|-------|
| 01 | [Project Scaffold](./01_project_scaffold/) | done | pyproject.toml, python-fire CLI, src/ layout |
| 02 | [Core Abstractions](./02_core_abstractions/) | done | Provider ABC, UsageInfo/StatusInfo models, registry |
| 03 | [Config System](./03_config_system/) | done | YAML config, env var fallback, pydantic validation |
| 04 | [Provider: Claude Code](./04_provider_claude_code/) | done | CLI for status; web API for usage (FIXME: cookie auth) |
| 05 | [Provider: Codex](./05_provider_codex/) | done | CLI for status; web API for usage (FIXME: cookie auth) |
| 06 | [Provider: Gemini](./06_provider_gemini/) | done | CLI for status; OAuth quota API (FIXME) |
| 07 | [Provider: MiniMax](./07_provider_minimax/) | done | Coding Plan /remains endpoint, region support |
| 08 | [Provider: GLM](./08_provider_glm/) | done | Monitor endpoints (quota/limit, model-usage), no-Bearer auth |
| 09 | [Provider: Qwen](./09_provider_qwen/) | done | CLI for status; FIXME: no account-level quota API |
| 10 | [Status Command](./10_status_command/) | done | quota + status + config commands via python-fire |
| 11 | [Testing](./11_testing/) | pending | pytest, mocked providers |

## Provider Approach (from CodexBar research)

### How CodexBar gets usage data (our reference):
- **Claude**: `GET https://claude.ai/api/organizations/{orgId}/usage` — cookie/OAuth auth
- **Codex**: `GET https://chatgpt.com/backend-api/wham/usage` — Bearer token auth
- **Gemini**: OAuth-backed quota API using Gemini CLI credentials
- **MiniMax**: `GET {host}/v1/api/openplatform/coding_plan/remains?GroupId={id}` — API key
- **GLM**: `GET {base}/api/monitor/usage/quota/limit` — API key (no Bearer prefix)

### CLI tools for status checks:
- `claude --version` — status check
- `codex --version` — status check
- `gemini --version` — status check
- `qwen --version` — status check

## FIXMEs
- Claude: Implement cookie/OAuth auth for web API usage endpoint
- Codex: Implement token auth for web API usage endpoint
- Gemini: Implement OAuth-backed quota API
- Qwen: No known account-level quota API; browser automation needed

## Dependencies
- 01 must complete before anything else
- 02, 03 must complete before providers (04-09)
- 04-09 can be done in parallel
- 10 depends on at least one provider being done
- 11 runs alongside or after each task
