"""Tests for odin.dependencies — centralized dependency checking.

Tags: [mock] — no I/O, pure logic with in-memory task data.

Tests verify that dependency status is always computed at runtime (never
cached), which is the key property that enables recovery when a human
fixes a failed upstream task.
"""

import pytest

from odin.dependencies import DepStatus, check_deps, get_failed_deps, get_unmet_deps, get_ready_tasks
from odin.taskit.models import Task, TaskStatus


# ── Helpers ─────────────────────────────────────────────────────────


def _task(id: str, status: TaskStatus = TaskStatus.TODO, depends_on=None) -> Task:
    """Create a minimal in-memory Task."""
    return Task(
        id=id,
        title=f"Task {id}",
        description="",
        status=status,
        depends_on=depends_on or [],
    )


def _make_resolver(tasks: list[Task]):
    """Build a resolver from a list of tasks."""
    by_id = {t.id: t for t in tasks}
    return lambda tid: by_id.get(tid)


# ── check_deps ──────────────────────────────────────────────────────


class TestCheckDeps:
    def test_no_deps_ready(self):
        t = _task("a")
        assert check_deps(t, _make_resolver([])) == DepStatus.READY

    def test_all_deps_done(self):
        dep = _task("d1", TaskStatus.DONE)
        t = _task("a", depends_on=["d1"])
        assert check_deps(t, _make_resolver([dep])) == DepStatus.READY

    def test_testing_counts_as_completed(self):
        """TESTING = reflection passed, unblocks dependents."""
        dep = _task("d1", TaskStatus.TESTING)
        t = _task("a", depends_on=["d1"])
        assert check_deps(t, _make_resolver([dep])) == DepStatus.READY

    def test_review_does_not_count_as_completed(self):
        """REVIEW = still under reflection, may loop back via NEEDS_WORK."""
        dep = _task("d1", TaskStatus.REVIEW)
        t = _task("a", depends_on=["d1"])
        assert check_deps(t, _make_resolver([dep])) == DepStatus.WAITING

    def test_mixed_done_and_testing_ready(self):
        d1 = _task("d1", TaskStatus.DONE)
        d2 = _task("d2", TaskStatus.TESTING)
        t = _task("a", depends_on=["d1", "d2"])
        assert check_deps(t, _make_resolver([d1, d2])) == DepStatus.READY

    def test_in_progress_dep_waiting(self):
        dep = _task("d1", TaskStatus.IN_PROGRESS)
        t = _task("a", depends_on=["d1"])
        assert check_deps(t, _make_resolver([dep])) == DepStatus.WAITING

    def test_todo_dep_waiting(self):
        dep = _task("d1", TaskStatus.TODO)
        t = _task("a", depends_on=["d1"])
        assert check_deps(t, _make_resolver([dep])) == DepStatus.WAITING

    def test_executing_dep_waiting(self):
        dep = _task("d1", TaskStatus.EXECUTING)
        t = _task("a", depends_on=["d1"])
        assert check_deps(t, _make_resolver([dep])) == DepStatus.WAITING

    def test_failed_dep_blocked(self):
        dep = _task("d1", TaskStatus.FAILED)
        t = _task("a", depends_on=["d1"])
        assert check_deps(t, _make_resolver([dep])) == DepStatus.BLOCKED

    def test_mixed_failed_and_done_blocked(self):
        """BLOCKED takes priority over READY."""
        d1 = _task("d1", TaskStatus.DONE)
        d2 = _task("d2", TaskStatus.FAILED)
        t = _task("a", depends_on=["d1", "d2"])
        assert check_deps(t, _make_resolver([d1, d2])) == DepStatus.BLOCKED

    def test_unknown_dep_waiting(self):
        """Dep not found by resolver → treated as unmet (waiting)."""
        t = _task("a", depends_on=["nonexistent"])
        assert check_deps(t, _make_resolver([])) == DepStatus.WAITING

    # --- Recovery scenarios ---

    def test_failed_dep_fixed_to_done_unblocks(self):
        """Simulates human fixing a failed dep: change status, re-check."""
        dep = _task("d1", TaskStatus.FAILED)
        t = _task("a", depends_on=["d1"])
        resolver = _make_resolver([dep])

        assert check_deps(t, resolver) == DepStatus.BLOCKED

        # Human fixes it
        dep.status = TaskStatus.DONE
        assert check_deps(t, resolver) == DepStatus.READY

    def test_failed_dep_retried_to_in_progress_waiting(self):
        """Human retries a failed dep → stays WAITING (not BLOCKED)."""
        dep = _task("d1", TaskStatus.FAILED)
        t = _task("a", depends_on=["d1"])
        resolver = _make_resolver([dep])

        assert check_deps(t, resolver) == DepStatus.BLOCKED

        dep.status = TaskStatus.IN_PROGRESS
        assert check_deps(t, resolver) == DepStatus.WAITING

    def test_failed_dep_reset_to_todo_waiting(self):
        """Dep goes FAILED → TODO (reset for re-execution) → WAITING."""
        dep = _task("d1", TaskStatus.FAILED)
        t = _task("a", depends_on=["d1"])
        resolver = _make_resolver([dep])

        assert check_deps(t, resolver) == DepStatus.BLOCKED

        dep.status = TaskStatus.TODO
        assert check_deps(t, resolver) == DepStatus.WAITING

    def test_three_deps_complete_in_order(self):
        """Three deps completing one at a time, in different orders."""
        d1 = _task("d1", TaskStatus.IN_PROGRESS)
        d2 = _task("d2", TaskStatus.TODO)
        d3 = _task("d3", TaskStatus.IN_PROGRESS)
        t = _task("a", depends_on=["d1", "d2", "d3"])
        resolver = _make_resolver([d1, d2, d3])

        assert check_deps(t, resolver) == DepStatus.WAITING

        d3.status = TaskStatus.DONE
        assert check_deps(t, resolver) == DepStatus.WAITING

        d1.status = TaskStatus.TESTING
        assert check_deps(t, resolver) == DepStatus.WAITING

        d2.status = TaskStatus.DONE
        assert check_deps(t, resolver) == DepStatus.READY

    def test_three_deps_one_fails_one_done_one_running(self):
        d1 = _task("d1", TaskStatus.DONE)
        d2 = _task("d2", TaskStatus.FAILED)
        d3 = _task("d3", TaskStatus.IN_PROGRESS)
        t = _task("a", depends_on=["d1", "d2", "d3"])
        assert check_deps(t, _make_resolver([d1, d2, d3])) == DepStatus.BLOCKED

    def test_multi_level_chain_recovery(self):
        """A→B→C: A fails, gets fixed, B becomes ready, B completes, C becomes ready."""
        a = _task("a", TaskStatus.FAILED)
        b = _task("b", TaskStatus.TODO, depends_on=["a"])
        c = _task("c", TaskStatus.TODO, depends_on=["b"])
        resolver = _make_resolver([a, b, c])

        # B blocked by A
        assert check_deps(b, resolver) == DepStatus.BLOCKED
        # C waiting on B (which is in TODO, not FAILED, so WAITING not BLOCKED)
        assert check_deps(c, resolver) == DepStatus.WAITING

        # Fix A
        a.status = TaskStatus.DONE
        assert check_deps(b, resolver) == DepStatus.READY
        assert check_deps(c, resolver) == DepStatus.WAITING

        # B completes
        b.status = TaskStatus.DONE
        assert check_deps(c, resolver) == DepStatus.READY


