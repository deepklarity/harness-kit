# 05 — Provider: Codex (OpenAI)

## Goal
Implement the OpenAI/Codex usage provider.

## Tasks
- [ ] Create `src/harness_usage_status/providers/codex.py`
- [ ] Use `openai` Python SDK or direct API calls
- [ ] Query usage/billing endpoints for quota and consumption
- [ ] Map response to `UsageInfo` model
- [ ] Implement `get_status()` with a lightweight API ping

## Research Needed
- OpenAI usage API endpoint (likely `/v1/dashboard/billing/usage` or similar)
- Rate limit headers as a proxy for quota info

## Acceptance Criteria
- Provider returns accurate `UsageInfo` when API key is valid
- Graceful error handling for invalid/missing keys
