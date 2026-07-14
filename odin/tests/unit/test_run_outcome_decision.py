"""Decision-table tests for the evidence-ladder run-outcome (task #331).

Deciding pass/fail by parsing the model's stdout tail is the single most
fragile design in the kit — 85+ failures where the work was fine but the
last message lacked an ODIN-STATUS line. This suite pins the replacement:
an **evidence ladder** where an explicit verdict is believed when present,
and otherwise the worktree delta vs the HEAD recorded at run start decides.

One test per row of the decision table:

    | # | explicit signal | work? | exit        | outcome              |
    |---|-----------------|-------|-------------|----------------------|
    | 1 | SUCCESS         | any   | clean       | success (REVIEW)     |
    | 1b| FAILED          | any   | clean       | failed               |
    | 2 | none            | yes   | clean       | complete_unconfirmed |
    | 3 | none            | no    | clean       | failed (silent)      |
    | 4 | none            | no    | timeout     | failed (timeout)     |
    | 5 | none/any        | any   | sandbox_... | infra (no agent ran) |

The headline invariant (``Done means`` #3): there is NO row where a missing
marker *alone* fails a run — a missing marker with work goes to REVIEW.
"""

from __future__ import annotations

from odin.harnesses.base import (
    OUTCOME_FAILED,
    OUTCOME_INFRA,
    OUTCOME_SUCCESS,
    OUTCOME_UNCONFIRMED,
    RunOutcome,
    decide_run_outcome,
)


class TestExplicitSignalBelieved:
    """Rung 1 — an explicit verdict from ANY channel is the strongest
    evidence and is believed regardless of the worktree state."""

    def test_explicit_success_from_stdout_is_believed(self):
        outcome = decide_run_outcome(
            explicit_signal=True,
            signal_source="stdout",
            has_work=False,
            exit_condition="clean",
        )
        assert isinstance(outcome, RunOutcome)
        assert outcome.outcome == OUTCOME_SUCCESS
        assert outcome.success is True
        assert outcome.unconfirmed is False
        assert outcome.signal_source == "stdout"

    def test_explicit_success_from_status_file_is_believed(self):
        outcome = decide_run_outcome(
            explicit_signal=True,
            signal_source="status_file",
            has_work=False,
            exit_condition="clean",
        )
        assert outcome.success is True
        assert outcome.signal_source == "status_file"

    def test_explicit_success_from_mcp_is_believed(self):
        """The MCP channel — an agent that confirmed done out-of-band by
        posting a proof comment through the TaskIt MCP tools. With no
        stdout marker and no git delta, only this signal can distinguish
        success from a silent failure, so it must be believed and its
        source recorded."""
        outcome = decide_run_outcome(
            explicit_signal=True,
            signal_source="mcp",
            has_work=False,
            exit_condition="clean",
        )
        assert outcome.outcome == OUTCOME_SUCCESS
        assert outcome.success is True
        assert outcome.unconfirmed is False
        assert outcome.signal_source == "mcp"

    def test_explicit_failed_is_believed(self):
        outcome = decide_run_outcome(
            explicit_signal=False,
            signal_source="stdout",
            has_work=True,  # even with work present, an explicit FAILED wins
            exit_condition="clean",
        )
        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.success is False
        assert outcome.unconfirmed is False


class TestWorkWithoutSignal:
    """Rung 2 — no explicit verdict but the worktree has real edits.

    This is the exact task-313 case: the work was fine, the last message
    lacked the ODIN-STATUS line. It must land in REVIEW flagged
    unconfirmed, NOT failed."""

    def test_work_no_signal_goes_to_review_unconfirmed(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="clean",
        )
        assert outcome.outcome == OUTCOME_UNCONFIRMED
        # success=True means REVIEW, not FAILED.
        assert outcome.success is True
        assert outcome.unconfirmed is True

    def test_work_no_signal_carries_reviewer_flag(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="clean",
        )
        # The reviewer must be told to judge the diff, not the marker.
        assert outcome.reviewer_note
        assert "confirm" in outcome.reviewer_note.lower()

    def test_work_no_signal_on_timeout_still_reviews(self):
        """Work exists but the agent was cut off mid-run. There are real
        edits to salvage — hand the diff to the reviewer rather than
        discarding a timed-out-but-productive run."""
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=True,
            exit_condition="timeout",
        )
        assert outcome.outcome == OUTCOME_UNCONFIRMED
        assert outcome.success is True