# ── get_failed_deps ─────────────────────────────────────────────────


class TestGetFailedDeps:
    def test_no_deps(self):
        t = _task("a")
        assert get_failed_deps(t, _make_resolver([])) == []

    def test_returns_failed_ids(self):
        d1 = _task("d1", TaskStatus.DONE)
        d2 = _task("d2", TaskStatus.FAILED)
        t = _task("a", depends_on=["d1", "d2"])
        assert get_failed_deps(t, _make_resolver([d1, d2])) == ["d2"]


# ── get_unmet_deps ──────────────────────────────────────────────────


class TestGetUnmetDeps:
    def test_no_deps(self):
        t = _task("a")
        assert get_unmet_deps(t, _make_resolver([])) == []

    def test_returns_unmet_ids(self):
        d1 = _task("d1", TaskStatus.DONE)
        d2 = _task("d2", TaskStatus.IN_PROGRESS)
        t = _task("a", depends_on=["d1", "d2"])
        assert get_unmet_deps(t, _make_resolver([d1, d2])) == ["d2"]

    def test_unknown_dep_is_unmet(self):
        t = _task("a", depends_on=["missing"])
        assert get_unmet_deps(t, _make_resolver([])) == ["missing"]


# ── get_ready_tasks ─────────────────────────────────────────────────


class TestGetReadyTasks:
    def test_no_deps_todo_is_ready(self):
        t = _task("a", TaskStatus.TODO)
        ready = get_ready_tasks([t], _make_resolver([t]))
        assert [r.id for r in ready] == ["a"]

    def test_non_todo_skipped(self):
        """Only TODO tasks are considered."""
        t = _task("a", TaskStatus.IN_PROGRESS)
        ready = get_ready_tasks([t], _make_resolver([t]))
        assert ready == []

    def test_satisfied_deps_ready(self):
        dep = _task("d1", TaskStatus.DONE)
        t = _task("a", TaskStatus.TODO, depends_on=["d1"])
        ready = get_ready_tasks([t], _make_resolver([dep, t]))
        assert [r.id for r in ready] == ["a"]

    def test_failed_dep_not_ready(self):
        dep = _task("d1", TaskStatus.FAILED)
        t = _task("a", TaskStatus.TODO, depends_on=["d1"])
        ready = get_ready_tasks([t], _make_resolver([dep, t]))
        assert ready == []

    def test_waiting_dep_not_ready(self):
        dep = _task("d1", TaskStatus.IN_PROGRESS)
        t = _task("a", TaskStatus.TODO, depends_on=["d1"])
        ready = get_ready_tasks([t], _make_resolver([dep, t]))
        assert ready == []

    def test_preserves_order(self):
        t1 = _task("t1", TaskStatus.TODO)
        t2 = _task("t2", TaskStatus.TODO)
        t3 = _task("t3", TaskStatus.TODO)
        ready = get_ready_tasks([t1, t2, t3], _make_resolver([t1, t2, t3]))
        assert [r.id for r in ready] == ["t1", "t2", "t3"]

    def test_recovery_scenario(self):
        """After fixing a failed dep, the dependent shows up as ready."""
        dep = _task("d1", TaskStatus.FAILED)
        t = _task("a", TaskStatus.TODO, depends_on=["d1"])
        resolver = _make_resolver([dep, t])

        assert get_ready_tasks([t], resolver) == []

        dep.status = TaskStatus.DONE
        ready = get_ready_tasks([t], resolver)
        assert [r.id for r in ready] == ["a"]
