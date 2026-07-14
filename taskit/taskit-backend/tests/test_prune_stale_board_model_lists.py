"""Tests for `prune_stale_board_model_lists`.

Board.model_escalation_priority and Board.reviewer_order are ordered lists
of {agent_name, model_name} dicts, hand-authored (or auto-populated) via
the settings UI. When the curated model registry (agent_models.json) drops
a model, or an agent is retired, previously saved boards can end up with
entries that reference something that no longer exists. Those entries are
inert (never selectable) but should be actively cleaned up rather than
silently ignored.
"""

from django.core.management import call_command

from .base import APITestCase
from tasks.models import Board, User, UserRole


def _make_agent(name, models, active=True):
    return User.objects.create(
        email=f"{name}@odin.agent",
        name=name,
        role=UserRole.AGENT,
        is_active=active,
        available_models=models,
    )


class TestPruneStaleBoardModelLists(APITestCase):
    def test_removes_entries_for_model_dropped_from_agent(self):
        _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        board = self.make_board(
            model_escalation_priority=[
                {"agent_name": "claude", "model_name": "claude-sonnet-5"},
                {"agent_name": "claude", "model_name": "claude-sonnet-4-5"},  # dropped model
            ],
        )

        call_command("prune_stale_board_model_lists")

        board.refresh_from_db()
        self.assertEqual(board.model_escalation_priority, [
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ])

    def test_removes_entries_for_retired_agent(self):
        _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        _make_agent("gemini", [{"name": "gemini-2.5-pro", "is_default": True}], active=False)
        board = self.make_board(
            reviewer_order=[
                {"agent_name": "gemini", "model_name": "gemini-2.5-pro"},  # retired agent
                {"agent_name": "claude", "model_name": "claude-sonnet-5"},
            ],
        )

        call_command("prune_stale_board_model_lists")

        board.refresh_from_db()
        self.assertEqual(board.reviewer_order, [
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ])

    def test_cleans_both_list_fields_independently(self):
        _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        board = self.make_board(
            model_escalation_priority=[{"agent_name": "claude", "model_name": "gone-model"}],
            reviewer_order=[{"agent_name": "claude", "model_name": "gone-model"}],
        )

        call_command("prune_stale_board_model_lists")

        board.refresh_from_db()
        self.assertEqual(board.model_escalation_priority, [])
        self.assertEqual(board.reviewer_order, [])

    def test_dry_run_does_not_persist_changes(self):
        _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        board = self.make_board(
            model_escalation_priority=[{"agent_name": "claude", "model_name": "gone-model"}],
        )

        call_command("prune_stale_board_model_lists", dry_run=True)

        board.refresh_from_db()
        self.assertEqual(board.model_escalation_priority, [
            {"agent_name": "claude", "model_name": "gone-model"},
        ])

    def test_idempotent_on_clean_board(self):
        _make_agent("claude", [{"name": "claude-sonnet-5", "is_default": True}])
        board = self.make_board(
            model_escalation_priority=[{"agent_name": "claude", "model_name": "claude-sonnet-5"}],
            reviewer_order=[{"agent_name": "claude", "model_name": "claude-sonnet-5"}],
        )

        call_command("prune_stale_board_model_lists")
        call_command("prune_stale_board_model_lists")

        board.refresh_from_db()
        self.assertEqual(board.model_escalation_priority, [
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ])
        self.assertEqual(board.reviewer_order, [
            {"agent_name": "claude", "model_name": "claude-sonnet-5"},
        ])

    def test_leaves_empty_lists_untouched(self):
        board = self.make_board()
        call_command("prune_stale_board_model_lists")
        board.refresh_from_db()
        self.assertEqual(board.model_escalation_priority, [])
        self.assertEqual(board.reviewer_order, [])
