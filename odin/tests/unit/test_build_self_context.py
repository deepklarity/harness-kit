"""Unit tests for Orchestrator._build_self_context().

All tests use a minimal stub orchestrator — no real backends or I/O.

Coverage:
- No comments → empty string (no-op).
- No summary comment → empty string.
- Summary comment alone → context block, no "human notes" section.
- Summary + post-summary human notes → context block includes notes.
- Summary + only agent comments after it → no "human notes" section.
- Multiple summaries → latest summary wins; older summary is ignored.
- Human notes from system@taskit excluded.
"""

from unittest.mock import MagicMock


def _make_orchestrator():
    """Create a minimal Orchestrator-like object with just _build_self_context."""
    from odin.orchestrator import Orchestrator
    from odin.config import OdinConfig

    cfg = OdinConfig()
    orch = object.__new__(Orchestrator)
    orch.config = cfg
    orch.task_mgr = MagicMock()
    return orch


def _comment(comment_type, author_email, author_label, content):
    return {
        "comment_type": comment_type,
        "author_email": author_email,
        "author_label": author_label,
        "content": content,
    }


class TestBuildSelfContext:
    """[simple] _build_self_context() — pure orchestrator method."""

    def test_no_comments_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = []
        assert orch._build_self_context("task-1") == ""

    def test_no_summary_comment_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", "Did some work."),
            _comment("status_update", "alice@example.com", "Alice", "Looks good."),
        ]
        assert orch._build_self_context("task-1") == ""

    def test_summary_alone_returns_context_block(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", "Initial attempt failed."),
            _comment("summary", "ai@taskit", "AI Summary", "The task attempted X but failed due to Y."),
        ]
        result = orch._build_self_context("task-1")
        assert "Task summary (from AI Summary):" in result
        assert "The task attempted X but failed due to Y." in result
        assert "Human notes" not in result

    def test_summary_with_post_human_notes(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", "First try."),
            _comment("summary", "ai@taskit", "AI Summary", "Summary of first attempt."),
            _comment("status_update", "alice@example.com", "Alice", "Please also fix the tests."),
            _comment("question", "bob@example.com", "Bob", "What approach should I use?"),
        ]
        result = orch._build_self_context("task-1")
        assert "Task summary (from AI Summary):" in result
        assert "Summary of first attempt." in result
        assert "Human notes added since summary:" in result
        assert "Alice" in result
        assert "Please also fix the tests." in result
        assert "Bob" in result
        assert "What approach should I use?" in result

    def test_only_agent_comments_after_summary_no_notes_section(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("summary", "ai@taskit", "AI Summary", "Summary text here."),
            _comment("status_update", "claude+sonnet@odin.agent", "claude (sonnet)", "Agent ran step 1."),
            _comment("status_update", "odin@odin.agent", "odin", "Orchestrator note."),
        ]
        result = orch._build_self_context("task-1")
        assert "Task summary (from AI Summary):" in result
        assert "Summary text here." in result
        assert "Human notes" not in result

    def test_system_taskit_comments_excluded(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("summary", "ai@taskit", "AI Summary", "The summary."),
            _comment("status_update", "system@taskit", "system", "System note."),
        ]
        result = orch._build_self_context("task-1")
        assert "Human notes" not in result
        assert "System note" not in result

    def test_multiple_summaries_latest_wins(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "alice@example.com", "Alice", "Initial try."),
            _comment("summary", "ai@taskit", "AI Summary", "OLD summary — first attempt."),
            _comment("status_update", "alice@example.com", "Alice", "Added more context."),
            _comment("summary", "ai@taskit", "AI Summary", "NEW summary — second attempt, includes old."),
            _comment("status_update", "bob@example.com", "Bob", "Post-second-summary note."),
        ]
        result = orch._build_self_context("task-1")
        assert "NEW summary — second attempt" in result
        assert "OLD summary" not in result
        assert "Post-second-summary note." in result
        # "Added more context." was between the two summaries → no longer a "human note after latest"
        assert "Added more context." not in result

    def test_summary_comment_itself_excluded_from_human_notes(self):
        """A summary after another summary should not appear in the human notes block."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("summary", "ai@taskit", "AI Summary", "First summary."),
            # Another summary after — this is the 'latest'; previous is before it
        ]
        result = orch._build_self_context("task-1")
        # Only one summary: it becomes the context block itself
        assert "First summary." in result
        assert "Human notes" not in result

    def test_get_comments_exception_returns_empty(self):
        """If get_comments raises, returns empty string gracefully."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.side_effect = RuntimeError("connection error")
        assert orch._build_self_context("task-1") == ""

    def test_works_with_object_style_comments(self):
        """Comments as objects (not dicts) are handled correctly."""
        orch = _make_orchestrator()

        class FakeComment:
            def __init__(self, comment_type, author_email, author_label, content):
                self.comment_type = comment_type
                self.author_email = author_email
                self.author_label = author_label
                self.content = content

        orch.task_mgr.get_comments.return_value = [
            FakeComment("summary", "ai@taskit", "AI Summary", "Object-style summary."),
            FakeComment("status_update", "alice@example.com", "Alice", "Human note after."),
        ]
        result = orch._build_self_context("task-1")
        assert "Object-style summary." in result
        assert "Human note after." in result
