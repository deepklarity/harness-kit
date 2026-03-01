# 03 — Config System

## Goal
Load and validate provider credentials and settings.

## Tasks
- [ ] Define config schema in `src/harness_usage_status/config.py` using pydantic
  - `ProviderConfig`: api_key, base_url (optional), enabled (bool)
  - `AppConfig`: dict of provider name → ProviderConfig
- [ ] Config file location: `~/.config/harness_usage_status/config.yaml`
- [ ] Support env var overrides (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)
- [ ] Create sample config template at `config/config.sample.yaml`
- [ ] Add `config` CLI subcommand to show current config status (which providers are configured)

## Acceptance Criteria
- Config loads from YAML file
- Missing API keys fall back to env vars
- Validation errors produce clear messages
- Sample config is documented
