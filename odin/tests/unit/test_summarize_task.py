"""Unit tests for Orchestrator.summarize_task().

All tests use a minimal stub orchestrator — no real backends or I/O.
The harness is mocked to return controlled output.

Coverage:
- No comments → returns failure with "No summarizable comments".
- Trace/debug comments filtered out, only summarizable comments used.
- Successful summarize posts comment with comment_type="summary".
- Summary text has ODIN-STATUS envelope stripped.
- summarize_in_progress metadata flag cleared after success.
- summarize_in_progress metadata flag cleared after failure.
- Harness exception handled gracefully.
- Prior summary comments excluded from comment lines in prompt.
- Latest prior summary included as separate context section.
- Only post-summary comments included when prior summary exists.
- First-time summarize (no prior summaries) includes all comments.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odin.config import OdinConfig
from odin.models import AgentConfig, CostTier, TaskResult
from odin.orchestrator import Orchestrator
from odin.taskit.models import Task, TaskStatus


def _make_orchestrator(comments=None, task=None):
    """Create a minimal Orchestrator with mocked internals."""
    cfg = OdinConfig(
        base_agent="claude",
        board_backend="local",
        agents={
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["reasoning", "coding"],
                cost_tier=CostTier.HIGH,
                default_model="claude-sonnet-4-5",
            ),
        },
    )
    orch = object.__new__(Orchestrator)
    orch.config = cfg
    orch.task_mgr = MagicMock()
    orch.spec_store = MagicMock()
    orch._log = MagicMock()

    # Default task
    if task is None:
        task = Task(
            id="42",
            title="Fix login bug",
            description="Fix the login flow",
            status=TaskStatus.REVIEW,
            assigned_agent="claude",
            metadata={},
        )

    orch.task_mgr.resolve_task_id.return_value = task.id
    orch.task_mgr.get_task.return_value = task
    orch.task_mgr.get_comments.return_value = comments or []
    orch.spec_store.load.return_value = None

    return orch


def _comment(comment_type, author_email, content, attachments=None, created_at=None):
    return {
        "comment_type": comment_type,
        "author_email": author_email,
        "author_label": author_email.split("@")[0],
        "content": content,
        "attachments": attachments or [],
        "created_at": created_at or "2026-02-23T14:13:00Z",
    }


class TestSummarizeTask:
    """Orchestrator.summarize_task() unit tests."""

    def test_no_comments_returns_failure(self):
        orch = _make_orchestrator(comments=[])
        result = asyncio.run(orch.summarize_task("42"))
        assert result["success"] is False
        assert "No summarizable" in result["error"]

    def test_only_trace_comments_returns_failure(self):
        orch = _make_orchestrator(comments=[
            _comment("status_update", "odin@harness.kit", "trace data",
                     attachments=["trace:execution_jsonl"]),
            _comment("status_update", "odin@harness.kit", "debug data",
                     attachments=["debug:effective_input"]),
        ])
        result = asyncio.run(orch.summarize_task("42"))
        assert result["success"] is False
        assert "No summarizable" in result["error"]

    @patch("odin.orchestrator.get_harness")
    def test_successful_summarize_posts_comment(self, mock_get_harness):
        comments = [
            _comment("status_update", "alice@example.com", "Tried fixing auth module."),
            _comment("status_update", "claude@odin.agent", "Completed the fix."),
        ]
        orch = _make_orchestrator(comments=comments)

        # Mock harness
        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True,
            output="The task attempted to fix the auth module. The fix was completed successfully.",
            duration_ms=1500,
            agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        result = asyncio.run(orch.summarize_task("42"))

        assert result["success"] is True
        assert "auth module" in result["summary"]

        # Verify add_comment was called with comment_type="summary"
        orch.task_mgr.add_comment.assert_called_once()
        call_kwargs = orch.task_mgr.add_comment.call_args
        assert call_kwargs[1]["comment_type"] == "summary"
        assert call_kwargs[1]["task_id"] == "42"
        assert "auth module" in call_kwargs[1]["content"]

    @patch("odin.orchestrator.get_harness")
    def test_strips_odin_status_envelope(self, mock_get_harness):
        """ODIN-STATUS envelope is stripped from the summary output."""
        comments = [
            _comment("status_update", "alice@example.com", "Did some work."),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        output_with_envelope = (
            "The task completed successfully.\n\n"
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            "Summarized."
        )
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output=output_with_envelope, duration_ms=1000, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        result = asyncio.run(orch.summarize_task("42"))

        assert result["success"] is True
        # The envelope should be stripped; the actual summary text used
        assert "ODIN-STATUS" not in result["summary"]
        assert "The task completed successfully." in result["summary"]

    @patch("odin.orchestrator.get_harness")
    def test_clears_metadata_flag_on_success(self, mock_get_harness):
        task = Task(
            id="42", title="Test", description="",
            status=TaskStatus.REVIEW, assigned_agent="claude",
            metadata={"summarize_in_progress": True},
        )
        orch = _make_orchestrator(
            comments=[_comment("status_update", "a@b.com", "work")],
            task=task,
        )

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="Summary text.", duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        # update_task should have been called to clear the flag
        orch.task_mgr.update_task.assert_called()
        updated_task = orch.task_mgr.update_task.call_args[0][0]
        assert "summarize_in_progress" not in updated_task.metadata

    def test_clears_metadata_flag_on_no_comments(self):
        task = Task(
            id="42", title="Test", description="",
            status=TaskStatus.REVIEW, assigned_agent="claude",
            metadata={"summarize_in_progress": True},
        )
        orch = _make_orchestrator(comments=[], task=task)

        asyncio.run(orch.summarize_task("42"))

        orch.task_mgr.update_task.assert_called()
        updated_task = orch.task_mgr.update_task.call_args[0][0]
        assert "summarize_in_progress" not in updated_task.metadata

    @patch("odin.orchestrator.get_harness")
    def test_harness_exception_handled(self, mock_get_harness):
        comments = [
            _comment("status_update", "a@b.com", "Some work."),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(side_effect=RuntimeError("harness crashed"))
        mock_get_harness.return_value = mock_harness

        result = asyncio.run(orch.summarize_task("42"))

        assert result["success"] is False
        assert "harness crashed" in result["error"]

    @patch("odin.orchestrator.get_harness")
    def test_filters_trace_comments_from_prompt(self, mock_get_harness):
        """Trace/debug comments should not appear in the prompt sent to the harness."""
        comments = [
            _comment("status_update", "alice@example.com", "Real work done."),
            _comment("status_update", "odin@harness.kit", "trace jsonl",
                     attachments=["trace:execution_jsonl"]),
            _comment("status_update", "odin@harness.kit", "debug data",
                     attachments=["debug:effective_input"]),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="Summary.", duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        # Check the prompt passed to execute
        call_args = mock_harness.execute.call_args
        prompt = call_args[0][0]
        assert "Real work done." in prompt
        assert "trace jsonl" not in prompt
        assert "debug data" not in prompt

    @patch("odin.orchestrator.get_harness")
    def test_prompt_requests_structured_markdown(self, mock_get_harness):
        """Prompt instructs LLM to produce structured markdown with sections."""
        comments = [
            _comment("status_update", "alice@example.com", "Started work.",
                     created_at="2026-02-23T14:13:00Z"),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="## Task Summary\n\n### Outcome\nDone.",
            duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        prompt = mock_harness.execute.call_args[0][0]
        # Must request structured sections
        assert "## Task Summary" in prompt
        assert "### Execution History" in prompt
        assert "### Key Events" in prompt
        assert "### Outcome" in prompt
        # Must include timestamp in comment lines
        assert "2026-02-23T14:13" in prompt

    @patch("odin.orchestrator.get_harness")
    def test_prompt_includes_task_status_and_agent(self, mock_get_harness):
        """Prompt includes task status and assigned agent for context."""
        comments = [
            _comment("status_update", "a@b.com", "work"),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="Summary.", duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        prompt = mock_harness.execute.call_args[0][0]
        assert "Status: review" in prompt
        assert "Assigned agent: claude" in prompt

    def test_task_not_found_raises(self):
        orch = _make_orchestrator()
        orch.task_mgr.resolve_task_id.return_value = "999"
        orch.task_mgr.get_task.return_value = None

        with pytest.raises(RuntimeError, match="Task not found"):
            asyncio.run(orch.summarize_task("999"))

    @patch("odin.orchestrator.get_harness")
    def test_excludes_prior_summaries_from_comment_lines(self, mock_get_harness):
        """Summary-type comments must not appear in the comment/activity section of the prompt."""
        comments = [
            _comment("status_update", "alice@example.com", "Started work.",
                     created_at="2026-02-23T14:00:00Z"),
            _comment("summary", "odin@harness.kit", "## Task Summary\n### Outcome\nOld summary.",
                     created_at="2026-02-23T14:30:00Z"),
            _comment("status_update", "alice@example.com", "More work done.",
                     created_at="2026-02-23T15:00:00Z"),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="New summary.", duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        prompt = mock_harness.execute.call_args[0][0]
        # The summary comment must NOT appear as a regular comment line
        assert "[summary]" not in prompt

    @patch("odin.orchestrator.get_harness")
    def test_includes_prior_summary_as_context(self, mock_get_harness):
        """When a prior summary exists, it appears in a dedicated 'Prior summary' section."""
        prior_summary_content = "## Task Summary\n### Outcome\nPrevious work summarized."
        comments = [
            _comment("status_update", "alice@example.com", "Initial work.",
                     created_at="2026-02-23T14:00:00Z"),
            _comment("summary", "odin@harness.kit", prior_summary_content,
                     created_at="2026-02-23T14:30:00Z"),
            _comment("status_update", "alice@example.com", "Follow-up work.",
                     created_at="2026-02-23T15:00:00Z"),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="Updated summary.", duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        prompt = mock_harness.execute.call_args[0][0]
        assert "Prior summary" in prompt
        assert prior_summary_content in prompt
        assert "do NOT repeat verbatim" in prompt.lower() or "Do NOT copy or repeat" in prompt

    @patch("odin.orchestrator.get_harness")
    def test_only_post_summary_comments_included(self, mock_get_harness):
        """When a prior summary exists, only comments after it appear in the activity section."""
        comments = [
            _comment("status_update", "alice@example.com", "Old work before summary.",
                     created_at="2026-02-23T14:00:00Z"),
            _comment("summary", "odin@harness.kit", "## Task Summary\nOld summary.",
                     created_at="2026-02-23T14:30:00Z"),
            _comment("status_update", "alice@example.com", "New work after summary.",
                     created_at="2026-02-23T15:00:00Z"),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="Fresh summary.", duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        prompt = mock_harness.execute.call_args[0][0]
        # Post-summary comment must be present
        assert "New work after summary." in prompt
        # Pre-summary comment must NOT be in the activity/comment section
        assert "Old work before summary." not in prompt

    @patch("odin.orchestrator.get_harness")
    def test_no_prior_summary_includes_all_comments(self, mock_get_harness):
        """First-time summarize (no prior summaries) includes all comments in the prompt."""
        comments = [
            _comment("status_update", "alice@example.com", "First action.",
                     created_at="2026-02-23T14:00:00Z"),
            _comment("agent", "claude@odin.agent", "Agent did work.",
                     created_at="2026-02-23T14:30:00Z"),
            _comment("status_update", "alice@example.com", "Second action.",
                     created_at="2026-02-23T15:00:00Z"),
        ]
        orch = _make_orchestrator(comments=comments)

        mock_harness = MagicMock()
        mock_harness.execute = AsyncMock(return_value=TaskResult(
            success=True, output="Complete summary.", duration_ms=500, agent="claude",
        ))
        mock_get_harness.return_value = mock_harness

        asyncio.run(orch.summarize_task("42"))

        prompt = mock_harness.execute.call_args[0][0]
        assert "First action." in prompt
        assert "Agent did work." in prompt
        assert "Second action." in prompt
        # No "Prior summary" section should exist
        assert "Prior summary" not in prompt
        # Should use "Comment history" header
        assert "Comment history" in prompt
