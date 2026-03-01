# 01 — Project Scaffold

## Goal
Initialize the Python project structure and CLI entry point.

## Tasks
- [ ] Create `pyproject.toml` with project metadata and dependencies
- [ ] Set up directory structure:
  ```
  src/harness_usage_status/
    __init__.py
    cli.py
    providers/
      __init__.py
    models.py
    config.py
  tests/
    __init__.py
  ```
- [ ] Add CLI entry point using `click` in `cli.py`
- [ ] Configure `pyproject.toml` script entry: `harness-usage-status = "harness_usage_status.cli:main"`

## Dependencies
- click
- rich
- pydantic
- httpx
- pyyaml

## Acceptance Criteria
- `pip install -e .` works
- `harness-usage-status --help` prints usage info
