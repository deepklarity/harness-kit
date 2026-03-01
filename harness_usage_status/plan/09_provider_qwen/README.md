# 09 — Provider: Qwen (Alibaba)

## Goal
Implement the Qwen usage provider using Alibaba's DashScope platform.

## Tasks
- [ ] Create `src/harness_usage_status/providers/qwen.py`
- [ ] Research `dashscope` Python SDK for usage/quota endpoints
- [ ] If programmatic access exists: implement using SDK/API
- [ ] If not: stub with `FIXME: requires browser automation fallback`
- [ ] Map response to `UsageInfo` model

## Research Needed
- DashScope SDK documentation for billing/usage
- Alibaba Cloud API for quota info

## FIXME
- Browser automation fallback if no programmatic access is found

## Acceptance Criteria
- Provider returns `UsageInfo` if API access exists, or raises `NotImplementedError` with clear FIXME message
