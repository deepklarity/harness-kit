"""Tests for spec I/O: SpecStore CRUD, multi-spec coexistence.

Tags:
- [io] — disk I/O only
"""

from odin.specs import SpecArchive, SpecStore
from odin.taskit import TaskManager


class TestSpecStore:
    """[io] SpecStore CRUD operations."""

    def test_save_and_load(self, tmp_path):
        """Save a spec archive and reload it by ID."""
        store = SpecStore(str(tmp_path))
        sid = "sp_test_save_load"
        spec = SpecArchive(id=sid, title="Test Spec", source="test.md", content="# Test\nSome content")
        store.save(spec)

        loaded = store.load(sid)
        assert loaded is not None
        assert loaded.id == sid
        assert loaded.title == "Test Spec"
        assert loaded.content == "# Test\nSome content"
        assert not loaded.abandoned

    def test_load_all(self, tmp_path):
        """load_all() returns all saved specs."""
        store = SpecStore(str(tmp_path))
        ids = [f"sp_test_{i}" for i in range(3)]
        for i, sid in enumerate(ids):
            store.save(SpecArchive(id=sid, title=f"Spec {i}", source="inline", content=f"Content {i}"))

        all_specs = store.load_all()
        assert len(all_specs) == 3
        assert {s.id for s in all_specs} == set(ids)

    def test_set_abandoned(self, tmp_path):
        """set_abandoned() marks spec and persists."""
        store = SpecStore(str(tmp_path))
        sid = "sp_abandon_me"
        store.save(SpecArchive(id=sid, title="To Abandon", source="inline", content="Will be abandoned"))

        result = store.set_abandoned(sid)
        assert result is not None
        assert result.abandoned
        assert store.load(sid).abandoned

    def test_resolve_prefix(self, tmp_path):
        """Prefix resolution finds spec by partial ID."""
        store = SpecStore(str(tmp_path))
        sid = "sp_abc123"
        store.save(SpecArchive(id=sid, title="Prefix Test", source="inline", content="Content"))

        assert store.resolve_spec_id("sp_abc") == sid
        assert store.resolve_spec_id("sp_xyz") is None

    def test_load_nonexistent(self, tmp_path):
        """Loading a nonexistent spec ID returns None."""
        store = SpecStore(str(tmp_path))
        assert store.load("sp_nonexistent") is None


class TestMultiSpecCoexistence:
    """[io] Multiple specs on the same board."""

    def test_tasks_from_different_specs(self, tmp_path):
        """Tasks are correctly filtered per spec_id."""
        mgr = TaskManager(str(tmp_path / "tasks"))
        spec_store = SpecStore(str(tmp_path / "specs"))

        sid1, sid2 = "sp_spec_a", "sp_spec_b"
        spec_store.save(SpecArchive(id=sid1, title="Spec A", source="inline", content="A"))
        spec_store.save(SpecArchive(id=sid2, title="Spec B", source="inline", content="B"))

        mgr.create_task("Task A1", "desc", spec_id=sid1)
        mgr.create_task("Task A2", "desc", spec_id=sid1)
        mgr.create_task("Task B1", "desc", spec_id=sid2)

        assert len(mgr.list_tasks(spec_id=sid1)) == 2
        assert len(mgr.list_tasks(spec_id=sid2)) == 1
        assert len(mgr.list_tasks()) == 3

    def test_abandoned_spec_excluded_from_exec(self, tmp_path):
        """Abandoned spec's tasks excluded from exec_all logic."""
        mgr = TaskManager(str(tmp_path / "tasks"))
        spec_store = SpecStore(str(tmp_path / "specs"))

        sid1, sid2 = "sp_active", "sp_abandoned"
        spec_store.save(SpecArchive(id=sid1, title="Active", source="inline", content="A"))
        spec_store.save(SpecArchive(id=sid2, title="Abandoned", source="inline", content="B"))

        mgr.create_task("Active task", "desc", spec_id=sid1)
        mgr.create_task("Abandoned task", "desc", spec_id=sid2)
        spec_store.set_abandoned(sid2)

        abandoned_ids = {s.id for s in spec_store.load_all() if s.abandoned}
        active_task_ids = [t.id for t in mgr.list_tasks() if t.spec_id not in abandoned_ids]
        assert len(active_task_ids) == 1

    def test_tasks_without_spec_id_still_work(self, tmp_path):
        """Old tasks without spec_id appear in list_tasks()."""
        mgr = TaskManager(str(tmp_path / "tasks"))
        t = mgr.create_task("Legacy task", "desc")
        assert t.spec_id is None

        all_tasks = mgr.list_tasks()
        assert len(all_tasks) == 1
        assert all_tasks[0].spec_id is None
