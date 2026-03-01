"""Tests for cost tracking — store, tracker, and models.

Tags: [io] + [simple] — disk I/O, no LLM calls.
"""

import json
from pathlib import Path

import pytest

from odin.cost_tracking import CostStore, CostTracker, SpecCostSummary, TaskCostRecord
from odin.models import TaskResult


# ── TaskCostRecord model ──────────────────────────────────────────────


class TestTaskCostRecord:
    def test_minimal_creation(self):
        r = TaskCostRecord(task_id="t1")
        assert r.task_id == "t1"
        assert r.success is True
        assert r.spec_id is None

    def test_full_creation(self):
        r = TaskCostRecord(
            task_id="t1",
            spec_id="sp_abc",
            agent="gemini",
            model="gemini-2.5-flash",
            duration_ms=1500.0,
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
            success=True,
        )
        assert r.total_tokens == 300
        assert r.agent == "gemini"


# ── CostStore persistence ────────────────────────────────────────────


class TestCostStore:
    def test_save_and_load(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        record = TaskCostRecord(
            task_id="t1", spec_id="sp_abc", agent="gemini", duration_ms=100.0
        )
        store.save_record(record)

        loaded = store.load_by_spec("sp_abc")
        assert len(loaded) == 1
        assert loaded[0].task_id == "t1"
        assert loaded[0].agent == "gemini"

    def test_multiple_records_same_spec(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        for i in range(3):
            store.save_record(
                TaskCostRecord(task_id=f"t{i}", spec_id="sp_abc", agent="gemini")
            )

        loaded = store.load_by_spec("sp_abc")
        assert len(loaded) == 3

    def test_load_all_across_specs(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        store.save_record(TaskCostRecord(task_id="t1", spec_id="sp_a"))
        store.save_record(TaskCostRecord(task_id="t2", spec_id="sp_b"))

        all_records = store.load_all()
        assert len(all_records) == 2

    def test_load_empty_spec(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        assert store.load_by_spec("nonexistent") == []

    def test_orphan_tasks_use_underscore_orphan(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        store.save_record(TaskCostRecord(task_id="t1", spec_id=None))

        path = odin_dirs["costs"] / "costs__orphan.json"
        assert path.exists()

    def test_corrupt_json_returns_empty(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        path = odin_dirs["costs"] / "costs_sp_bad.json"
        path.write_text("not valid json {{{")

        loaded = store.load_by_spec("sp_bad")
        assert loaded == []


# ── CostStore summarization ──────────────────────────────────────────


class TestCostStoreSummarize:
    def test_summarize_spec(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        store.save_record(TaskCostRecord(
            task_id="t1", spec_id="sp_a", agent="gemini",
            duration_ms=100.0, total_tokens=500,
        ))
        store.save_record(TaskCostRecord(
            task_id="t2", spec_id="sp_a", agent="claude",
            duration_ms=200.0, total_tokens=1000,
        ))

        summary = store.summarize_spec("sp_a")
        assert summary.spec_id == "sp_a"
        assert summary.task_count == 2
        assert summary.total_duration_ms == 300.0
        assert summary.total_tokens == 1500
        assert summary.invocations_by_agent == {"gemini": 1, "claude": 1}

    def test_summarize_all(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        store.save_record(TaskCostRecord(task_id="t1", spec_id="sp_a"))
        store.save_record(TaskCostRecord(task_id="t2", spec_id="sp_b"))

        summaries = store.summarize_all()
        assert len(summaries) == 2
        spec_ids = {s.spec_id for s in summaries}
        assert "sp_a" in spec_ids
        assert "sp_b" in spec_ids

    def test_summarize_empty_spec(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        summary = store.summarize_spec("nonexistent")
        assert summary.task_count == 0
        assert summary.total_duration_ms == 0.0


# ── CostTracker ───────────────────────────────────────────────────────


class TestCostTracker:
    def test_record_task(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        tracker = CostTracker(store)

        result = TaskResult(
            success=True,
            output="done",
            duration_ms=150.0,
            agent="gemini",
            metadata={"usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}},
        )

        record = tracker.record_task("t1", "sp_abc", result, model="gemini-2.5-flash")
        assert record.task_id == "t1"
        assert record.spec_id == "sp_abc"
        assert record.model == "gemini-2.5-flash"
        assert record.input_tokens == 10
        assert record.output_tokens == 20
        assert record.total_tokens == 30

        # Verify it was persisted
        loaded = store.load_by_spec("sp_abc")
        assert len(loaded) == 1

    def test_record_task_no_usage(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        tracker = CostTracker(store)

        result = TaskResult(success=True, output="done", agent="qwen")
        record = tracker.record_task("t1", "sp_abc", result)
        assert record.input_tokens is None
        assert record.total_tokens is None

    def test_record_task_openai_style_tokens(self, odin_dirs):
        store = CostStore(str(odin_dirs["costs"]))
        tracker = CostTracker(store)

        result = TaskResult(
            success=True,
            output="done",
            agent="codex",
            metadata={"usage": {"prompt_tokens": 50, "completion_tokens": 100}},
        )

        record = tracker.record_task("t1", None, result)
        assert record.input_tokens == 50
        assert record.output_tokens == 100
