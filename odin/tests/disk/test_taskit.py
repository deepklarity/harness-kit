"""Tests for taskit — TaskManager CRUD and lifecycle.

Tags: [io] — disk I/O, no LLM calls.
"""

import json
from pathlib import Path

import pytest

from odin.taskit import TaskManager
from odin.taskit.models import Task, TaskStatus


# ── CRUD ──────────────────────────────────────────────────────────────


class TestTaskManagerCRUD:
    def test_create_and_get(self, task_mgr):
        task = task_mgr.create_task("Title A", "Description A")
        assert task.id
        assert task.title == "Title A"
        assert task.status == TaskStatus.BACKLOG

        fetched = task_mgr.get_task(task.id)
        assert fetched is not None
        assert fetched.title == "Title A"

    def test_create_with_metadata(self, task_mgr):
        task = task_mgr.create_task("T", "D", metadata={"key": "val"})
        fetched = task_mgr.get_task(task.id)
        assert fetched.metadata["key"] == "val"

    def test_create_with_spec_id(self, task_mgr):
        task = task_mgr.create_task("T", "D", spec_id="sp_abc123")
        fetched = task_mgr.get_task(task.id)
        assert fetched.spec_id == "sp_abc123"

    def test_list_tasks_empty(self, task_mgr):
        assert task_mgr.list_tasks() == []

    def test_list_tasks_returns_all(self, task_mgr):
        task_mgr.create_task("A", "a")
        task_mgr.create_task("B", "b")
        task_mgr.create_task("C", "c")
        assert len(task_mgr.list_tasks()) == 3

    def test_delete_task(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        assert task_mgr.delete_task(task.id) is True
        assert task_mgr.get_task(task.id) is None

    def test_delete_nonexistent(self, task_mgr):
        assert task_mgr.delete_task("nonexistent") is False

    def test_get_nonexistent(self, task_mgr):
        assert task_mgr.get_task("nonexistent") is None


# ── Status lifecycle ──────────────────────────────────────────────────


class TestTaskLifecycle:
    def test_backlog_to_todo(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        result = task_mgr.assign_task(task.id, "gemini")
        assert result.status == TaskStatus.TODO
        assert result.assigned_agent == "gemini"

    def test_todo_to_in_progress(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        task_mgr.assign_task(task.id, "gemini")
        result = task_mgr.update_status(task.id, TaskStatus.IN_PROGRESS)
        assert result.status == TaskStatus.IN_PROGRESS

    def test_in_progress_to_done(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        task_mgr.assign_task(task.id, "gemini")
        task_mgr.update_status(task.id, TaskStatus.IN_PROGRESS)
        result = task_mgr.update_status(task.id, TaskStatus.DONE)
        assert result.status == TaskStatus.DONE

    def test_in_progress_to_failed(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        task_mgr.assign_task(task.id, "gemini")
        task_mgr.update_status(task.id, TaskStatus.IN_PROGRESS)
        result = task_mgr.update_status(task.id, TaskStatus.FAILED)
        assert result.status == TaskStatus.FAILED

    def test_assign_nonexistent_returns_none(self, task_mgr):
        assert task_mgr.assign_task("bad_id", "gemini") is None

    def test_update_status_nonexistent_returns_none(self, task_mgr):
        assert task_mgr.update_status("bad_id", TaskStatus.DONE) is None


# ── Prefix resolution ────────────────────────────────────────────────


class TestPrefixResolution:
    def test_unique_prefix_resolves(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        prefix = task.id[:4]
        resolved = task_mgr.resolve_task_id(prefix)
        assert resolved == task.id

    def test_ambiguous_prefix_returns_none(self, task_mgr):
        # Create multiple tasks and find a prefix that matches >1
        tasks = [task_mgr.create_task(f"T{i}", "D") for i in range(20)]
        # Full ID is always unique, but empty prefix matches all
        assert task_mgr.resolve_task_id("") is None

    def test_no_match_returns_none(self, task_mgr):
        task_mgr.create_task("T", "D")
        assert task_mgr.resolve_task_id("zzzzzzz") is None


# ── Filtering ─────────────────────────────────────────────────────────


class TestTaskFiltering:
    def test_filter_by_status(self, task_mgr):
        t1 = task_mgr.create_task("A", "a")
        t2 = task_mgr.create_task("B", "b")
        task_mgr.assign_task(t1.id, "gemini")

        backlog = task_mgr.list_tasks(status=TaskStatus.BACKLOG)
        todo = task_mgr.list_tasks(status=TaskStatus.TODO)
        assert len(backlog) == 1
        assert backlog[0].id == t2.id
        assert len(todo) == 1
        assert todo[0].id == t1.id

    def test_filter_by_agent(self, task_mgr):
        t1 = task_mgr.create_task("A", "a")
        t2 = task_mgr.create_task("B", "b")
        task_mgr.assign_task(t1.id, "gemini")
        task_mgr.assign_task(t2.id, "claude")

        result = task_mgr.list_tasks(agent="gemini")
        assert len(result) == 1
        assert result[0].id == t1.id

    def test_filter_by_spec_id(self, task_mgr):
        task_mgr.create_task("A", "a", spec_id="sp_aaa")
        task_mgr.create_task("B", "b", spec_id="sp_bbb")
        task_mgr.create_task("C", "c", spec_id="sp_aaa")

        result = task_mgr.list_tasks(spec_id="sp_aaa")
        assert len(result) == 2
        assert all(t.spec_id == "sp_aaa" for t in result)


# ── Comments ──────────────────────────────────────────────────────────


class TestTaskComments:
    def test_add_comment(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        result = task_mgr.add_comment(task.id, "gemini", "Looks good")
        assert len(result.comments) == 1
        assert result.comments[0].author == "gemini"
        assert result.comments[0].content == "Looks good"

    def test_add_multiple_comments(self, task_mgr):
        task = task_mgr.create_task("T", "D")
        task_mgr.add_comment(task.id, "gemini", "First")
        task_mgr.add_comment(task.id, "claude", "Second")
        fetched = task_mgr.get_task(task.id)
        assert len(fetched.comments) == 2

    def test_comment_on_nonexistent_returns_none(self, task_mgr):
        assert task_mgr.add_comment("bad_id", "gemini", "Nope") is None


# ── Index consistency ─────────────────────────────────────────────────


class TestIndexConsistency:
    def test_index_updated_on_create(self, odin_dirs):
        mgr = TaskManager(str(odin_dirs["tasks"]))
        task = mgr.create_task("T", "D")

        index_path = odin_dirs["tasks"] / "index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert task.id in index
        assert index[task.id]["title"] == "T"

    def test_index_updated_on_delete(self, odin_dirs):
        mgr = TaskManager(str(odin_dirs["tasks"]))
        task = mgr.create_task("T", "D")
        mgr.delete_task(task.id)

        index = json.loads((odin_dirs["tasks"] / "index.json").read_text())
        assert task.id not in index

    def test_index_reflects_status_change(self, odin_dirs):
        mgr = TaskManager(str(odin_dirs["tasks"]))
        task = mgr.create_task("T", "D")
        mgr.assign_task(task.id, "gemini")

        index = json.loads((odin_dirs["tasks"] / "index.json").read_text())
        assert index[task.id]["status"] == "todo"
        assert index[task.id]["assigned_agent"] == "gemini"


# ── Ready tasks (dependency resolution) ──────────────────────────────


class TestReadyTasks:
    def test_no_deps_all_ready(self, task_mgr):
        t1 = task_mgr.create_task("A", "a")
        t2 = task_mgr.create_task("B", "b")
        task_mgr.assign_task(t1.id, "gemini")
        task_mgr.assign_task(t2.id, "qwen")

        ready = task_mgr.get_ready_tasks()
        assert len(ready) == 2

    def test_dep_blocks_task(self, task_mgr):
        t1 = task_mgr.create_task("A", "a")
        t2 = task_mgr.create_task("B", "b")
        task_mgr.assign_task(t1.id, "gemini")
        task_mgr.assign_task(t2.id, "qwen")

        # t2 depends on t1
        task = task_mgr.get_task(t2.id)
        task.depends_on = [t1.id]
        task_mgr.save_task(task)

        ready = task_mgr.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == t1.id

    def test_dep_satisfied_unblocks_task(self, task_mgr):
        t1 = task_mgr.create_task("A", "a")
        t2 = task_mgr.create_task("B", "b")
        task_mgr.assign_task(t1.id, "gemini")
        task_mgr.assign_task(t2.id, "qwen")

        task = task_mgr.get_task(t2.id)
        task.depends_on = [t1.id]
        task_mgr.save_task(task)

        # Complete t1
        task_mgr.update_status(t1.id, TaskStatus.IN_PROGRESS)
        task_mgr.update_status(t1.id, TaskStatus.DONE)

        ready = task_mgr.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == t2.id

    def test_backlog_tasks_not_ready(self, task_mgr):
        t1 = task_mgr.create_task("A", "a")
        # Not assigned, so not ready
        ready = task_mgr.get_ready_tasks()
        assert len(ready) == 0
