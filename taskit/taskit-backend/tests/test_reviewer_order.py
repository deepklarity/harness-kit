"""Tests for deterministic, quota-aware reflection reviewer selection.

Replaces the old random default-reviewer pick (`_find_first_available_reviewer`
used `random.shuffle`) with an ordered, board-configured walk:

Precedence (highest to lowest):
1. board.reflection_model            -> "board_model_override"
2. board.reflection_review_strategy  -> "size_small" | "size_medium" | "size_large"
3. board.reviewer_order walk         -> "reviewer_order" (skips agents that
   aren't enabled board members, models not in the agent's available_models,
   and agents whose real-time quota is exhausted)
4. Deterministic strongest-first fallback (by output_price_per_1m_tokens,
   descending) when reviewer_order is empty/exhausted -> "default_strongest"

The env-var forced-provider mechanism (tasks/forced_provider.py) must no
longer influence reviewer selection at all.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from django.test import override_settings

from .base import APITestCase
from tasks.models import User, UserRole
from tasks.views import (
    QuotaGroundTruth,
    _QUOTA_EXHAUSTED,
    _QUOTA_HEADROOM,
    _QUOTA_UNAVAILABLE,
    select_reviewer_by_context_size,
)


def _make_agent(name, models, active=True):
    return User.objects.create(
        email=f"{name}@odin.agent",
        name=name.title(),
        role=UserRole.AGENT,
        is_active=active,
        available_models=models,
    )


def _membership(board, user):
    from tasks.models import BoardMembership
    return BoardMembership.objects.create(board=board, user=user)


class TestReviewerOrderWalk(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_walk_picks_entry_zero_when_viable(self):
        claude = _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        gemini = _make_agent("gemini", [{"name": "gemini-2.5-pro", "is_default": True}])
        _membership(self.board, claude)
        _membership(self.board, gemini)
        self.board.reviewer_order = [
            {"agent_name": "gemini", "model_name": "gemini-2.5-pro"},
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_HEADROOM, 10.0, "ok")):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        self.assertEqual((agent, model), ("gemini", "gemini-2.5-pro"))
        self.assertTrue(reason.startswith("reviewer_order"))

    def test_walk_skips_agent_not_on_board(self):
        claude = _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        _make_agent("gemini", [{"name": "gemini-2.5-pro", "is_default": True}])  # not a board member
        _membership(self.board, claude)
        self.board.reviewer_order = [
            {"agent_name": "gemini", "model_name": "gemini-2.5-pro"},
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_HEADROOM, 10.0, "ok")):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        self.assertEqual((agent, model), ("claude", "claude-sonnet-5"))
        self.assertTrue(reason.startswith("reviewer_order"))

    def test_walk_skips_inactive_agent_user(self):
        claude = _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        gemini = _make_agent("gemini", [{"name": "gemini-2.5-pro", "is_default": True}], active=False)
        _membership(self.board, claude)
        _membership(self.board, gemini)
        self.board.reviewer_order = [
            {"agent_name": "gemini", "model_name": "gemini-2.5-pro"},
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_HEADROOM, 10.0, "ok")):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        self.assertEqual((agent, model), ("claude", "claude-sonnet-5"))

    def test_walk_skips_entry_whose_model_not_in_available_models(self):
        claude = _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        _membership(self.board, claude)
        self.board.reviewer_order = [
            {"agent_name": "claude", "model_name": "claude-opus-4-8"},  # not available
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_HEADROOM, 10.0, "ok")):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        self.assertEqual((agent, model), ("claude", "claude-sonnet-5"))

    def test_walk_skips_agent_with_exhausted_quota(self):
        claude = _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        gemini = _make_agent("gemini", [{"name": "gemini-2.5-pro", "is_default": True}])
        _membership(self.board, claude)
        _membership(self.board, gemini)
        self.board.reviewer_order = [
            {"agent_name": "gemini", "model_name": "gemini-2.5-pro"},
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        def fake_usage(agent_key):
            if agent_key == "gemini":
                return QuotaGroundTruth(_QUOTA_EXHAUSTED, 99.0, "exhausted")
            return QuotaGroundTruth(_QUOTA_HEADROOM, 5.0, "ok")

        with patch("tasks.views._check_provider_usage", side_effect=fake_usage):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        self.assertEqual((agent, model), ("claude", "claude-sonnet-5"))

    def test_walk_does_not_skip_agent_with_unavailable_quota(self):
        """A down/unmapped quota checker must not brick reviewer selection."""
        gemini = _make_agent("gemini", [{"name": "gemini-2.5-pro", "is_default": True}])
        _membership(self.board, gemini)
        self.board.reviewer_order = [
            {"agent_name": "gemini", "model_name": "gemini-2.5-pro"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_UNAVAILABLE, None, "no provider mapped")):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        self.assertEqual((agent, model), ("gemini", "gemini-2.5-pro"))
        self.assertTrue(reason.startswith("reviewer_order"))

    def test_empty_reviewer_order_falls_back_to_strongest_first_deterministically(self):
        _make_agent("claude", [
            {"name": "claude-haiku-4-5", "is_default": False, "output_price_per_1m_tokens": 5},
        ])
        gemini = _make_agent("gemini", [
            {"name": "gemini-2.5-pro", "is_default": True},
        ])
        claude = User.objects.get(email="claude@odin.agent")
        _membership(self.board, claude)
        _membership(self.board, gemini)
        self.board.reviewer_order = []
        self.board.save(update_fields=["reviewer_order"])

        results = []
        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_HEADROOM, 1.0, "ok")):
            for _ in range(5):
                results.append(select_reviewer_by_context_size(self.task, board=self.board))

        self.assertEqual(len(set(results)), 1, "fallback must be stable/non-random across calls")
        self.assertEqual(results[0][2], "default_strongest")

    def test_legacy_reflection_model_is_ignored_reviewer_order_wins(self):
        claude = _make_agent("claude", [
            {"name": "claude-sonnet-5", "is_default": True},
            {"name": "claude-opus-4-8", "is_default": False},
        ])
        _membership(self.board, claude)
        self.board.reviewer_order = [
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.reflection_model = "claude-opus-4-8"
        self.board.save(update_fields=["reviewer_order", "reflection_model"])

        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_HEADROOM, 1.0, "ok")):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        # Operator directive: the ordered walk is the ONE mechanism; the
        # legacy single-model pin is set here and must be ignored.
        self.assertEqual((agent, model), ("claude", "claude-sonnet-5"))
        self.assertEqual(reason, "reviewer_order[0]")

    @override_settings(FORCED_BASE_PROVIDER="glm", FORCED_BASE_MODEL="zai-coding-plan/glm-5.2")
    @patch("tasks.forced_provider.shutil.which", return_value="/usr/bin/opencode")
    def test_forced_provider_env_does_not_affect_selection(self, _mock_which):
        claude = _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        _membership(self.board, claude)
        self.board.reviewer_order = [
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ]
        self.board.save(update_fields=["reviewer_order"])

        with patch("tasks.views._check_provider_usage",
                   return_value=QuotaGroundTruth(_QUOTA_HEADROOM, 1.0, "ok")):
            agent, model, reason = select_reviewer_by_context_size(self.task, board=self.board)

        self.assertEqual((agent, model), ("claude", "claude-sonnet-5"))
        self.assertNotEqual(reason, "forced_provider")