class TestNoWorkNoSignal:
    """Rung 3 — no explicit verdict AND no work. Only here does the exit
    condition name the true reason, and the run fails."""

    def test_clean_exit_no_work_fails_silent(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,
            exit_condition="clean",
        )
        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.success is False
        # The reason is the true observable fact, never a "Likely..." guess.
        assert outcome.error
        assert "likely" not in outcome.error.lower()

    def test_clean_exit_no_work_prefers_stream_reason(self):
        """The true reason string from the stream wins over the default."""
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,
            exit_condition="clean",
            reason_detail="provider returned empty completion (finish_reason=stop, 0 tokens)",
        )
        assert outcome.success is False
        assert "empty completion" in outcome.error

    def test_timeout_no_work_fails_with_timeout_reason(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,
            exit_condition="timeout",
        )
        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.success is False
        assert outcome.failure_type == "timeout"
        assert "timed out" in outcome.error.lower() or "timeout" in outcome.error.lower()

    def test_crash_no_work_fails_with_crash_reason(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,
            exit_condition="crash",
            reason_detail="Process exited with code 137",
        )
        assert outcome.success is False
        assert outcome.failure_type == "agent_execution_failure"
        assert "137" in outcome.error


class TestPreExecutionInfra:
    """Rung 0 — a pre-execution failure means no agent process ever ran.

    No agent is blamed, no escalation fires; the routing policy retries
    the infra failure. Evaluated FIRST so it cannot be mistaken for a
    silent agent (no output because nothing ran, not because the model
    stalled)."""

    def test_sandbox_unavailable_is_infra_no_blame(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,
            exit_condition="sandbox_unavailable",
            reason_detail="microsandbox: msb daemon not reachable",
        )
        assert outcome.outcome == OUTCOME_INFRA
        assert outcome.success is False
        assert outcome.no_agent_ran is True
        # The failure_type must map to the infra-retry class, not an
        # agent-blaming one.
        assert outcome.failure_type == "sandbox_unavailable"

    def test_sandbox_unavailable_wins_over_a_stray_signal(self):
        """Even if some stale stdout parsed as a verdict, a sandbox that
        never booted means there is no agent output to trust."""
        outcome = decide_run_outcome(
            explicit_signal=True,
            signal_source="stdout",
            has_work=True,
            exit_condition="sandbox_unavailable",
        )
        assert outcome.outcome == OUTCOME_INFRA
        assert outcome.no_agent_ran is True

    def test_missing_worktree_is_infra(self):
        outcome = decide_run_outcome(
            explicit_signal=None,
            signal_source=None,
            has_work=False,
            exit_condition="no_worktree",
        )
        assert outcome.outcome == OUTCOME_INFRA
        assert outcome.no_agent_ran is True


class TestNoMissingMarkerEverFailsAlone:
    """The invariant that ``Done means`` #3 requires a grep to confirm:
    a missing marker on its own never fails a run. Exhaustively: for
    every exit condition that isn't a hard failure, work-present +
    no-signal is a REVIEW, and no-work is failed for the *exit* reason,
    never for the missing marker itself."""

    def test_missing_marker_with_work_never_fails(self):
        for exit_condition in ("clean", "timeout", "nonzero", "crash"):
            outcome = decide_run_outcome(
                explicit_signal=None,
                signal_source=None,
                has_work=True,
                exit_condition=exit_condition,
            )
            assert outcome.success is True, exit_condition
            assert outcome.outcome == OUTCOME_UNCONFIRMED, exit_condition
