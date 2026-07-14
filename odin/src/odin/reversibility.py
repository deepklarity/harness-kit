"""Single source of truth for action-reversibility classification (task #244).

The kit decides *when* to involve a human by hard-coded flow points, not by
what's at stake. This module replaces that with one rule: gate on
reversibility. Anything cheaply undoable proceeds autonomously; anything
hard to undo pauses for a human. Callers consult this module instead of
growing their own special-case ``if``.

Three buckets:

- ``REVERSIBLE`` — task branch commits, worktree changes, comments,
  requeues. Proceeds autonomously.
- ``SEMI`` — spec-branch merges: revertible but shared with other tasks.
  The merge flow (``odin.merge_agent`` + ``tasks.dag_executor``) already
  gates its own ambiguous cases via ``merge_status=needs_human``; semi
  actions don't get a *new* gate from this module.
- ``HARD`` — deleting a branch/worktree that still carries unmerged work,
  a config flip that affects currently-running work, anything that
  touches ``main``/the default branch, or an external publication (PR,
  webhook, etc). Pauses for a human before proceeding, reusing the same
  park → human reply → resume mechanism the merge flow uses.
"""

REVERSIBLE = "reversible"
SEMI = "semi"
HARD = "hard"

# The pinned classification table. Every action a caller wants to gate on
# reversibility must have an entry here — an unlisted action is a bug at
# the call site, not something to guess about, so lookups raise.
ACTIONS = {
    # reversible — cheaply undone, proceeds autonomously
    "task_branch_commit": REVERSIBLE,
    "worktree_change": REVERSIBLE,
    "post_comment": REVERSIBLE,
    "requeue_task": REVERSIBLE,
    "remove_worktree_branch_preserved": REVERSIBLE,
    "delete_branch_already_merged": REVERSIBLE,
    # semi — revertible but shared; the merge flow's own ambiguity gate
    # already covers the human-in-the-loop case for this bucket
    "spec_branch_merge": SEMI,
    # hard — pauses for a human
    "delete_branch_with_unmerged_work": HARD,
    "delete_worktree_with_unmerged_work": HARD,
    "config_flip_affecting_running_work": HARD,
    "touch_main_branch": HARD,
    "external_publication": HARD,
}


def classify(action: str) -> str:
    """Look up an action's reversibility class.

    Raises ``ValueError`` for an action not in :data:`ACTIONS` — an
    unclassified action must be added to the table, never guessed at the
    call site.
    """
    try:
        return ACTIONS[action]
    except KeyError:
        raise ValueError(f"Unclassified action for reversibility gating: {action!r}") from None


def is_hard(action: str) -> bool:
    """True if ``action`` is in the hard (pause-for-human) bucket."""
    return classify(action) == HARD


def classify_worktree_cleanup(merge_status) -> str:
    """Classify a task-branch-deletion cleanup by whether its work is safe.

    Deleting a task's worktree+branch (``WorktreeManager.cleanup_task_worktree``,
    which runs ``git branch -D``) is reversible once the branch's commits
    already live on the spec branch (``merge_status`` is ``merged`` or
    ``noop`` — the same field the merge flow already writes). Any other
    status means the branch could still carry work nothing else has a copy
    of, so deleting it now would be permanent — hard.
    """
    if merge_status in ("merged", "noop"):
        return classify("delete_branch_already_merged")
    return classify("delete_branch_with_unmerged_work")
