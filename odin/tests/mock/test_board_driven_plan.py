"""Tests for board-driven planning mode in the orchestrator.

Tags: [mock] — no real LLM calls, no real HTTP.

Verifies:
- plan(board_driven=True) posts gate questions to spec comments and returns early
- board_resume_plan() loads the spec, appends the reply, and runs task breakdown
- The two-phase split is clean: Phase 1 exits before task breakdown, Phase 2
  skips the gate and proceeds directly to decomposition + task creation
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from odin.models import AgentConfig, CostTier, ModelRoute, OdinConfig, TaskResult
from odin.orchestrator import Orchestrator


@pytest.fixture
def config_with_mock(odin_dirs):
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents={
            "mock": AgentConfig(
                cli_command="mock",
                capabilities=["coding", "planning"],
                cost_tier=CostTier.LOW,
                enabled=True,
            ),
        },
        model_routing=[
            ModelRoute(agent="mock", model="mock-model"),
        ],
    )


SIMPLE_PLAN = [{
    "id": "task_1",
    "title": "Do it",
    "description": "Do the thing",
    "required_capabilities": ["coding"],
    "suggested_agent": "mock",
    "complexity": "low",
    "depends_on": [],
}]


def _mock_result():
    return TaskResult(success=True, output="", duration_ms=100, agent="mock")


class TestBoardDrivenPlan:
    """Phase 1: plan(board_driven=True) posts questions and exits early."""

    def test_board_driven_posts_questions_and_returns_empty(
        self, odin_dirs, config_with_mock,
    ):
        """Board-driven mode posts gate questions and returns (sid, [])."""
        orch = Orchestrator(config=config_with_mock)

        # Mock the backend so _post_board_gate can work
        mock_backend = MagicMock()
        mock_backend.get_spec_odin_id_by_pk.return_value = None
        orch._spec_backend = mock_backend

        spec_text = "# Board Plan\nBuild a REST API."

        posted_comments = []

        def mock_post(spec_pk, content, comment_type="status_update",
                      author_email="", author_label="", attachment_ids=None):
            posted_comments.append({
                "spec_pk": spec_pk,
                "content": content,
                "comment_type": comment_type,
                "attachment_ids": attachment_ids,
            })
            return {"id": len(posted_comments)}

        mock_backend.post_spec_comment_by_pk = mock_post
        mock_backend.upload_spec_attachment = MagicMock(return_value=[{"id": 99}])
        mock_backend.update_spec_metadata_by_pk = MagicMock()

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                Path(out_path).write_text(json.dumps({
                    "questions": ["Which framework?", "Any auth?"],
                    "summary": "A REST API service.",
                }))
                preview_path = Path(out_path).with_name(
                    Path(out_path).name.replace("clarification_", "preview_")
                ).with_suffix(".html")
                preview_path.write_text("<html>preview</html>")
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        sid, tasks = asyncio.run(orch.plan(
                            spec_text, quick=True, gate=True,
                            gate_callback=None,
                            board_driven=True, board_spec_pk=42,
                        ))

        # Should return early with no tasks
        assert len(tasks) == 0
        assert sid is not None

        # Should have posted comments: summary + questions + preview-as-attachment
        comment_types = [c["comment_type"] for c in posted_comments]
        assert "planning" in comment_types  # summary
        assert "question" in comment_types  # questions

        # Preview HTML should be uploaded as a spec attachment (not embedded
        # in a comment body) and linked via attachment_ids.
        mock_backend.upload_spec_attachment.assert_called_once()
        upload_args = mock_backend.upload_spec_attachment.call_args
        assert upload_args[0][0] == 42  # spec_pk
        assert str(upload_args[0][1]).endswith(".html")  # preview path
        att_comments = [c for c in posted_comments if c.get("attachment_ids")]
        assert len(att_comments) == 1
        assert att_comments[0]["attachment_ids"] == [99]

        # Should have updated spec metadata to awaiting_answers
        mock_backend.update_spec_metadata_by_pk.assert_called_with(
            42, {"board_plan_status": "awaiting_answers"},
        )

        # Should NOT have called _create_tasks_from_plan
        # (decompose was only called for clarification, not for task breakdown)
        # Verify by checking that the plan_{sid}.json file was NOT written
        plans_dir = Path(config_with_mock.task_storage).parent / "plans"
        plan_files = list(plans_dir.glob(f"plan_{sid}.json"))
        assert len(plan_files) == 0  # No task breakdown happened

    def test_board_driven_without_backend_logs_warning(
        self, odin_dirs, config_with_mock,
    ):
        """When no backend is configured, _post_board_gate logs a warning
        but doesn't crash."""
        orch = Orchestrator(config=config_with_mock)
        # _spec_backend is None for board_backend="local"

        spec_text = "# Test\nBuild something."

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                Path(out_path).write_text(json.dumps({
                    "questions": ["Q1"],
                    "summary": "Summary.",
                }))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        sid, tasks = asyncio.run(orch.plan(
                            spec_text, quick=True, gate=True,
                            gate_callback=None,
                            board_driven=True, board_spec_pk=None,
                        ))

        # Still returns (sid, []) — the gate ran but couldn't post
        assert len(tasks) == 0

    def test_board_driven_no_questions_still_posts(
        self, odin_dirs, config_with_mock,
    ):
        """When the clarification has no questions, still posts a
        'no questions needed' comment."""
        orch = Orchestrator(config=config_with_mock)
        mock_backend = MagicMock()
        mock_backend.get_spec_odin_id_by_pk.return_value = None
        orch._spec_backend = mock_backend

        posted_comments = []
        def _mock_post(spec_pk, content="", comment_type="status_update",
                      author_email="", author_label=""):
            posted_comments.append({
                "content": content,
                "comment_type": comment_type,
            })
            return {"id": len(posted_comments)}
        mock_backend.post_spec_comment_by_pk = _mock_post
        mock_backend.update_spec_metadata_by_pk = MagicMock()

        spec_text = "# Clear Spec\nEverything is obvious."

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                Path(out_path).write_text(json.dumps({
                    "questions": [],
                    "summary": "Clear spec.",
                }))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        asyncio.run(orch.plan(
                            spec_text, quick=True, gate=True,
                            gate_callback=None,
                            board_driven=True, board_spec_pk=1,
                        ))

        # Should have posted at least 2 comments (summary + "no questions")
        question_comments = [c for c in posted_comments if c.get("comment_type") == "question"]
        assert len(question_comments) == 1
        assert "no clarification" in question_comments[0]["content"].lower()


