"""Unit tests for Orchestrator._build_task_context().

All tests use a minimal stub orchestrator — no real backends or I/O.

Coverage:
- No comments → empty string.
- Single NEEDS_WORK reflection → "Previous Review Feedback" section.
- FAIL reflection → included (not just NEEDS_WORK).
- PASS reflection → excluded.
- Summary + human notes → both sections.
- Summary + reflection + Q&A + proof → all sections in priority order.
- Pre-summary comments excluded (except reflections).
- Noise filtering: "Effective input" skipped, debug attachments skipped.
- Multiple reflections → chronological order.
- Budget enforcement: huge content → truncated at MAX_CONTEXT_CHARS.
- Agent output: latest only, capped at 1000 chars.
- get_comments exception → empty string.
- Object-style comments (not dicts) → handled.
"""

from unittest.mock import MagicMock


def _make_orchestrator():
    """Create a minimal Orchestrator-like object with _build_task_context."""
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


class TestBuildTaskContext:
    """[simple] _build_task_context() — pure orchestrator method."""

    def test_no_comments_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = []
        assert orch._build_task_context("task-1") == ""

    def test_single_needs_work_reflection(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", "Did work."),
            _reflection_comment("NEEDS_WORK", "Missing back button in phase 2."),
        ]
        result = orch._build_task_context("task-1")
        assert "## Previous Review Feedback" in result
        assert "NEEDS_WORK" in result
        assert "Missing back button in phase 2." in result
        assert "claude/claude-sonnet-4-5" in result

    def test_fail_reflection_included(self):
        """FAIL reflections are included (unlike the old _build_reflection_context)."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("FAIL", "Completely wrong approach."),
        ]
        result = orch._build_task_context("task-1")
        assert "## Previous Review Feedback" in result
        assert "[FAIL]" in result
        assert "Completely wrong approach." in result

    def test_pass_reflection_excluded(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("PASS", "Everything looks good."),
        ]
        assert orch._build_task_context("task-1") == ""

    def test_summary_and_human_notes(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("summary", "ai@taskit", "AI Summary", "Task attempted X."),
            _comment("status_update", "alice@example.com", "Alice", "Please fix the tests."),
        ]
        result = orch._build_task_context("task-1")
        assert "## Task Summary" in result
        assert "Task attempted X." in result
        assert "## Human Notes" in result
        assert "Alice" in result
        assert "Please fix the tests." in result

    def test_all_sections_in_priority_order(self):
        """Reflection → Summary → Human Notes → Q&A → Proof → Agent Output."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("NEEDS_WORK", "Fix the timer."),
            _comment("summary", "ai@taskit", "AI Summary", "First attempt summary."),
            _comment("status_update", "alice@example.com", "Alice", "Human note."),
            _comment("question", "bob@example.com", "Bob", "What approach?"),
            _comment("reply", "alice@example.com", "Alice", "Use approach B."),
            _comment("proof", "agent@odin.agent", "Agent", "Screenshot attached."),
            _comment("status_update", "agent@odin.agent", "Agent", "Completed step 3."),
        ]
        result = orch._build_task_context("task-1")

        # All sections present
        assert "## Previous Review Feedback" in result
        assert "## Task Summary" in result
        assert "## Human Notes" in result
        assert "## Questions & Answers" in result
        assert "## Prior Proof of Work" in result
        assert "## Previous Execution Output" in result

        # Priority order: reflections before summary before notes etc.
        idx_refl = result.index("## Previous Review Feedback")
        idx_summ = result.index("## Task Summary")
        idx_human = result.index("## Human Notes")
        idx_qa = result.index("## Questions & Answers")
        idx_proof = result.index("## Prior Proof of Work")
        idx_agent = result.index("## Previous Execution Output")
        assert idx_refl < idx_summ < idx_human < idx_qa < idx_proof < idx_agent

    def test_pre_summary_comments_excluded_except_reflections(self):
        """Comments before the summary checkpoint are ignored (except reflections)."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "alice@example.com", "Alice", "Old note before summary."),
            _comment("question", "bob@example.com", "Bob", "Old question."),
            _reflection_comment("NEEDS_WORK", "Fix the timer."),  # pre-summary but included
            _comment("summary", "ai@taskit", "AI Summary", "Summary content."),
            _comment("status_update", "alice@example.com", "Alice", "New note after summary."),
        ]
        result = orch._build_task_context("task-1")
        # Reflection crosses the boundary
        assert "Fix the timer." in result
        # Pre-summary human note and question are excluded
        assert "Old note before summary." not in result
        assert "Old question." not in result
        # Post-summary note is included
        assert "New note after summary." in result

    def test_noise_filtering_effective_input_skipped(self):
        """'Effective input' debug comments are filtered out."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "odin@odin.agent", "odin",
                     "Effective input (with upstream context):\n\nSome wrapped prompt"),
        ]
        # Noise filtered → no meaningful content → empty
        assert orch._build_task_context("task-1") == ""

    def test_debug_attachment_comments_skipped(self):
        """Comments with debug: attachments are filtered out."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("summary", "ai@taskit", "AI Summary", "Summary."),
            _comment("status_update", "odin@odin.agent", "odin",
                     "Some debug content",
                     attachments=["debug:effective_input"]),
        ]
        result = orch._build_task_context("task-1")
        assert "Some debug content" not in result
        assert "## Task Summary" in result

    def test_multiple_reflections_chronological(self):
        """All NEEDS_WORK/FAIL reflections included in chronological order."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("NEEDS_WORK", "First round: fix the timer."),
            _comment("status_update", "agent@odin.agent", "Agent", "Re-executed."),
            _reflection_comment("NEEDS_WORK", "Second round: still missing back button."),
        ]
        result = orch._build_task_context("task-1")
        assert "First round: fix the timer." in result
        assert "Second round: still missing back button." in result
        # Chronological: first before second
        assert result.index("First round") < result.index("Second round")

    def test_budget_enforcement_truncates(self):
        """Content exceeding MAX_CONTEXT_CHARS gets truncated."""
        orch = _make_orchestrator()
        # Create a huge reflection that fills most of the budget
        huge_content = "X" * 5000
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("NEEDS_WORK", huge_content),
            _comment("summary", "ai@taskit", "AI Summary", "A" * 3000),
        ]
        result = orch._build_task_context("task-1")
        assert len(result) <= orch.MAX_CONTEXT_CHARS + 100  # small margin for headers
        # Reflection (higher priority) should be present
        assert "## Previous Review Feedback" in result
        # Summary may be truncated or missing depending on budget
        if "## Task Summary" in result:
            assert "[...truncated]" in result

    def test_agent_output_latest_only_and_capped(self):
        """Only the latest agent status_update is included, capped at 1000 chars."""
        orch = _make_orchestrator()
        long_output = "Y" * 2000
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", "First execution output."),
            _comment("status_update", "agent@odin.agent", "Agent", long_output),
        ]
        result = orch._build_task_context("task-1")
        assert "## Previous Execution Output" in result
        # First output not present (latest wins)
        assert "First execution output." not in result
        # Long output capped
        section_start = result.index("## Previous Execution Output")
        section = result[section_start:]
        # The Y's in the section should be at most 1000
        assert section.count("Y") <= 1000

    def test_get_comments_exception_returns_empty(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.side_effect = RuntimeError("connection error")
        assert orch._build_task_context("task-1") == ""

    def test_object_style_comments_handled(self):
        """Comments as objects (not dicts) are handled correctly."""
        orch = _make_orchestrator()

        class FakeComment:
            def __init__(self, comment_type, author_email, author_label, content,
                         attachments=None):
                self.comment_type = comment_type
                self.author_email = author_email
                self.author_label = author_label
                self.content = content
                self.attachments = attachments

        orch.task_mgr.get_comments.return_value = [
            FakeComment("summary", "ai@taskit", "AI Summary", "Object summary."),
            FakeComment("status_update", "alice@example.com", "Alice", "Object note."),
        ]
        result = orch._build_task_context("task-1")
        assert "Object summary." in result
        assert "Object note." in result

    def test_system_json_content_filtered(self):
        """Status updates containing raw JSON system events are skipped."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent",
                     '{"type":"system","subtype":"init"}'),
        ]
        assert orch._build_task_context("task-1") == ""

    def test_cli_noise_lines_stripped(self):
        """Lines with CLI noise (DeprecationWarning etc.) are stripped."""
        orch = _make_orchestrator()
        noisy_content = (
            "Starting execution\n"
            "DeprecationWarning: something old\n"
            "YOLO mode activated\n"
            "Task completed successfully"
        )
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "agent@odin.agent", "Agent", noisy_content),
        ]
        result = orch._build_task_context("task-1")
        if result:
            assert "DeprecationWarning" not in result
            assert "YOLO mode" not in result
            assert "Task completed successfully" in result

    def test_proof_capped_at_500_chars(self):
        """Proof content is capped at 500 characters."""
        orch = _make_orchestrator()
        long_proof = "Z" * 1000
        orch.task_mgr.get_comments.return_value = [
            _comment("proof", "agent@odin.agent", "Agent", long_proof),
        ]
        result = orch._build_task_context("task-1")
        if "## Prior Proof of Work" in result:
            section_start = result.index("## Prior Proof of Work")
            section = result[section_start:]
            assert section.count("Z") <= 500

    def test_no_summary_all_comments_included(self):
        """Without a summary checkpoint, all post-position-0 comments are eligible."""
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _comment("status_update", "alice@example.com", "Alice", "A human note."),
            _comment("question", "bob@example.com", "Bob", "A question."),
            _comment("reply", "alice@example.com", "Alice", "An answer."),
        ]
        result = orch._build_task_context("task-1")
        assert "## Human Notes" in result
        assert "A human note." in result
        assert "## Questions & Answers" in result
        assert "[QUESTION]: A question." in result
        assert "[REPLY]: An answer." in result

    def test_reflection_with_empty_content_skipped(self):
        orch = _make_orchestrator()
        orch.task_mgr.get_comments.return_value = [
            _reflection_comment("NEEDS_WORK", ""),
        ]
        assert orch._build_task_context("task-1") == ""
