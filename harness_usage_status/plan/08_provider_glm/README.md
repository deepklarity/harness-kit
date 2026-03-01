# 08 — Provider: GLM (Zhipu AI)

## Goal
Implement the GLM usage provider using the Zhipu AI platform.

## Tasks
- [ ] Create `src/harness_usage_status/providers/glm.py`
- [ ] Research `zhipuai` Python SDK for usage/quota endpoints
- [ ] If programmatic access exists: implement using SDK/API
- [ ] If not: stub with `FIXME: requires browser automation fallback`
- [ ] Map response to `UsageInfo` model

## Research Needed
- Zhipu AI (zhipuai) SDK documentation for billing/usage
- API endpoint for quota info

## FIXME
- Browser automation fallback if no programmatic access is found

## Acceptance Criteria
- Provider returns `UsageInfo` if API access exists, or raises `NotImplementedError` with clear FIXME message
