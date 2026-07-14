"""Unit tests for the reversibility classification module (task #244).

Pins the classification table and the one context-dependent lookup
(``classify_worktree_cleanup``) it owns. No I/O — pure data lookups.
"""

import pytest

from odin.reversibility import (
    HARD,
    REVERSIBLE,
    SEMI,
    ACTIONS,
    classify,
    classify_worktree_cleanup,
    is_hard,
)


# ------------------------------------------------------------------
# The pinned classification table
# ------------------------------------------------------------------

class TestClassificationTable:
    """WHY (task #244): one module, not scattered ifs. Pin the table so a
    future edit that reclassifies an action is a visible, reviewable diff.
    """

    @pytest.mark.parametrize("action", [
        "task_branch_commit",
        "worktree_change",
        "post_comment",
        "requeue_task",
        "remove_worktree_branch_preserved",
        "delete_branch_already_merged",
    ])
    def test_reversible_actions(self, action):
        assert classify(action) == REVERSIBLE
        assert is_hard(action) is False

    def test_spec_branch_merge_is_semi(self):
        assert classify("spec_branch_merge") == SEMI
        assert is_hard("spec_branch_merge") is False

    @pytest.mark.parametrize("action", [
        "delete_branch_with_unmerged_work",
        "delete_worktree_with_unmerged_work",
        "config_flip_affecting_running_work",
        "touch_main_branch",
        "external_publication",
    ])
    def test_hard_actions(self, action):
        assert classify(action) == HARD
        assert is_hard(action) is True

    def test_every_action_classified_as_one_of_the_three_buckets(self):
        assert set(ACTIONS.values()) <= {REVERSIBLE, SEMI, HARD}
        assert ACTIONS  # non-empty

    def test_unknown_action_raises(self):
        with pytest.raises(ValueError, match="Unclassified action"):
            classify("delete_production_database")


# ------------------------------------------------------------------
# classify_worktree_cleanup — the context-dependent lookup
# ------------------------------------------------------------------

class TestClassifyWorktreeCleanup:
    @pytest.mark.parametrize("merge_status", ["merged", "noop"])
    def test_merged_or_noop_is_reversible(self, merge_status):
        assert classify_worktree_cleanup(merge_status) == REVERSIBLE

    @pytest.mark.parametrize("merge_status", [
        "conflict", "error", "needs_human", "pending", None, "",
    ])
    def test_anything_else_is_hard(self, merge_status):
        assert classify_worktree_cleanup(merge_status) == HARD
