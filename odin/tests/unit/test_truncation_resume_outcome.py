"""Decision-table tests for the truncation-resume rung (task #332).

When a model hits its output cap mid-task, the run truncates with real work
sitting in the worktree. Today that lands in REVIEW as an *unconfirmed
completion* (task #331 Rung 2) — but the work is unfinished, so handing an
incomplete diff to the reviewer is wrong. The right move is to RESUME: requeue
the same task into the same worktree with a host-built resume prompt.

This suite pins the new rung the classifier grew for that case:

    | # | explicit | truncated | work        | outcome              |
    |---|----------|-----------|-------------|----------------------|
    | 1 | none     | yes       | yes         | truncated_resumable  |
    | 2 | none     | yes       | resumable*  | truncated_resumable  |
    | 3 | SUCCESS  | yes       | any         | success (Rung 1 wins)|
    | 4 | none     | yes       | none        | failed (truncation)  |
    | 5 | none     | no        | yes         | complete_unconfirmed |
    | 6 | any      | yes       | any + infra | infra (Rung 0 wins)  |

    *resumable = strict has_work is False (dirty at start) but the isolated
     worktree still holds prior-attempt edits — the has_resumable_work signal.

The headline invariant: a *confirmed-done* run is never resumed, and a
truncation with genuine work-in-progress is never thrown away.
"""

from __future__ import annotations

from odin.harnesses.base import (
    OUTCOME_FAILED,
    OUTCOME_INFRA,
    OUTCOME_SUCCESS,
    OUTCOME_TRUNCATED_RESUMABLE,
    OUTCOME_UNCONFIRMED,
    RunOutcome,
    decide_run_outcome,
    finish_reason_is_output_cap,
)


class TestFinishReasonIsOutputCap:
    def test_length_and_max_tokens_are_output_cap(self):
        assert finish_reason_is_output_cap("length") is True
        assert finish_reason_is_output_cap("max_tokens") is True
        assert finish_reason_is_output_cap("max_turns") is True

    def test_normal_stops_are_not_output_cap(self):
        assert finish_reason_is_output_cap("stop") is False
        assert finish_reason_is_output_cap("end_turn") is False
        assert finish_reason_is_output_cap("") is False
        assert finish_reason_is_output_cap(None) is False


class TestTruncationWithWorkResumes:
    """Row 1 — the headline case: output cap hit mid-task, real work in the
    worktree. Resume in place, don't discard and don't review-half."""

    def test_truncated_with_work_is_resumable(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="clean",
            truncated=True,
        )
        assert isinstance(outcome, RunOutcome)
        assert outcome.outcome == OUTCOME_TRUNCATED_RESUMABLE
        # Routes to FAILED so the executor's requeue path fires; it is NOT a
        # review-worthy completion.
        assert outcome.success is False
        assert outcome.resumable is True
        # Classified as truncation so the truncation AUTO_REQUEUE policy picks
        # it up (same assignee, same worktree, attempt n+1).
        assert outcome.failure_type == "truncation"

    def test_resumable_error_names_the_output_cap(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="clean",
            truncated=True,
        )
        # A downstream tagger must be able to read "truncation" out of the
        # reason text too (belt-and-suspenders with failure_type).
        assert outcome.error
        assert "cap" in outcome.error.lower() or "truncat" in outcome.error.lower()

    def test_dirty_start_still_resumable_via_resumable_work_signal(self):
        """Row 2 — the crux of a *second* resume. The worktree was dirty at
        start (the prior attempt's uncommitted edits), so strict has_work is
        False, but the isolated worktree still holds resumable work. The
        classifier must keep resuming, not fall through to no-work-failed."""
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,           # strict delta sees nothing new (start dirty)
            has_resumable_work=True,  # but prior-attempt edits are still here
            exit_condition="clean",
            truncated=True,
        )
        assert outcome.outcome == OUTCOME_TRUNCATED_RESUMABLE
        assert outcome.resumable is True


class TestConfirmedDoneIsNeverResumed:
    """Row 3 — an explicit SUCCESS from ANY channel wins over truncation.
    A run that confirmed done out-of-band (e.g. an MCP proof comment) whose
    trailing stream got cut is complete, not resumable."""

    def test_explicit_success_beats_truncation(self):
        outcome = decide_run_outcome(
            explicit_signal=True,
            signal_source="mcp",
            has_work=True,
            exit_condition="clean",
            truncated=True,
        )
        assert outcome.outcome == OUTCOME_SUCCESS
        assert outcome.success is True
        assert outcome.resumable is False


class TestTruncationWithoutWork:
    """Row 4 — truncated but the worktree is empty. There is nothing to
    resume, so it is an honest failure — but named truncation, not a silent
    hang (there WAS output, the model just got cut off before doing work)."""

    def test_truncated_no_work_fails_as_truncation(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,
            has_resumable_work=False,
            exit_condition="clean",
            truncated=True,
        )
        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.success is False
        assert outcome.resumable is False
        assert outcome.failure_type == "truncation"


class TestRegressionUnchangedRungs:
    """Rows 5 & 6 — the pre-existing ladder is untouched when truncated is
    absent/false, and pre-execution infra still wins over everything."""

    def test_work_without_truncation_still_reviews(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="clean",
            truncated=False,
        )
        assert outcome.outcome == OUTCOME_UNCONFIRMED
        assert outcome.success is True

    def test_default_truncated_is_false(self):
        """Callers that predate task #332 don't pass truncated — the default
        must preserve the Rung 2 REVIEW behaviour."""
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="clean",
        )
        assert outcome.outcome == OUTCOME_UNCONFIRMED

    def test_infra_wins_over_truncation(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="sandbox_unavailable",
            truncated=True,
        )
        assert outcome.outcome == OUTCOME_INFRA
        assert outcome.no_agent_ran is True