class TestBoardResumePlan:
    """Phase 2: board_resume_plan() does task breakdown after reply."""

    def test_resume_reads_reply_and_creates_tasks(
        self, odin_dirs, config_with_mock,
    ):
        """board_resume_plan loads the spec, appends the reply,
        and creates tasks."""
        orch = Orchestrator(config=config_with_mock)
        mock_backend = MagicMock()
        mock_backend.get_spec_odin_id_by_pk.return_value = None
        orch._spec_backend = mock_backend

        spec_text = "# Resume Test\nBuild a CLI tool."

        # First, run Phase 1 to create the spec archive
        async def mock_decompose_clar(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                Path(out_path).write_text(json.dumps({
                    "questions": ["Which language?"],
                    "summary": "A CLI tool.",
                }))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose_clar):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        sid, tasks = asyncio.run(orch.plan(
                            spec_text, quick=True, gate=True,
                            gate_callback=None,
                            board_driven=True, board_spec_pk=1,
                        ))

        assert len(tasks) == 0  # Phase 1: no tasks yet

        # Now resume with a reply
        plans_dir = Path(config_with_mock.task_storage).parent / "plans"
        plan_path = plans_dir / f"plan_{sid}.json"

        async def mock_decompose_resume(prompt, wd, **kwargs):
            # The reply text should be in the prompt
            assert "Use Python" in prompt
            plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        mock_tasks_created = []

        async def mock_create_tasks(sub_tasks, spec_id, *args, **kwargs):
            from odin.taskit.models import Task, TaskStatus
            for st in sub_tasks:
                mock_tasks_created.append(
                    Task(id="mock-1", title=st["title"],
                         description=st.get("description", ""),
                         status=TaskStatus.TODO)
                )
            return mock_tasks_created

        with patch.object(orch, "_decompose", side_effect=mock_decompose_resume):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        with patch.object(orch, "_create_tasks_from_plan", side_effect=mock_create_tasks):
                            with patch.object(orch, "_record_planning_trace"):
                                with patch.object(orch, "_mark_planning_complete"):
                                    resume_sid, resume_tasks = asyncio.run(
                                        orch.board_resume_plan(
                                            spec_id=sid,
                                            reply_text="Use Python 3.12",
                                            board_spec_pk=1,
                                        )
                                    )

        assert resume_sid == sid
        assert len(resume_tasks) == 1
        assert resume_tasks[0].title == "Do it"

        # Should have updated spec metadata to complete
        mock_backend.update_spec_metadata_by_pk.assert_called_with(
            1, {"board_plan_status": "complete"},
        )

    def test_resume_without_backend_still_creates_tasks(
        self, odin_dirs, config_with_mock,
    ):
        """board_resume_plan works even without a backend (local mode)."""
        orch = Orchestrator(config=config_with_mock)
        # _spec_backend is None for local mode

        spec_text = "# Local Resume\nBuild something simple."

        # Run Phase 1 to create the spec archive
        async def mock_decompose_clar(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                Path(out_path).write_text(json.dumps({
                    "questions": [],
                    "summary": "Simple.",
                }))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose_clar):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        sid, _ = asyncio.run(orch.plan(
                            spec_text, quick=True, gate=True,
                            gate_callback=None,
                            board_driven=True, board_spec_pk=None,
                        ))

        # Resume
        plans_dir = Path(config_with_mock.task_storage).parent / "plans"
        plan_path = plans_dir / f"plan_{sid}.json"

        async def mock_decompose_resume(prompt, wd, **kwargs):
            plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        async def mock_create_tasks(sub_tasks, spec_id, *args, **kwargs):
            from odin.taskit.models import Task, TaskStatus
            return [Task(id="t1", title="Do it", description="Do the thing", status=TaskStatus.TODO)]

        with patch.object(orch, "_decompose", side_effect=mock_decompose_resume):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        with patch.object(orch, "_create_tasks_from_plan", side_effect=mock_create_tasks):
                            with patch.object(orch, "_record_planning_trace"):
                                with patch.object(orch, "_mark_planning_complete"):
                                    resume_sid, resume_tasks = asyncio.run(
                                        orch.board_resume_plan(
                                            spec_id=sid,
                                            reply_text="Go ahead",
                                        )
                                    )

        assert len(resume_tasks) == 1

    def test_resume_missing_spec_archive_raises(
        self, odin_dirs, config_with_mock,
    ):
        """board_resume_plan raises if the spec archive doesn't exist."""
        orch = Orchestrator(config=config_with_mock)

        with pytest.raises(RuntimeError, match="not found for board resume"):
            asyncio.run(orch.board_resume_plan(
                spec_id="sp_nonexistent",
                reply_text="reply",
            ))


