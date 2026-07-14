"""Tests for size-bucketed reflection reviewer selection.

W3.18 — Reflection runs on one fixed model regardless of task size. Scale
reviewer choice by review context size: small diffs/contexts → cheaper
reviewer (e.g. claude-haiku-4-5); large/complex → current sonnet default;
expose as board-level config with the current behavior as fallback.

The selection happens server-side when the auto-reflection is triggered
(task → REVIEW). The selected reviewer is stored on the ReflectionReport
so the report itself shows which reviewer ran and why.

Acceptance:
- Selection logic tested across size buckets (small / medium / large).
- Board config honored (set → size-bucketed, unset → fallback default).
- Default behavior unchanged when board.reflection_review_strategy is unset.
- selection_reason surfaces why a reviewer was picked (visible on report).
- Legacy board.reflection_model override still wins (operator-set override
  is sacred; size scaling is the new opt-in layer, not a replacement).
- Forced provider (env-var driven) still wins.
"""

from unittest.mock import patch

from django.utils import timezone

from .base import APITestCase
from tasks.models import (
    ReflectionReport, ReflectionStatus, TaskComment, TaskStatus, User, UserRole,
)


def _strongest_claude_model():
    """The claude model _ensure_agents() will pick under the new
    strongest-first fallback (highest output_price_per_1m_tokens per the
    live agent_models.json registry — not the `is_default` flag, which the
    old random-default path used but the new deterministic fallback does
    not consult)."""
    from tasks.pricing import get_pricing_table
    pricing = get_pricing_table()
    candidates = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8"]
    priced = [(pricing.get(m, {}).get("output_price_per_1m_tokens"), m) for m in candidates]
    priced = [(p, m) for p, m in priced if p is not None]
    priced.sort(reverse=True)
    return priced[0][1] if priced else "claude-sonnet-5"


def _ensure_agents(*, with_models=None, only=None):
    """Create agent users with available_models so selection can resolve.

    Default agents mirror what's in production:
    - claude@odin.agent: claude-haiku-4-5, claude-sonnet-5, claude-opus-4-8
    - gemini@odin.agent: gemini-2.5-pro
    - glm@odin.agent: zai-coding-plan/glm-5.2 (task #246)
    - minimax@odin.agent: minimax-coding-plan/MiniMax-M3 (task #246)

    Pass `only={"claude"}` to limit which agents exist in the test — useful
    when the test asserts on the specific reviewer picked (random selection
    in `_find_first_available_reviewer` would otherwise be flaky).
    """
    defaults = with_models or {
        "claude": [
            {"name": "claude-haiku-4-5", "is_default": False},
            {"name": "claude-sonnet-5", "is_default": True},
            {"name": "claude-opus-4-8", "is_default": False},
        ],
        "gemini": [
            {"name": "gemini-2.5-pro", "is_default": True},
        ],
        "glm": [
            {"name": "zai-coding-plan/glm-5.2", "is_default": True},
        ],
        "minimax": [
            {"name": "minimax-coding-plan/MiniMax-M3", "is_default": True},
        ],
    }
    if only:
        # Drop any agents not in the requested set so the random
        # `_find_first_available_reviewer` pick is deterministic.
        User.objects.filter(
            email__iendswith="@odin.agent",
        ).exclude(email__startswith=tuple(only)).delete()
    for name, models in defaults.items():
        if only and name not in only:
            continue
        User.objects.get_or_create(
            email=f"{name}@odin.agent",
            defaults={
                "name": name.title(),
                "role": UserRole.AGENT,
                "available_models": models,
            },
        )


def _set_full_output(task, content):
    """Helper: store agent text in task.metadata['full_output']."""
    md = dict(task.metadata or {})
    md["full_output"] = content
    task.metadata = md
    task.save(update_fields=["metadata"])


def _set_description(task, content):
    task.description = content
    task.save(update_fields=["description"])


def _add_comment(task, content, comment_type="status_update"):
    TaskComment.objects.create(
        task=task,
        author_email="claude+sonnet-4@odin.agent",
        content=content,
        comment_type=comment_type,
    )


