# 04 — Provider: Claude Code

## Goal
Implement the Claude Code usage provider using the Anthropic API/SDK.

## Tasks
- [ ] Create `src/harness_usage_status/providers/claude_code.py`
- [ ] Use `anthropic` Python SDK or direct API calls via httpx
- [ ] Query usage/billing endpoint for current plan quota
- [ ] Map response to `UsageInfo` model
- [ ] Implement `get_status()` with a lightweight API ping

## Research Needed
- Identify the correct Anthropic API endpoint for usage/quota info
- Check if `anthropic` SDK exposes usage data directly

## Acceptance Criteria
- Provider returns accurate `UsageInfo` when API key is valid
- Graceful error handling for invalid/missing keys
