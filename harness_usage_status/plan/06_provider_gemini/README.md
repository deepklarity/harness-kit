# 06 — Provider: Gemini

## Goal
Implement the Google Gemini usage provider.

## Tasks
- [ ] Create `src/harness_usage_status/providers/gemini.py`
- [ ] Use `google-generativeai` SDK or Google Cloud API
- [ ] Query usage/quota info for the configured project
- [ ] Map response to `UsageInfo` model
- [ ] Implement `get_status()` with a lightweight API ping

## Research Needed
- Google AI Studio quota API or Cloud billing API for Gemini usage
- Whether free-tier vs paid-tier quotas are queryable

## Acceptance Criteria
- Provider returns accurate `UsageInfo` when API key is valid
- Graceful error handling for invalid/missing keys