class TestReviewerSelectionByContextSize(APITestCase):
    """select_reviewer_by_context_size() — size-bucket selection logic.

    The function picks (agent, model) based on:
    1. Forced provider (env-var) — wins unconditionally.
    2. Board.reflection_model — legacy operator override, wins unconditionally.
    3. Board.reflection_review_strategy — new opt-in; buckets context size
       into small/medium/large and looks up the named model.
    4. Default — current behavior (uses default reviewer agent's default model).

    Bucket thresholds: small < small_max < medium < large_min ≤ large.
    Defaults: small_max=8000, large_min=24000 (sensible mid-task heuristics).
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        _ensure_agents()

    # ── Default behavior (no board config) ──────────────────────────

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_no_strategy_uses_default_reviewer_for_small_task(self, mock_delay):
        """No strategy → current default behavior regardless of size."""
        # Restrict to claude-only so the random default-pick is deterministic.
        _ensure_agents(only={"claude"})
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "Tight one-liner.")
        _add_comment(task, "Done.", comment_type="status_update")
        self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        report = ReflectionReport.objects.get(task=task)
        # Default = claude + the strongest (highest-priced) available model.
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.reviewer_model, _strongest_claude_model())
        self.assertEqual(report.selection_reason, "default_strongest")
        mock_delay.assert_called_once_with(report.id)

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_no_strategy_uses_default_reviewer_for_large_task(self, mock_delay):
        """No strategy → large task still gets the default reviewer.

        Without an opt-in size-bucket strategy, ALL tasks get the same
        reviewer — preserving the legacy single-model behavior.
        """
        _ensure_agents(only={"claude"})
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "X" * 50_000)
        _add_comment(task, "Y" * 30_000, comment_type="status_update")
        _set_full_output(task, "Z" * 80_000)
        self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.reviewer_model, _strongest_claude_model())
        self.assertEqual(report.selection_reason, "default_strongest")

    # ── Size buckets with strategy set ──────────────────────────────

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_size_strategy_is_retired_and_ignored(self, mock_delay):
        """reflection_review_strategy is set in full — and ignored.

        Operator directive: the ordered reviewer walk is the ONE
        mechanism; size bucketing was retired with it. A board carrying a
        complete legacy strategy must select exactly as if it were unset.
        """
        self.board.reflection_review_strategy = {
            "thresholds": {"small_max": 8000, "large_min": 24000},
            "small": "claude-haiku-4-5",
            "medium": "claude-sonnet-5",
            "large": "claude-opus-4-8",
        }
        self.board.save(update_fields=["reflection_review_strategy"])

        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "Short.")

        self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.reviewer_model, _strongest_claude_model())
        self.assertEqual(report.selection_reason, "default_strongest")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_legacy_reflection_model_is_retired_and_ignored(self, mock_delay):
        """board.reflection_model is set — and ignored (retired override)."""
        self.board.reflection_model = "claude-haiku-4-5"
        self.board.save(update_fields=["reflection_model"])

        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "Short.")

        self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_model, _strongest_claude_model())
        self.assertEqual(report.selection_reason, "default_strongest")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_empty_strategy_dict_falls_back_to_default(self, mock_delay):
        """strategy = {} → use default reviewer (no size scaling)."""
        _ensure_agents(only={"claude"})
        self.board.reflection_review_strategy = {}
        self.board.save(update_fields=["reflection_review_strategy"])

        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "D" * 30_000)
        _add_comment(task, "C" * 30_000, comment_type="status_update")

        self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_model, _strongest_claude_model())
        self.assertEqual(report.selection_reason, "default_strongest")

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_missing_bucket_in_strategy_falls_back_to_default(self, mock_delay):
        """If the strategy doesn't define a bucket, fall back to default reviewer."""
        _ensure_agents(only={"claude"})
        self.board.reflection_review_strategy = {
            "thresholds": {"small_max": 8000, "large_min": 24000},
            "small": "claude-haiku-4-5",
            # medium missing — large falls back to default for medium-sized tasks
            "large": "claude-opus-4-8",
        }
        self.board.save(update_fields=["reflection_review_strategy"])

        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "D" * 5000)
        _add_comment(task, "C" * 5000, comment_type="status_update")
        _set_full_output(task, "O" * 3000)
        # Total ~13000 → medium bucket, but strategy has no "medium" → default

        self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_model, _strongest_claude_model())
        self.assertEqual(report.selection_reason, "default_strongest")


