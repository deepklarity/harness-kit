"""Tests for structured JSONL logging.

Tags: [io] — writes to temp files, no LLM calls.
"""

import json
from pathlib import Path

import pytest

from odin.logging import OdinLogger


class TestOdinLogger:
    def test_creates_log_file(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        logger.log(action="test_action")
        assert logger.log_path.exists()

    def test_log_entry_is_valid_json(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        logger.log(action="plan_started", metadata={"spec_length": 42})

        lines = logger.log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["action"] == "plan_started"
        assert entry["metadata"]["spec_length"] == 42

    def test_log_has_timestamp(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        logger.log(action="test")

        entry = json.loads(logger.log_path.read_text().strip())
        assert "timestamp" in entry

    def test_log_with_task_id_and_agent(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        logger.log(action="task_started", task_id="abc123", agent="gemini")

        entry = json.loads(logger.log_path.read_text().strip())
        assert entry["task_id"] == "abc123"
        assert entry["agent"] == "gemini"

    def test_none_values_excluded(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        logger.log(action="test")

        entry = json.loads(logger.log_path.read_text().strip())
        assert "task_id" not in entry
        assert "agent" not in entry
        assert "output" not in entry

    def test_output_truncation(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        long_output = "x" * 5000
        logger.log(action="task_completed", output=long_output)

        entry = json.loads(logger.log_path.read_text().strip())
        assert len(entry["output"]) == 2000

    def test_multiple_entries_appended(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        logger.log(action="first")
        logger.log(action="second")
        logger.log(action="third")

        lines = logger.log_path.read_text().strip().splitlines()
        assert len(lines) == 3
        actions = [json.loads(line)["action"] for line in lines]
        assert actions == ["first", "second", "third"]

    def test_duration_ms_recorded(self, odin_dirs):
        logger = OdinLogger(str(odin_dirs["logs"]))
        logger.log(action="task_completed", duration_ms=1234.5)

        entry = json.loads(logger.log_path.read_text().strip())
        assert entry["duration_ms"] == 1234.5
