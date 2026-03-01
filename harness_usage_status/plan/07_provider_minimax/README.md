# 07 — Provider: MiniMax

## Goal
Implement the MiniMax usage provider.

## Tasks
- [ ] Create `src/harness_usage_status/providers/minimax.py`
- [ ] Research available MiniMax API/SDK for usage queries
- [ ] If programmatic access exists: implement using SDK/API
- [ ] If not: stub with `FIXME: requires browser automation fallback`
- [ ] Map response to `UsageInfo` model

## Research Needed
- MiniMax API documentation for usage/billing endpoints
- Any open-source Python SDK for MiniMax

## FIXME
- Browser automation fallback if no programmatic access is found

## Acceptance Criteria
- Provider returns `UsageInfo` if API access exists, or raises `NotImplementedError` with clear FIXME message