class TestReviewerSelectionThresholdDefaults(APITestCase):
    """When the strategy omits thresholds, sensible defaults apply.

    Defaults: small_max=8000, large_min=24000. A strategy of
    {"small": "x", "medium": "y", "large": "z"} with no thresholds
    section still works — the missing thresholds fall back to the
    documented defaults so an operator can configure a board with the
    minimum viable strategy.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        _ensure_agents()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_strategy_without_thresholds_is_ignored(self, mock_delay):
        """A bucket-only legacy strategy (no thresholds) is also ignored."""
        self.board.reflection_review_strategy = {"small": "claude-haiku-4-5"}
        self.board.save(update_fields=["reflection_review_strategy"])

        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "tiny")

        self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "admin@test.com"},
            format="json",
        )
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.selection_reason, "default_strongest")


class TestReviewerSelectionViaReflectEndpoint(APITestCase):
    """POST /tasks/:id/reflect/ — manual endpoint preserves legacy contract.

    The manual endpoint accepts explicit reviewer_agent/reviewer_model
    values. It is the operator-driven path — selection_reason distinguishes
    "caller_override" (the operator passed values) from "manual_default"
    (the serializer defaults were used). Size selection happens in
    _trigger_auto_reflection, not here, so the operator can still pin
    a specific reviewer for a specific task regardless of context size.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        _ensure_agents()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_manual_reflect_uses_serializer_defaults_without_caller_input(self, mock_delay):
        """No caller-supplied reviewer → derived from available agents +
        selection_reason=manual_default.

        When agent users exist, the default derives from available reviewers
        (the same logic the auto-reflection path uses) instead of a hardcoded
        claude/claude-opus-4-8 serializer default.  This prevents a
        single-provider board from crashing on a default that doesn't match
        any installed CLI.
        """
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        _set_description(task, "Tiny task.")

        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {"custom_prompt": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.selection_reason, "manual_default")
        # The derived reviewer is one of the available agents — not a
        # hardcoded provider default that may not exist on this board.
        agent_names = set(
            User.objects.filter(
                role=UserRole.AGENT, is_active=True,
            ).values_list("email", flat=True)
        )
        derived_agents = {
            e.split("@")[0].split("+")[0].lower() for e in agent_names
        }
        self.assertIn(
            report.reviewer_agent,
            derived_agents,
            "derived reviewer must be an available agent, not a hardcoded default",
        )

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_manual_reflect_explicit_caller_override(self, mock_delay):
        """Caller-supplied reviewer → selection_reason=caller_override."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)

        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {
                "reviewer_model": "claude-opus-4-8",
                "reviewer_agent": "claude",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_model, "claude-opus-4-8")
        self.assertEqual(report.selection_reason, "caller_override")


class TestContextSizeEstimation(APITestCase):
    """_estimate_task_context_size() — the size used to bucket.

    The estimate is computed from the task's description, comments,
    full_output (agent's execution transcript), and dependencies. The
    estimate doesn't need to be byte-exact with the assembled prompt;
    it just needs to bucket the task into the right range.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        _ensure_agents()

    def test_empty_task_context_size_is_zero(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        from tasks.views import _estimate_task_context_size
        size = _estimate_task_context_size(task)
        # description has default ""; no comments; no full_output; no deps
        self.assertEqual(size, 0)

    def test_description_counted(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_description(task, "X" * 500)
        from tasks.views import _estimate_task_context_size
        size = _estimate_task_context_size(task)
        self.assertEqual(size, 500)

    def test_comments_counted_aggregated(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _add_comment(task, "X" * 100, comment_type="status_update")
        _add_comment(task, "Y" * 200, comment_type="proof")
        from tasks.views import _estimate_task_context_size
        size = _estimate_task_context_size(task)
        self.assertEqual(size, 300)

    def test_full_output_counted(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        _set_full_output(task, "Z" * 1000)
        from tasks.views import _estimate_task_context_size
        size = _estimate_task_context_size(task)
        self.assertEqual(size, 1000)


class TestResolveReviewerForModelForNonAnthropicFamilies(APITestCase):
    """Bug #246: _resolve_reviewer_for_model must find glm/minimax agents.

    The hint table in _reflection_agent_hint_for_model only knows about
    gemini/claude/codex — zai-coding-plan/glm-* and minimax-coding-plan/* return
    no hint, so the search order stays on REFLECTION_PREFERRED_AGENTS and the
    loop never visits glm/minimax even though those agents advertise the model.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_resolves_zai_coding_plan_glm_5_2_to_glm_agent(self):
        _ensure_agents(only={"glm"})
        from tasks.views import _resolve_reviewer_for_model
        agent, model = _resolve_reviewer_for_model(
            "zai-coding-plan/glm-5.2", board=self.board,
        )
        self.assertEqual(agent, "glm")
        self.assertEqual(model, "zai-coding-plan/glm-5.2")

    def test_resolves_minimax_coding_plan_M3_to_minimax_agent(self):
        _ensure_agents(only={"minimax"})
        from tasks.views import _resolve_reviewer_for_model
        agent, model = _resolve_reviewer_for_model(
            "minimax-coding-plan/MiniMax-M3", board=self.board,
        )
        self.assertEqual(agent, "minimax")
        self.assertEqual(model, "minimax-coding-plan/MiniMax-M3")

    def test_unknown_model_returns_none_tuple(self):
        _ensure_agents(only={"claude"})
        from tasks.views import _resolve_reviewer_for_model
        agent, model = _resolve_reviewer_for_model(
            "totally-unknown-model-xyz", board=self.board,
        )
        self.assertEqual((agent, model), (None, None))


class TestAvailableModelsShapeTolerance(APITestCase):
    """Bug #246: available_models entries may be bare strings, not dicts.

    Today the lookup uses ``m.get("name")`` which raises AttributeError on a
    bare-string entry; the surrounding ``any()`` returns False on the silent
    branch and the resolver returns (None, None). A real glm agent may carry
    its model list as plain strings — the resolver must accept that shape.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_resolves_against_bare_string_available_models(self):
        User.objects.filter(email__iendswith="@odin.agent").delete()
        User.objects.create(
            email="glm@odin.agent",
            name="GLM",
            role=UserRole.AGENT,
            is_active=True,
            available_models=["zai-coding-plan/glm-5.2"],
        )
        from tasks.views import _resolve_reviewer_for_model
        agent, model = _resolve_reviewer_for_model(
            "zai-coding-plan/glm-5.2", board=self.board,
        )
        self.assertEqual(agent, "glm")
        self.assertEqual(model, "zai-coding-plan/glm-5.2")