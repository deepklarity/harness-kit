"""Unit tests for Orchestrator._build_reflection_context().

All tests use a minimal stub orchestrator — no real backends or I/O.

Coverage:
- No comments → empty string.
- No reflection comment → empty string.
- NEEDS_WORK reflection → formatted feedback block.
- PASS reflection → empty string (only NEEDS_WORK triggers context).
- Multiple reflections → latest NEEDS_WORK wins.
- Reflection with empty content → skipped.
- get_comments exception → empty string (graceful).
"""

from unittest.mock import MagicMock


def _make_orchestrator():
    """Create a minimal Orchestrator-like object with _build_reflection_context."""
    from odin.orchestrator import Orchestrator
    from odin.config import OdinConfig

    cfg = OdinConfig()
    orch = object.__new__(Orchestrator)
    orch.config = cfg
    orch.task_mgr = MagicMock()
    return orch


def _comment(comment_type, author_email, author_label, content, attachments=None):
    c = {
        "comment_type": comment_type,
        "author_email": author_email,
        "author_label": author_label,
        "content": content,
    }
    if attachments is not None:
        c["attachments"] = attachments
    return c


def _reflection_comment(verdict, content, reviewer_label="claude/claude-sonnet-4-5"):
    return _comment(
        comment_type="reflection",
        author_email="system@odin.agent",
        author_label=reviewer_label,
        content=content,
        attachments=[{"type": "reflection", "report_id": 1, "verdict": verdict}],
    )


class TestBuildReflectionContext:
    """[simple] _build_reflection_context() — pure orchestrator method."""

    def test_no_comments_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = []
        assert orch._build_reflection_context("task-1") == ""

    def test_no_reflection_comment_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", "Did work."),
            _comment("summary", "ai@taskit", "AI Summary", "Summary."),
        ]
        assert orch._build_reflection_context("task-1") == ""

    def test_needs_work_reflection_returns_feedback(self):
        orch = _make_orchestrator()
        feedback = "Missing back button in phase 2. Timer should persist."
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", "Done."),
            _reflection_comment("NEEDS_WORK", feedback),
        ]
        result = orch._build_reflection_context("task-1")
        assert "PREVIOUS ATTEMPT REVIEWED" in result
        assert "NEEDS WORK" in result
        assert feedback in result
        assert "claude/claude-sonnet-4-5" in result

    def test_pass_reflection_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("PASS", "Everything looks good."),
        ]
        assert orch._build_reflection_context("task-1") == ""

    def test_fail_reflection_returns_empty(self):
        """FAIL verdict doesn't trigger context — task goes to FAILED, not retry."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("FAIL", "Completely wrong."),
        ]
        assert orch._build_reflection_context("task-1") == ""

    def test_multiple_reflections_latest_needs_work_wins(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("NEEDS_WORK", "First round: fix the timer."),
            _comment("status_update", "agent@odin.agent", "Agent", "Re-executed."),
            _reflection_comment("NEEDS_WORK", "Second round: still missing back button."),
        ]
        result = orch._build_reflection_context("task-1")
        assert "Second round: still missing back button." in result
        assert "First round" not in result

    def test_empty_content_skipped(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("NEEDS_WORK", ""),
        ]
        assert orch._build_reflection_context("task-1") == ""

    def test_get_comments_exception_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.side_effect = RuntimeError("connection error")
        assert orch._build_reflection_context("task-1") == ""

    def test_lowercase_verdict_still_matches(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("needs_work", "Fix these issues."),
        ]
        result = orch._build_reflection_context("task-1")
        assert "Fix these issues." in result

    def test_no_attachments_skips_reflection(self):
        """Reflection comment without attachments is skipped (can't verify verdict)."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("reflection", "system@odin.agent", "reviewer", "Some feedback."),
        ]
        assert orch._build_reflection_context("task-1") == ""

    def test_address_all_issues_phrasing(self):
        """Prompt should clearly instruct the agent to address all issues."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("NEEDS_WORK", "Fix A, B, and C."),
        ]
        result = orch._build_reflection_context("task-1")
        assert "Address ALL" in result
