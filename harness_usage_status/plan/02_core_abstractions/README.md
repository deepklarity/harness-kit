# 02 — Core Abstractions

## Goal
Define the base provider interface and shared data models.

## Tasks
- [ ] Create `Provider` ABC in `src/harness_usage_status/providers/base.py`
  - `async def get_usage() -> UsageInfo`
  - `async def get_status() -> ProviderStatus`
  - `name: str` property
- [ ] Define data models in `src/harness_usage_status/models.py`
  - `UsageInfo`: quota_limit, used, remaining, usage_pct, reset_date, plan_name
  - `ProviderStatus`: name, online (bool), latency_ms, last_checked
- [ ] Create provider registry in `src/harness_usage_status/providers/__init__.py`
  - Dict mapping provider name → Provider class
  - `get_provider(name, config) -> Provider`

## Acceptance Criteria
- Models can be instantiated and serialized
- Provider ABC enforces method implementation
- Registry can look up providers by name
