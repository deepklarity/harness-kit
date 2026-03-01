# 11 — Testing

## Goal
Add test coverage for providers, config, and the status command.

## Tasks
- [ ] Set up pytest in `pyproject.toml`
- [ ] Unit tests for each provider with mocked API responses
  - `tests/test_providers/test_claude_code.py`
  - `tests/test_providers/test_codex.py`
  - `tests/test_providers/test_gemini.py`
  - etc.
- [ ] Unit tests for config loading and validation (`tests/test_config.py`)
- [ ] Unit tests for data models (`tests/test_models.py`)
- [ ] Integration test for `status` command using Click's test runner (`tests/test_cli.py`)

## Dependencies
- pytest
- pytest-asyncio
- respx or pytest-httpx (for mocking httpx)

## Acceptance Criteria
- `pytest` runs and passes
- Each provider has at least one happy-path and one error-path test
- Config tests cover YAML loading, env var fallback, and validation errors
