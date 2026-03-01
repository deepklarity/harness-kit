"""Shared fixtures for e2e snapshot tests."""
import json
import os
from pathlib import Path

import pytest

SNAPSHOTS_DIR = Path(__file__).parent


def load_snapshot(name):
    """Load a named snapshot directory, returning all JSON files as a dict."""
    snapshot_dir = SNAPSHOTS_DIR / name
    if not snapshot_dir.exists():
        raise FileNotFoundError(f"Snapshot '{name}' not found at {snapshot_dir}")

    data = {}
    for json_file in snapshot_dir.glob("*.json"):
        with open(json_file) as f:
            data[json_file.stem] = json.load(f)
    return data


@pytest.fixture
def full_harness_smoke():
    """Load the full_harness_smoke snapshot."""
    return load_snapshot("full_harness_smoke")