class TestBoardDrivenSpecLinking:
    """Task 345 round 4: Phase 1 must reuse the requested board spec's
    odin_id, not mint a fresh one — otherwise the spec archive (and every
    task created against it in Phase 2) ends up linked to a brand-new spec
    row instead of the spec the operator actually asked to plan."""

    def _run_phase1(self, orch, spec_text, board_spec_pk):
        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                Path(out_path).write_text(json.dumps({
                    "questions": ["Q1"],
                    "summary": "Summary.",
                }))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_fetch_quota", return_value={}):
                with patch.object(orch, "_fetch_routing_config", return_value=None):
                    with patch.object(orch, "_build_available_agents", return_value=[]):
                        return asyncio.run(orch.plan(
                            spec_text, quick=True, gate=True,
                            gate_callback=None,
                            board_driven=True, board_spec_pk=board_spec_pk,
                        ))

    def test_board_driven_reuses_existing_spec_odin_id(
        self, odin_dirs, config_with_mock,
    ):
        """When the requested board spec already has an odin_id (e.g. set
        by the UI when the spec was created), Phase 1 must plan under THAT
        id instead of generating a new one."""
        orch = Orchestrator(config=config_with_mock)
        mock_backend = MagicMock()
        mock_backend.get_spec_odin_id_by_pk.return_value = "ui-planning-existing-8"
        mock_backend.post_spec_comment_by_pk.return_value = {"id": 1}
        orch._spec_backend = mock_backend

        sid, tasks = self._run_phase1(
            orch, "# Reuse Test\nBuild a widget.", board_spec_pk=8,
        )

        assert tasks == []
        mock_backend.get_spec_odin_id_by_pk.assert_called_once_with(8)
        # The spec archive must be saved under the EXISTING odin_id — not a
        # freshly minted "sp_<timestamp>" id — so Phase 2 (and every task it
        # creates) resolves back to spec pk=8, the spec that was requested.
        assert sid == "ui-planning-existing-8"
        saved_ids = [call.args[0].id for call in mock_backend.save_spec.call_args_list]
        assert saved_ids
        assert all(saved_id == "ui-planning-existing-8" for saved_id in saved_ids)

    def test_board_driven_generates_id_when_spec_has_none(
        self, odin_dirs, config_with_mock,
    ):
        """Falls back to a freshly generated id when the board spec has no
        resolvable odin_id yet (e.g. lookup fails or spec is brand new)."""
        orch = Orchestrator(config=config_with_mock)
        mock_backend = MagicMock()
        mock_backend.get_spec_odin_id_by_pk.return_value = None
        mock_backend.post_spec_comment_by_pk.return_value = {"id": 1}
        orch._spec_backend = mock_backend

        sid, _ = self._run_phase1(
            orch, "# Fresh Test\nBuild a gadget.", board_spec_pk=99,
        )

        mock_backend.get_spec_odin_id_by_pk.assert_called_once_with(99)
        assert sid.startswith("sp_")
