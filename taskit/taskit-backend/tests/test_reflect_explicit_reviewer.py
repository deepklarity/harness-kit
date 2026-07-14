"""Tests for bug #325 — the manual /reflect/ endpoint must honor the caller's
explicit reviewer_agent/reviewer_model all the way through to the reflection
run, and reject unknown (agent, model) pairs with a 400 that lists valid
choices instead of silently storing them.

Three guarantees:
1. POSTing an explicit (agent, model) pair stores both on the report with
   selection_reason=caller_override.
2. The forced_base_provider env knob must NOT silently overwrite those values
   either at the API layer or at the CLI layer (covered indirectly by
   odin/tests/mock/test_reflect_command.py::TestOdinCLIReflectHonorsExplicitReviewer
   — only the API-layer test is duplicated here).
3. A bogus model name returns a 400 listing valid choices, not a silent
   fallback.
"""

from unittest.mock import patch

from django.test import override_settings

from .base import APITestCase
from tasks.models import ReflectionReport, TaskStatus, User, UserRole


def _make_claude_agent():
    """Create a claude agent User carrying the canonical model set."""
    User.objects.get_or_create(
        email="claude@odin.agent",
        defaults={
            "name": "Claude",
            "role": UserRole.AGENT,
            "available_models": [
                {"name": "claude-haiku-4-5", "is_default": False},
                {"name": "claude-sonnet-5", "is_default": True},
                {"name": "claude-opus-4-8", "is_default": False},
            ],
        },
    )


class TestReflectExplicitReviewer(APITestCase):
    """Bug #325 — POST /tasks/:id/reflect/ must honor explicit reviewer_agent/model."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        _make_claude_agent()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_explicit_reviewer_persists_on_report(self, mock_delay):
        """Explicit reviewer_agent + reviewer_model are stored verbatim on
        the report — no rewriting by the serializer defaults, no rewriting by
        size-bucket logic, no rewriting by forced_provider env (the latter
        is an odin-CLI concern tested separately)."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)

        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {"reviewer_agent": "claude", "reviewer_model": "claude-opus-4-8"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["reviewer_agent"], "claude")
        self.assertEqual(resp.data["reviewer_model"], "claude-opus-4-8")
        self.assertEqual(resp.data["selection_reason"], "caller_override")
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.reviewer_model, "claude-opus-4-8")
        mock_delay.assert_called_once_with(report.id)


class TestReflectBogusReviewerRejected(APITestCase):
    """Bug #325 — if the caller asks for an agent/model that doesn't exist,
    return 400 listing valid options instead of accepting and burning tokens
    on a model the agent can't run."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        _make_claude_agent()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_bogus_agent_returns_400_listing_valid_agents(self, mock_delay):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {
                "reviewer_agent": "totally-bogus-agent-xyz",
                "reviewer_model": "claude-opus-4-8",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        # The serializer's validate_reviewer_agent surfaces the allowed
        # list so the operator can fix the typo without grepping the docs.
        msg = str(resp.data).lower()
        self.assertIn("claude", msg)
        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_bogus_model_for_known_agent_returns_400_listing_models(
        self, mock_delay,
    ):
        """Model that the named agent doesn't advertise → 400 with the
        agent's actual available_models so the operator can fix the typo."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {
                "reviewer_agent": "claude",
                "reviewer_model": "totally-bogus-model-xyz",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        body = resp.data
        # The error must name the bad model AND list the valid options
        # (otherwise operators just see "invalid" and ask "what's valid?").
        self.assertIn("totally-bogus-model-xyz", str(body))
        self.assertIn("claude-opus-4-8", str(body))
        self.assertIn("claude-sonnet-5", str(body))
        self.assertIn("allowed_models", body)
        mock_delay.assert_not_called()

    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_model_not_supplied_falls_back_to_default_without_400(
        self, mock_delay,
    ):
        """Caller supplied only reviewer_agent (no reviewer_model) → use
        the size-bucket default for that agent. The bogus-model validation
        must not fire on missing data — only on explicit wrong data."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {"reviewer_agent": "claude"},
            format="json",
        )
        self.assertEqual(resp.status_code, 202, resp.data)
        mock_delay.assert_called_once()


class TestReflectForcedProviderDoesNotSilenceCaller(APITestCase):
    """Bug #325 — confirm the API layer (the `views.py` reflect action)
    honors explicit reviewer_agent/model even when FORCED_BASE_PROVIDER is
    set in the environment. The CLI-layer side of the same guarantee is
    covered in odin/tests/mock/test_reflect_command.py::TestOdinCLIReflectHonorsExplicitReviewer
    (one layer up — same invariant tested at the seam)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        _make_claude_agent()

    @override_settings(
        FORCED_BASE_PROVIDER="gemini",
        FORCED_BASE_MODEL="gemini-2.5-pro",
    )
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/gemini")
    @patch("tasks.dag_executor.execute_reflection.delay")
    def test_explicit_pair_wins_over_forced_provider(
        self, mock_delay, _mock_which,
    ):
        """Even when FORCED_BASE_PROVIDER=gemini is set globally, an
        explicit reviewer_agent=claude + reviewer_model=claude-opus-4-8 must
        land on the report unchanged (clauses the operator's intent down
        to the dispatched Celery task)."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        resp = self.client.post(
            f"/tasks/{task.id}/reflect/",
            {
                "reviewer_agent": "claude",
                "reviewer_model": "claude-opus-4-8",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        report = ReflectionReport.objects.get(task=task)
        self.assertEqual(report.reviewer_agent, "claude")
        self.assertEqual(report.reviewer_model, "claude-opus-4-8")
        self.assertEqual(report.selection_reason, "caller_override")
        mock_delay.assert_called_once_with(report.id)
