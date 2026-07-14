"""Task 183 — reflection input must be bounded.

The reflection prompt is assembled fresh and sent to an Opus-class reviewer
that ALSO has read-only access to the worktree and the canonical per-task
proof file ``.proof/task-<id>/proof.md``. Two unbounded inputs let a single
large task inflate that Opus prompt without adding evidence the reviewer
can't already reach:

  1. ``execution_output`` = the whole executor text trace, uncapped.
  2. Non-noisy (proof) comments bypassed the comment char cap entirely, and
     prior-attempt history was injected at full size even though the rubric
     tells the reviewer to judge only the current attempt.

These tests pin head+tail truncation on the execution output and a cap on
proof / history comments. Detail is never lost — the reviewer reads the
uncapped per-task ``.proof/`` files from the worktree mount when a criterion
needs them.
"""

from odin.reflection import (
    _EXECUTION_OUTPUT_CHAR_LIMIT,
    _HISTORY_COMMENT_CHAR_LIMIT,
    _PROOF_COMMENT_CHAR_LIMIT,
    _format_comment_for_prompt,
    _truncate_execution_output,
)


class TestTruncateExecutionOutput:
    def test_short_output_unchanged(self):
        text = "did the work\n-------ODIN-STATUS-------\nSUCCESS"
        assert _truncate_execution_output(text, _EXECUTION_OUTPUT_CHAR_LIMIT) == text

    def test_long_output_capped(self):
        text = "A" * 100_000
        out = _truncate_execution_output(text, _EXECUTION_OUTPUT_CHAR_LIMIT)
        assert len(out) <= _EXECUTION_OUTPUT_CHAR_LIMIT + 120  # + marker slack

    def test_tail_preserved_so_odin_status_survives(self):
        # The verdict-relevant ODIN-STATUS/summary lives at the very end of the
        # trace; truncation must keep the tail, not just the head.
        head = "PLAN: " + ("x" * 100_000)
        tail = "\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nshipped it"
        out = _truncate_execution_output(head + tail, _EXECUTION_OUTPUT_CHAR_LIMIT)
        assert "ODIN-STATUS" in out
        assert "shipped it" in out
        assert "truncated" in out.lower()

    def test_empty_passthrough(self):
        assert _truncate_execution_output("", _EXECUTION_OUTPUT_CHAR_LIMIT) == ""


class TestProofCommentCap:
    def test_proof_comment_is_capped(self):
        # A worker that pastes a full suite dump into the proof comment (against
        # policy) must not blow up the reflection prompt.
        big = {"comment_type": "proof", "content": "PROOF " + ("z" * 50_000)}
        out = _format_comment_for_prompt(big)
        assert len(out) <= _PROOF_COMMENT_CHAR_LIMIT + 120
        assert "truncated" in out.lower()

    def test_small_proof_comment_untouched(self):
        c = {"comment_type": "proof", "content": "See .proof/task-42/proof.md @ abc123"}
        out = _format_comment_for_prompt(c)
        assert out == "- [proof] See .proof/task-42/proof.md @ abc123"

    def test_history_comment_capped_harder(self):
        # Comments before the current-attempt checkpoint are context only; they
        # get a much tighter cap than current-attempt proof.
        big = {"comment_type": "proof", "content": "OLD " + ("q" * 50_000)}
        cur = _format_comment_for_prompt(big, is_history=False)
        hist = _format_comment_for_prompt(big, is_history=True)
        assert len(hist) < len(cur)
        assert len(hist) <= _HISTORY_COMMENT_CHAR_LIMIT + 120

    def test_noisy_comment_still_capped_at_2000(self):
        # Existing behavior preserved: status_update stays on the 2000 cap.
        big = {"comment_type": "status_update", "content": "s" * 50_000}
        out = _format_comment_for_prompt(big)
        assert len(out) <= 2000 + 120
