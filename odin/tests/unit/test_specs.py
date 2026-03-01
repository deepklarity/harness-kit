"""Tests for spec pure functions: derive_spec_status, spec_short_tag.

Tags:
- [simple] — pure logic, no I/O
"""

from odin.specs import derive_spec_status, spec_short_tag
from odin.taskit.models import Task, TaskStatus


class TestDeriveSpecStatus:
    """[simple] derive_spec_status() — pure function, all 9 status rules."""

    def _make_task(self, status: TaskStatus) -> Task:
        return Task(id="test", title="test", description="test", status=status)

    def test_abandoned_overrides_everything(self):
        """abandoned=True always yields 'abandoned' regardless of tasks."""
        assert derive_spec_status([self._make_task(TaskStatus.DONE)], abandoned=True) == "abandoned"

    def test_empty_tasks(self):
        """No tasks yields 'empty'."""
        assert derive_spec_status([], abandoned=False) == "empty"

    def test_all_completed_is_done(self):
        """All DONE tasks yields 'done'."""
        tasks = [self._make_task(TaskStatus.DONE), self._make_task(TaskStatus.DONE)]
        assert derive_spec_status(tasks, abandoned=False) == "done"

    def test_any_in_progress_is_active(self):
        """Any IN_PROGRESS task yields 'active'."""
        tasks = [self._make_task(TaskStatus.DONE), self._make_task(TaskStatus.IN_PROGRESS)]
        assert derive_spec_status(tasks, abandoned=False) == "active"

    def test_any_failed_none_running_is_blocked(self):
        """FAILED + DONE (no in-progress) yields 'blocked'."""
        tasks = [self._make_task(TaskStatus.DONE), self._make_task(TaskStatus.FAILED)]
        assert derive_spec_status(tasks, abandoned=False) == "blocked"

    def test_in_progress_beats_failed(self):
        """IN_PROGRESS + FAILED yields 'active' (in-progress wins)."""
        tasks = [self._make_task(TaskStatus.FAILED), self._make_task(TaskStatus.IN_PROGRESS)]
        assert derive_spec_status(tasks, abandoned=False) == "active"

    def test_some_completed_some_assigned_is_partial(self):
        """DONE + TODO yields 'partial'."""
        tasks = [self._make_task(TaskStatus.DONE), self._make_task(TaskStatus.TODO)]
        assert derive_spec_status(tasks, abandoned=False) == "partial"

    def test_all_assigned_is_planned(self):
        """All TODO yields 'planned'."""
        tasks = [self._make_task(TaskStatus.TODO), self._make_task(TaskStatus.TODO)]
        assert derive_spec_status(tasks, abandoned=False) == "planned"

    def test_all_pending_is_draft(self):
        """All BACKLOG yields 'draft'."""
        tasks = [self._make_task(TaskStatus.BACKLOG), self._make_task(TaskStatus.BACKLOG)]
        assert derive_spec_status(tasks, abandoned=False) == "draft"


class TestSpecShortTag:
    """[simple] spec_short_tag() label generation."""

    def test_file_path(self):
        """File path input produces a short label."""
        tag = spec_short_tag("specs/user_profile_api.md")
        assert "profile" in tag or "api" in tag

    def test_inline_prompt(self):
        """Free-text prompt produces a truncated label (max 20 chars)."""
        tag = spec_short_tag("Fix the auth token refresh bug")
        assert 0 < len(tag) <= 20

    def test_heading(self):
        """A heading phrase produces a non-empty label."""
        assert len(spec_short_tag("Dark Mode Toggle")) > 0
