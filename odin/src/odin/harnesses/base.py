"""Abstract base class for agent harnesses."""

import asyncio
import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, List, Dict, Optional

from odin.models import AgentConfig, TaskResult

# 10 MiB — asyncio.StreamReader default is 64 KiB which is too small for
# large JSON stream events emitted by CLI agents (e.g. result events with
# full output can exceed 64 KiB on a single line, causing LimitOverrunError).
SUBPROCESS_STREAM_LIMIT = 10 * 1024 * 1024

ODIN_STATUS_MARKER = "-------ODIN-STATUS-------"


@dataclass
class OdinStatusResult:
    """Structured outcome of parsing an ODIN-STATUS block.

    Fields:

    - ``success`` — True only when we have a verdict that allows the
      task to proceed (explicit SUCCESS, OR a malformed block with
      observable work in the worktree).
    - ``error`` — human-readable reason when ``success`` is False; None
      when ``success`` is True.
    - ``raw_block`` — the verbatim value the agent emitted (post the
      ``-------ODIN-STATUS-------`` separator). ``None`` for the
      well-formed / missing-block cases; populated for malformed
      values so the orchestrator can record the exact garbage the
      model produced.
    - ``inferred`` — True when success came from worktree inspection
      rather than an explicit SUCCESS word. The orchestrator puts
      this in ``TaskResult.metadata['malformed_status']`` so the
      reviewer and the league-table query can see when a model
      emitted garbage instead of an honest verdict.
    - ``inference_reason`` — short string explaining *why* inference
      succeeded (e.g. "1 commit ahead of base", "uncommitted edits").
      Empty when ``inferred`` is False.
    """

    success: bool
    error: Optional[str] = None
    raw_block: Optional[str] = None
    inferred: bool = False
    inference_reason: Optional[str] = None

    def as_legacy_tuple(self) -> tuple[bool, Optional[str]]:
        """Return the (success, error) pair old callers expect."""
        return self.success, self.error


def _git_commits_ahead(worktree_path: Optional[str], *, base: str = "main") -> int:
    """Return the count of commits on the current branch that are
    not in ``base``. Returns 0 when the path is missing, not a git
    worktree, or the merge-base call fails. The helper swallows all
    errors because it runs in the failure path of ``validate_odin_status``
    — git trouble must never crash the live task.
    """
    if not worktree_path:
        return 0
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{base}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def _git_has_uncommitted_changes(worktree_path: Optional[str]) -> bool:
    """True if the worktree has uncommitted or untracked files. Used
    as the fallback signal when the agent committed nothing but
    edited locally.
    """
    if not worktree_path:
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _git_head(worktree_path: Optional[str]) -> Optional[str]:
    """Return the worktree's current HEAD sha, or None when the path is
    missing or not a git worktree.

    Recording the start HEAD is bookkeeping around the run, so this
    swallows every error (see docs/patterns/bookkeeping-never-kills-the-run):
    a git hiccup at run start must never crash the run it is only trying
    to measure.
    """
    if not worktree_path:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_commits_since(worktree_path: Optional[str], start_head: Optional[str]) -> int:
    """Count commits added to the worktree since ``start_head``.

    This is the run-scoped delta the evidence ladder trusts. Unlike
    ``main..HEAD`` (see :func:`_git_commits_ahead`) it cannot be fooled by
    commits a *prior* attempt left on the same task branch, because it
    measures from the HEAD recorded when THIS run began. Returns 0 on any
    error or when ``start_head`` is unknown.
    """
    if not worktree_path or not start_head:
        return 0
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{start_head}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def worktree_has_work_since(
    worktree_path: Optional[str],
    start_head: Optional[str],
    *,
    start_dirty: bool = False,
) -> bool:
    """True when the worktree shows observable agent work relative to the
    HEAD recorded at run start.

    "Work" is: at least one new commit since ``start_head``, or — when the
    tree was clean at run start — any uncommitted / untracked edit. This is
    the evidence the ladder falls back to when no explicit verdict is
    present: real edits mean the run produced something a reviewer can
    judge.

    Work MUST be scoped to this run's baseline:

    - Without a recorded ``start_head`` there is no baseline at all, so we
      return False rather than guess.
    - ``start_dirty`` records whether the tree already had uncommitted
      changes when THIS run began. When it did (e.g. the run fell back to
      the shared project checkout, which a developer may have left dirty),
      uncommitted changes cannot be attributed to the agent — only new
      commits count. A fresh task worktree is clean at start
      (``start_dirty=False``), so its uncommitted edits are the agent's.

    This is the guard against the ambient-cwd trap: a run that never got
    an isolated worktree must not read unrelated repo dirt as a completion.
    """
    if not start_head:
        return False
    if _git_commits_since(worktree_path, start_head) > 0:
        return True
    if start_dirty:
        return False
    return _git_has_uncommitted_changes(worktree_path)


def worktree_has_resumable_work(
    worktree_path: Optional[str], start_head: Optional[str]
) -> bool:
    """True when an isolated worktree holds work worth resuming (task #332).

    Unlike :func:`worktree_has_work_since`, this ignores the ``start_dirty``
    guard: it is called ONLY for the truncation-resume path, which by
    construction runs inside an isolated task worktree where every
    uncommitted change is agent work. The second resume attempt starts on the
    FIRST attempt's uncommitted edits (dirty at start), so the strict
    ambient-cwd guard would wrongly report "no work" and stall the resume
    loop — this signal keeps counting those edits.

    "Resumable work" is: a commit since ``start_head``, or any uncommitted /
    untracked edit in the tree. A genuinely clean tree still returns False, so
    a truncation that produced nothing is not mistaken for a resume.

    The isolation precondition is the caller's responsibility — the
    orchestrator only reaches this signal when ``working_dir`` is a real task
    worktree (never the shared project checkout).
    """
    if _git_commits_since(worktree_path, start_head) > 0:
        return True
    return _git_has_uncommitted_changes(worktree_path)


def validate_odin_status(stdout_text: str) -> tuple[bool, Optional[str]]:
    """Verify the agent emitted a proper ODIN-STATUS block declaring SUCCESS.

    Legacy tuple-returning helper. New code should call
    :func:`validate_odin_status_full` so the worktree-inference path
    is available, but this signature is preserved so callers that
    unpacked ``success, error = validate_odin_status(text)`` keep
    working.

    Every task prompt tells the agent to end its output with:

        -------ODIN-STATUS-------
        SUCCESS or FAILED
        -------ODIN-SUMMARY-------
        <summary>

    This helper guards against cases where the agent's CLI exits cleanly
    (returncode 0) but the model silently truncated mid-generation, errored,
    or never emitted the status block at all. Observed failure modes in
    production: step_finish with reason="stop" and tokens.output=0, and
    step_finish with reason="other" and tokens=0.

    Handles both raw text output (plain agent stdout) and JSONL-stream output
    (opencode / claude CLI emit the block inside a JSON string, so real
    newlines appear as literal \\n escape sequences in the raw bytes).

    Returns (success, error_message). success is True only when a trailing
    ODIN-STATUS block with value "SUCCESS" is present.
    """
    result = validate_odin_status_full(stdout_text)
    return result.as_legacy_tuple()


def validate_odin_status_full(
    stdout_text: str,
    *,
    worktree_path: Optional[str] = None,
) -> OdinStatusResult:
    """Parse the ODIN-STATUS envelope and return a structured result.

    Behaviour:

    - **Well-formed ``SUCCESS``** → ``OdinStatusResult(success=True)``.
    - **Well-formed ``FAILED``** → ``OdinStatusResult(success=False, ...)``.
    - **Empty stdout / no block** → ``OdinStatusResult(success=False, ...,
      raw_block=None)``.
    - **Malformed value (e.g. ``'\\'`` for task #234) + worktree_path
      with commits ahead of ``main``** → ``OdinStatusResult(success=True,
      inferred=True, ...)``. Inference is the headline recovery:
      the agent committed real work but emitted garbage for the status
      word, so we let the run continue and mark the inference.
    - **Malformed value + worktree_path with dirty-but-no-commit** →
      same inference path (uncommitted edits count as observable work;
      the orchestrator auto-commits after the run).
    - **Malformed value + clean worktree** → ``OdinStatusResult(success=
      False, raw_block=...)``. No observable work, no inference, fail as
      today.

    The orchestrator surfaces ``raw_block`` and ``inferred`` via
    ``TaskResult.metadata['malformed_status']`` so the ErrorEvent ledger
    (W6.5) can record the malformed block with the agent + model name.
    """
    if not stdout_text:
        return OdinStatusResult(
            success=False,
            error=(
                "Agent produced no output; likely the CLI crashed or the "
                "model terminated silently before emitting anything."
            ),
        )
    idx = stdout_text.rfind(ODIN_STATUS_MARKER)
    if idx == -1:
        return OdinStatusResult(
            success=False,
            error=(
                "Agent did not emit an ODIN-STATUS block. Likely the model "
                "truncated mid-generation or the response terminated silently "
                "(e.g. provider hit output cap, network drop, or unknown error)."
            ),
        )
    tail = stdout_text[idx + len(ODIN_STATUS_MARKER):]
    # Normalise JSON escape sequences so "\\n" and real "\n" are treated the
    # same. Opencode / Claude Code stream JSONL events where the agent's text
    # is embedded inside a JSON string, leaving backslash-n in the raw bytes.
    normalised = tail.replace("\\n", "\n").replace("\\r", "\r")
    first_token = normalised.split(None, 1)[0] if normalised.split() else ""
    raw_block = first_token
    if first_token == "SUCCESS":
        return OdinStatusResult(success=True)
    if first_token == "FAILED":
        return OdinStatusResult(
            success=False,
            error="Agent explicitly reported FAILED in ODIN-STATUS block.",
        )

    # Malformed value — try to infer success from observable work. We must
    # NOT do this when the value is empty or whitespace-only (those cases
    # mean "agent forgot the envelope" and inference would mask a real
    # truncation).
    if not raw_block:
        return OdinStatusResult(
            success=False,
            error="ODIN-STATUS block has unexpected value: empty",
            raw_block=raw_block,
        )

    ahead = _git_commits_ahead(worktree_path)
    if ahead > 0:
        return OdinStatusResult(
            success=True,
            raw_block=raw_block,
            inferred=True,
            inference_reason=f"{ahead} commit(s) ahead of base branch",
        )
    if _git_has_uncommitted_changes(worktree_path):
        return OdinStatusResult(
            success=True,
            raw_block=raw_block,
            inferred=True,
            inference_reason="uncommitted edits in worktree",
        )

    return OdinStatusResult(
        success=False,
        error=f"ODIN-STATUS block has unexpected value: {first_token!r}",
        raw_block=raw_block,
    )


# ── Evidence-ladder run outcome (task #331) ──────────────────────────
#
# Deciding pass/fail by parsing the model's stdout tail is the single most
# fragile design in the kit: 85+ failures where the work was fine but the
# last message lacked an ODIN-STATUS line. The ladder replaces that:
#
#   Rung 0  pre-execution / no agent ran  → INFRA (no blame, infra retry)
#   Rung 1  an explicit verdict is present → believe it (SUCCESS / FAILED)
#   Rung 2  no verdict, but work exists    → complete_unconfirmed (REVIEW)
#   Rung 3  no verdict, no work            → failed, with the true reason
#
# The status block stays in the agent prompt as a courtesy accelerator —
# nothing here fails a run *solely* because the block is missing.

OUTCOME_SUCCESS = "success"                    # explicit success signal believed
OUTCOME_UNCONFIRMED = "complete_unconfirmed"   # work exists, no verdict → REVIEW flagged
OUTCOME_FAILED = "failed"                      # no work + a real reason (crash/timeout/cap/silent)
OUTCOME_INFRA = "infra"                        # no agent ran (sandbox unavailable, worktree missing)
OUTCOME_TRUNCATED_RESUMABLE = "truncated_resumable"  # output cap hit mid-task + work → resume in place (task #332)

# Exit conditions where no agent process ever ran. Evaluated FIRST: there
# is no agent output to trust and no agent to blame, so escalation must
# not fire — the routing policy retries the infra failure. Names are
# coordinated with the taskit failure taxonomy (tasks/failure_tagger.py)
# and task #328's policy table.
PRE_EXECUTION_CONDITIONS = frozenset({"sandbox_unavailable", "no_worktree", "cli_unavailable"})

_UNCONFIRMED_REVIEWER_NOTE = (
    "agent never confirmed success (no ODIN-STATUS verdict) — judge the diff, "
    "not the missing marker"
)


@dataclass
class RunOutcome:
    """Structured verdict of the evidence ladder.

    Fields:

    - ``outcome`` — one of the ``OUTCOME_*`` constants.
    - ``success`` — True routes the task to REVIEW, False to FAILED.
      ``complete_unconfirmed`` is a success (it has real work to review).
    - ``unconfirmed`` — True only for ``complete_unconfirmed``; the
      orchestrator surfaces ``reviewer_note`` so the reviewer knows the
      verdict was inferred from the diff, not declared by the agent.
    - ``error`` — the true reason string when the run did not succeed;
      None on success.
    - ``signal_source`` — where an explicit verdict came from
      (``"stdout"`` / ``"status_file"`` / ``"mcp"``), else None.
    - ``failure_type`` — feeds the backend classifier
      (``last_failure_type``) so the routing policy picks the right class.
    - ``no_agent_ran`` — True for the INFRA rung; no agent is blamed and
      no escalation fires.
    - ``reviewer_note`` — human-readable flag for the unconfirmed case.
    - ``resumable`` — True only for ``truncated_resumable`` (task #332): the
      run hit the provider output cap mid-task with real work in the
      worktree, so the executor requeues the SAME task into the SAME worktree
      as the next attempt with a host-built resume prompt.
    """

    outcome: str
    success: bool
    unconfirmed: bool = False
    error: Optional[str] = None
    signal_source: Optional[str] = None
    failure_type: Optional[str] = None
    no_agent_ran: bool = False
    reviewer_note: Optional[str] = None
    resumable: bool = False


def _failure_type_for_exit(exit_condition: str) -> str:
    """Map an exit condition to the backend failure_type used by the
    classifier when a run failed with no work and no verdict."""
    if exit_condition == "timeout":
        return "timeout"
    if exit_condition in ("crash", "nonzero"):
        return "agent_execution_failure"
    # A clean exit with no work and no verdict is a silent agent — it
    # returned nothing observable.
    return "silent_hang"


def _default_reason_for_exit(exit_condition: str) -> str:
    """The true, observable reason string for a no-work failure. These are
    statements of fact, never speculation ("Likely...") — the caller
    passes the real stream tail via ``reason_detail`` when it has one."""
    if exit_condition == "timeout":
        return (
            "Agent timed out before producing any worktree changes or an "
            "ODIN-STATUS verdict."
        )
    if exit_condition in ("crash", "nonzero"):
        return (
            "Agent process exited abnormally with no worktree changes."
        )
    return (
        "Agent exited cleanly but produced no worktree changes and no "
        "ODIN-STATUS verdict."
    )


_TRUNCATED_RESUME_REASON = (
    "Agent hit the provider output cap (truncated mid-generation) with work "
    "in progress in the worktree; resuming the same task in the same worktree."
)


def decide_run_outcome(
    *,
    explicit_signal: Optional[bool],
    signal_source: Optional[str],
    has_work: bool,
    exit_condition: str,
    reason_detail: Optional[str] = None,
    truncated: bool = False,
    has_resumable_work: Optional[bool] = None,
) -> RunOutcome:
    """Decide a run's outcome from evidence, not from a stdout marker.

    Parameters
    ----------
    explicit_signal:
        The agent's declared verdict from any channel — True (SUCCESS),
        False (FAILED), or None (no verdict / malformed / missing block).
    signal_source:
        Where ``explicit_signal`` came from (``"stdout"``,
        ``"status_file"``, ``"mcp"``); recorded for the audit trail.
    has_work:
        Whether the worktree shows changes relative to the HEAD recorded
        at run start (see :func:`worktree_has_work_since`).
    exit_condition:
        How the run ended: ``"clean"``, ``"timeout"``, ``"nonzero"``,
        ``"crash"``, or a pre-execution condition
        (``"sandbox_unavailable"``, ``"no_worktree"``, ``"cli_unavailable"``).
    reason_detail:
        The true reason string recovered from the stream / exit, used
        verbatim when the run failed. Falls back to a factual default.
    truncated:
        Whether the provider stream ended at an output-cap finish reason
        (see :func:`finish_reason_is_output_cap`). Task #332: an output cap
        hit mid-task means the work is *unfinished* — with work present the
        run is resumed in place rather than handed to the reviewer half-done.
    has_resumable_work:
        The isolated-worktree work signal
        (:func:`worktree_has_resumable_work`) used ONLY for the truncation
        branch, so a second resume that starts on the first attempt's dirty
        tree still counts as work. Defaults to ``has_work`` when the caller
        does not supply it.

    See the module-level ladder comment for the rung order. The key
    invariant: a missing marker NEVER fails a run on its own — with work
    it becomes an unconfirmed REVIEW; without work the run fails for the
    *exit* reason, not for the absent marker.
    """
    # Rung 0 — pre-execution / no agent ran. Must precede everything: no
    # agent output exists, so signal/worktree reasoning would be noise.
    if exit_condition in PRE_EXECUTION_CONDITIONS:
        return RunOutcome(
            outcome=OUTCOME_INFRA,
            success=False,
            error=reason_detail or f"Pre-execution failure: {exit_condition}",
            failure_type=exit_condition,
            no_agent_ran=True,
        )

    # Rung 1 — an explicit verdict from any channel is the strongest
    # evidence. Believe it. A confirmed-done run is NEVER resumed, even if
    # its trailing stream got cut (the verdict already proves completion).
    if explicit_signal is True:
        return RunOutcome(
            outcome=OUTCOME_SUCCESS,
            success=True,
            signal_source=signal_source,
        )
    if explicit_signal is False:
        return RunOutcome(
            outcome=OUTCOME_FAILED,
            success=False,
            signal_source=signal_source,
            error=reason_detail or "Agent explicitly reported FAILED.",
            failure_type="agent_reported_failed",
        )

    # Rung 1.5 — no verdict, output cap hit mid-task (task #332). If work is
    # in progress in the worktree, resume it in place rather than review a
    # half-finished diff. ``has_resumable_work`` is the isolated-worktree
    # signal so a second resume (which starts on the prior attempt's dirty
    # tree) still counts as work.
    resumable_work = has_resumable_work if has_resumable_work is not None else has_work
    if truncated:
        if resumable_work:
            return RunOutcome(
                outcome=OUTCOME_TRUNCATED_RESUMABLE,
                success=False,
                error=reason_detail or _TRUNCATED_RESUME_REASON,
                failure_type="truncation",
                resumable=True,
            )
        # Truncated but nothing to resume — an honest failure, named
        # truncation (there WAS output, the model was cut off before doing
        # any work) rather than a silent hang.
        return RunOutcome(
            outcome=OUTCOME_FAILED,
            success=False,
            error=reason_detail or (
                "Agent hit the provider output cap before producing any "
                "worktree changes or an ODIN-STATUS verdict."
            ),
            failure_type="truncation",
        )

    # Rung 2 — no verdict, but the worktree has real edits. Do NOT fail on
    # a missing marker: hand the diff to the reviewer, flagged.
    if has_work:
        return RunOutcome(
            outcome=OUTCOME_UNCONFIRMED,
            success=True,
            unconfirmed=True,
            reviewer_note=_UNCONFIRMED_REVIEWER_NOTE,
        )

    # Rung 3 — no verdict AND no work. Now the exit condition names the
    # true reason and the run fails.
    return RunOutcome(
        outcome=OUTCOME_FAILED,
        success=False,
        error=reason_detail or _default_reason_for_exit(exit_condition),
        failure_type=_failure_type_for_exit(exit_condition),
    )


class BaseHarness(ABC):
    """Base class for all agent harnesses.

    Subclasses must implement execute() and is_available().
    Optionally override execute_streaming() and execute_conversation_turn()
    for interactive plan mode.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._current_pid: Optional[int] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Harness display name."""
        ...

    @abstractmethod
    async def execute(self, prompt: str, context: dict) -> TaskResult:
        """Execute a task prompt and return result."""
        ...

    async def execute_streaming(self, prompt: str, context: dict) -> AsyncIterator[str]:
        """Execute and yield output chunks as they arrive.

        Default: calls execute() and yields the full output.
        CLI harnesses override to stream subprocess stdout line-by-line.
        """
        result = await self.execute(prompt, context)
        if result.output:
            yield result.output

    async def execute_conversation_turn(
        self, messages: List[Dict[str, str]], context: dict
    ) -> AsyncIterator[str]:
        """Execute a multi-turn conversation and yield output chunks.

        Default: flattens messages into a single prompt and calls
        execute_streaming(). API harnesses override to pass the full
        messages array.
        """
        # Flatten messages into a single prompt
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(content)
        prompt = "\n\n".join(parts)
        async for chunk in self.execute_streaming(prompt, context):
            yield chunk

    def build_execute_command(self, prompt: str, context: dict) -> Optional[List[str]]:
        """Build CLI command list for one-shot task execution.

        Returns None for API harnesses (they don't have a CLI command).
        CLI harnesses return the full command with prompt included.
        """
        return None

    def build_interactive_command(self, system_prompt_file: str, context: dict) -> Optional[List[str]]:
        """Build command for interactive (non-one-shot) CLI mode.

        Args:
            system_prompt_file: Path to a file containing the system prompt.
                Using a file avoids shell escaping issues with long prompts.
            context: Execution context (working_dir, model, etc.)

        Returns None for API harnesses (they can't run interactively in tmux).
        CLI harnesses return the command without -p flag.
        """
        return None

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this harness CLI/API is reachable."""
        ...


async def terminate_subprocess(proc: asyncio.subprocess.Process) -> None:
    """Best-effort subprocess termination after timeout.

    Kills the process and waits for it to exit. Tolerates all errors
    because this runs in the failure path — a cleanup failure must not
    mask the original timeout/cancellation. The process may have already
    exited (so kill raises ProcessLookupError) or the OS may refuse the
    signal; in either case we want to continue, not re-raise.
    """
    try:
        proc.kill()
    except Exception:
        pass
    try:
        await proc.wait()
    except Exception:
        pass


async def read_with_tee(
    proc: asyncio.subprocess.Process, output_file: str
) -> str:
    """Read stdout line-by-line, writing each line to output_file and accumulating.

    Used by CLI harnesses when context["output_file"] is set to provide
    tail -f style observability into running tasks.
    """
    lines = []
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            lines.append(line)
            f.write(line)
            f.flush()
    return "".join(lines)


async def read_with_trace(
    proc: asyncio.subprocess.Process,
    output_file: str,
    trace_file: str,
) -> str:
    """Read stdout, writing raw JSON to trace_file and extracted text to output_file.

    The trace_file gets the raw stream-json output (the full execution trace).
    The output_file gets extracted plain text for backward compat with `odin tail`.
    Returns the extracted plain-text output.
    """
    raw_lines: list[str] = []
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(trace_file).parent.mkdir(parents=True, exist_ok=True)

    with open(trace_file, "w") as tf, open(output_file, "w") as of:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            raw_lines.append(line)
            # Write raw JSON line to trace file
            tf.write(line)
            tf.flush()
            # Extract text content and write to output file for tail -f
            text = extract_text_from_line(line)
            if text:
                of.write(text)
                of.flush()

    raw_output = "".join(raw_lines)
    return extract_text_from_stream(raw_output)


def extract_text_from_line(line: str) -> str:
    """Extract displayable text content from a single JSON line.

    Handles both stream-json (Claude/Gemini) and opencode/kilo JSON formats.
    Returns empty string if the line has no text content.
    """
    stripped = line.strip()
    if not stripped:
        return ""
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — pass through as plain text
        return line

    # Claude stream-json: {"type": "content_block_delta", "delta": {"text": "..."}}
    if obj.get("type") == "content_block_delta":
        delta = obj.get("delta", {})
        return delta.get("text", "")

    # Claude stream-json: {"type": "result", "result": "..."}
    if obj.get("type") == "result":
        result = obj.get("result", "")
        if isinstance(result, str):
            return result

    # Gemini/GLM/MiniMax stream-json: {"type": "text", "text": "..."}
    if obj.get("type") == "text":
        # opencode/kilo JSON: {"type": "text", "content": "..."}
        if "content" in obj:
            return obj.get("content", "")
        return obj.get("text", "")

    # Gemini CLI: {"type": "message", "role": "assistant", "content": "..."}
    if obj.get("type") == "message" and obj.get("role") == "assistant":
        return obj.get("content", "")

    # Assistant format: {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}
    if obj.get("type") == "assistant":
        msg = obj.get("message", {})
        if isinstance(msg, dict):
            parts = []
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append(text)
            return "".join(parts)

    # opencode/kilo: {"type": "step_finish", "content": "..."}
    if obj.get("type") == "step_finish":
        return obj.get("content", "")

    # Codex CLI: {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
    if obj.get("type") == "item.completed":
        item = obj.get("item", {})
        if isinstance(item, dict) and item.get("type") == "agent_message":
            return item.get("text", "")
        return ""

    # Codex --json wrapper: {"id":"0","msg":{"type":"agent_message","message":"..."}}
    msg = obj.get("msg")
    if isinstance(msg, dict) and "id" in obj and "type" not in obj:
        if msg.get("type") == "agent_message":
            return msg.get("message", "")
        return ""

    return ""


def extract_token_usage(raw_output: str) -> dict:
    """Extract token usage from any harness's raw stream/JSONL output.

    Every CLI reports tokens differently. This one function understands them
    all and returns the uniform shape claude has always used::

        {input_tokens, output_tokens, cache_read_tokens,
         cache_write_tokens, total_tokens}

    Formats handled (mirrors the backend's ``extract_agent_text`` so the odin
    harness layer and the taskit ingestion layer agree on token counts):

    - **claude** — ``modelUsage`` aggregate: ``{"modelUsage":{model:{"inputTokens":N,
      "outputTokens":M,"cacheReadInputTokens":X,"cacheCreationInputTokens":Y}}}``
    - **glm / minimax** (opencode/kilo) — ``step_finish`` events:
      ``{"type":"step_finish","part":{"tokens":{"input":N,"output":M,"total":T,
      "cache":{"read":X,"write":Y}}}}`` (summed across steps; ``total`` is kept
      verbatim because reasoning tokens push it above input+output)
    - **gemini** — ``result`` stats: ``{"type":"result","stats":{"total_tokens":T,
      "input_tokens":N,"output_tokens":M}}``
    - **qwen** — ``result`` usage: ``{"type":"result","usage":{"total_tokens":T,...}}``
    - **codex** — ``turn.completed``: ``{"type":"turn.completed","usage":{"input_tokens":N,
      "output_tokens":M}}`` and the older wrapped form
      ``{"id":"0","msg":{"type":"token_count","input_tokens":N,"output_tokens":M}}``

    An authoritative aggregate event (modelUsage, gemini/qwen result, codex
    usage) wins over accumulated ``step_finish`` steps. Returns ``{}`` when no
    token data is present.
    """
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    # An explicit total from a format that reports one (step_finish total,
    # gemini/qwen total_tokens). None means "derive as input + output".
    explicit_total = None
    # Once an authoritative aggregate event is seen, ignore step_finish steps.
    aggregate_found = False

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue

        # Codex --json wraps events: {"id":"0","msg":{"type":"token_count",...}}
        msg = obj.get("msg")
        if isinstance(msg, dict) and "id" in obj and "type" not in obj:
            if msg.get("type") == "token_count":
                inp = msg.get("input_tokens", 0) or 0
                out = msg.get("output_tokens", 0) or 0
                if inp or out:
                    totals["input_tokens"] = inp
                    totals["output_tokens"] = out
                    explicit_total = None
                    aggregate_found = True
            continue

        event_type = obj.get("type")

        # Claude Code: aggregate counts in a modelUsage event.
        model_usage = obj.get("modelUsage")
        if isinstance(model_usage, dict):
            agg_in = agg_out = agg_read = agg_write = 0
            for model_data in model_usage.values():
                if isinstance(model_data, dict):
                    agg_in += model_data.get("inputTokens", 0)
                    agg_out += model_data.get("outputTokens", 0)
                    agg_read += model_data.get("cacheReadInputTokens", 0)
                    agg_write += model_data.get("cacheCreationInputTokens", 0)
            if agg_in or agg_out:
                totals = {
                    "input_tokens": agg_in,
                    "output_tokens": agg_out,
                    "cache_read_tokens": agg_read,
                    "cache_write_tokens": agg_write,
                }
                explicit_total = None
                aggregate_found = True
            if not event_type:
                continue

        # opencode / kilo (glm, minimax, claude fallback): step_finish tokens.
        if event_type == "step_finish":
            tokens = (obj.get("part") or {}).get("tokens") or (obj.get("result") or {}).get("tokens") or {}
            if isinstance(tokens, dict) and tokens and not aggregate_found:
                totals["input_tokens"] += tokens.get("input", 0) or 0
                totals["output_tokens"] += tokens.get("output", 0) or 0
                cache = tokens.get("cache") or {}
                totals["cache_read_tokens"] += cache.get("read", 0) or 0
                totals["cache_write_tokens"] += cache.get("write", 0) or 0
                if tokens.get("total"):
                    explicit_total = (explicit_total or 0) + (tokens.get("total") or 0)
            continue

        # Gemini result.stats and Qwen result.usage.
        if event_type == "result":
            stats = obj.get("stats") or {}
            if isinstance(stats, dict) and stats.get("total_tokens"):
                totals["input_tokens"] = stats.get("input_tokens", 0) or 0
                totals["output_tokens"] = stats.get("output_tokens", 0) or 0
                explicit_total = stats.get("total_tokens")
                aggregate_found = True
            usage = obj.get("usage") or {}
            if isinstance(usage, dict) and usage.get("total_tokens"):
                totals["input_tokens"] = usage.get("input_tokens", 0) or 0
                totals["output_tokens"] = usage.get("output_tokens", 0) or 0
                totals["cache_read_tokens"] = (
                    usage.get("cache_read_input_tokens", 0)
                    or usage.get("cache_read_tokens", 0) or 0
                )
                explicit_total = usage.get("total_tokens")
                aggregate_found = True
            continue

        # Codex JSONL: turn.completed carries the final usage.
        if event_type == "turn.completed":
            usage = obj.get("usage") or {}
            if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
                totals["input_tokens"] = usage.get("input_tokens", 0) or 0
                totals["output_tokens"] = usage.get("output_tokens", 0) or 0
                explicit_total = None
                aggregate_found = True
            continue

    total = explicit_total if explicit_total is not None else totals["input_tokens"] + totals["output_tokens"]
    totals["total_tokens"] = total
    return totals if total > 0 else {}


def stream_json_is_complete(output_tail: str) -> bool:
    """Check if a stream-json output tail contains a completion signal.

    Scans the last few lines for a JSON object with ``{"type":"result",...}``
    which CLI agents (Claude, Gemini, etc.) emit when the task finishes.
    Also detects error-type results as completion.

    Args:
        output_tail: The last N bytes/chars of the output file (typically 4-8 KiB).

    Returns:
        True if a result line is found, meaning the agent has finished.
    """
    for line in reversed(output_tail.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return True
    return False


def extract_text_from_stream(raw_output: str) -> str:
    """Extract all plain-text content from a raw stream-json output.

    Parses each line and concatenates the text fragments.
    Falls back to returning the raw output if no JSON is detected.
    """
    if not raw_output.strip():
        return ""

    # Detect a JSON stream anywhere in the head of the output — CLIs
    # prepend plain-text banners (codex prints "Reading additional input
    # from stdin..." when the prompt arrives via stdin), so judging only
    # the FIRST non-empty line silently returned raw JSONL framing and
    # broke every codex reflection (verdict parser saw JSON, not text).
    has_json_line = any(
        line.strip().startswith("{")
        for line in raw_output.splitlines()[:20]
        if line.strip()
    )
    if not has_json_line:
        # Genuinely plain text — return as-is.
        return raw_output

    text_parts: list[str] = []
    for line in raw_output.splitlines():
        text = extract_text_from_line(line)
        if text:
            text_parts.append(text)

    return "".join(text_parts) if text_parts else raw_output


# Provider finish-reason values that indicate the run hit an output cap rather
# than ending normally. Used to label truncation in the failure note.
_OUTPUT_CAP_REASONS = frozenset({
    "length", "max_tokens", "max-tokens", "max_turns", "error_max_turns",
})


def finish_reason_is_output_cap(reason: Optional[str]) -> bool:
    """True when a provider finish reason means the run hit its output cap.

    The single source of truth for "did the model get cut off mid-generation"
    — used by the orchestrator to feed ``decide_run_outcome(truncated=...)``
    (task #332) and shared with the truncation note formatter so the
    resume path and the failure note agree on what counts as a cap hit.
    """
    return bool(reason) and reason in _OUTPUT_CAP_REASONS


def extract_stream_summary(raw_output: str) -> dict:
    """Extract run-close metadata from a raw JSONL agent stream.

    Tolerant across harness formats (claude, opencode/glm/minimax, gemini,
    codex). Never raises — a missing field is simply absent. Returns::

        {
          "finish_reason": str | None,      # terminal stop reason
          "output_tokens": int | None,
          "input_tokens": int | None,
          "total_tokens": int | None,
          "last_event_timestamp": str | None,  # ISO-8601 (UTC)
          "error_events": list[str],           # recognizable error fragments
          "result_emitted": bool,              # a completion event appeared
        }

    The last ``step_finish``/``message_delta``/``result`` reason wins, so the
    terminal reason reflects how the run actually ended.
    """
    summary: Dict[str, Any] = {
        "finish_reason": None,
        "output_tokens": None,
        "input_tokens": None,
        "total_tokens": None,
        "last_event_timestamp": None,
        "error_events": [],
        "result_emitted": False,
    }
    if not raw_output or not raw_output.strip():
        return summary

    usage = extract_token_usage(raw_output)
    if usage:
        summary["input_tokens"] = usage.get("input_tokens")
        summary["output_tokens"] = usage.get("output_tokens")
        summary["total_tokens"] = usage.get("total_tokens")

    last_raw_ts: Optional[float] = None
    stop_reason: Optional[str] = None
    fallback_reason: Optional[str] = None
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue

        event_type = obj.get("type")
        ts = obj.get("timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            last_raw_ts = ts

        part = obj.get("part") if isinstance(obj.get("part"), dict) else None

        # opencode / kilo (glm, minimax): step_finish.part.reason is the
        # terminal reason for that step; the LAST step_finish wins.
        if event_type == "step_finish":
            reason = (part or {}).get("reason") or obj.get("reason")
            if isinstance(reason, str) and reason:
                stop_reason = reason

        # Claude / gemini / qwen: result event marks completion. An explicit
        # stop_reason is authoritative; subtype (e.g. "success") is only a
        # fallback so it never masks a real stop reason like "max_tokens".
        if event_type == "result":
            summary["result_emitted"] = True
            sr = obj.get("stop_reason")
            if isinstance(sr, str) and sr:
                stop_reason = sr
            elif isinstance(obj.get("subtype"), str) and obj["subtype"]:
                fallback_reason = obj["subtype"]

        # Claude stream-json: message_delta carries delta.stop_reason.
        if event_type == "message_delta":
            delta = obj.get("delta")
            sr = delta.get("stop_reason") if isinstance(delta, dict) else None
            if isinstance(sr, str) and sr:
                stop_reason = sr

        # Codex turn.completed marks completion.
        if event_type == "turn.completed":
            summary["result_emitted"] = True

        # Error events — surface recognizable failures.
        if event_type == "error":
            msg = obj.get("message") or obj.get("error") or obj.get("text")
            if msg:
                summary["error_events"].append(str(msg)[:300])
        if obj.get("is_error") is True:
            err = obj.get("result") or obj.get("error") or obj.get("message")
            if err and str(err) not in summary["error_events"]:
                summary["error_events"].append(str(err)[:300])

    summary["finish_reason"] = stop_reason or fallback_reason
    if last_raw_ts is not None:
        summary["last_event_timestamp"] = _ts_to_iso(last_raw_ts)

    return summary


def _ts_to_iso(ts: float) -> Optional[str]:
    """Convert an epoch-ms or epoch-s timestamp to ISO-8601 UTC, or None."""
    try:
        from datetime import datetime, timezone

        divisor = 1000 if ts > 1e12 else 1
        return datetime.fromtimestamp(ts / divisor, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def format_stream_summary_note(summary: dict) -> str:
    """Turn a stream summary into a concise human-readable note.

    Empty string when there is nothing to report (no finish reason). The note
    is appended to a generic truncation error so the failure record names the
    *real* reason ("provider hit output cap at 40 output tokens") instead of a
    guess.
    """
    if not summary:
        return ""
    reason = summary.get("finish_reason")
    if not reason:
        return ""
    out_tok = summary.get("output_tokens")
    in_tok = summary.get("input_tokens")
    label = " (likely output cap)" if reason in _OUTPUT_CAP_REASONS else ""
    parts = [f'Provider stream ended at finish_reason="{reason}"{label}']
    if out_tok is not None:
        token_bits = [f"{int(out_tok):,} output tokens"]
        if in_tok is not None:
            token_bits.append(f"{int(in_tok):,} input")
        parts.append("with " + " · ".join(token_bits))
    return ". ".join(parts) + "."


# ── Host-built resume prompt (task #332) ─────────────────────────────
#
# When a run truncates at the output cap with work in the worktree, the next
# attempt resumes IN PLACE. The resume prompt is rebuilt entirely host-side
# from durable pieces — the brief (the task description, injected by the
# caller), a git status/diff summary of the worktree, and the tail of the
# previous attempt's trace — plus the standing instruction to verify the
# working state first. Nothing here reads the provider session, so ANY agent
# can resume a run another agent started.


def summarize_worktree_state(worktree_path: Optional[str], *, limit: int = 2000) -> str:
    """Return a compact git status/diff summary of a worktree, or "".

    Lists the changed / untracked files and a diffstat so a resuming agent
    can see what the prior attempt left mid-flight. A clean tree returns the
    empty string. Never raises — a git hiccup must not crash the resume
    (see docs/patterns/bookkeeping-never-kills-the-run)."""
    if not worktree_path:
        return ""
    try:
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        diffstat = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    parts: list[str] = []
    if status.returncode == 0 and status.stdout.strip():
        parts.append("Changed files:\n" + status.stdout.strip()[:limit])
    if diffstat.returncode == 0 and diffstat.stdout.strip():
        parts.append("Diff stat:\n" + diffstat.stdout.strip()[:limit])
    return "\n\n".join(parts)


def build_resume_prompt(
    *, resume_count: int, worktree_state: str, trace_tail: str
) -> str:
    """Assemble the resume-prompt block from durable pieces.

    Pure formatting — the caller supplies the brief separately (it stays the
    task description) and the two reconstructed pieces here. Returns "" when
    there is nothing to resume from (no worktree state AND no trace tail), so
    a fresh start never gets a misleading resume header stapled on."""
    worktree_state = (worktree_state or "").strip()
    trace_tail = (trace_tail or "").strip()
    if not worktree_state and not trace_tail:
        return ""
    parts = [
        f"## RESUMING A TRUNCATED ATTEMPT (resume {resume_count})",
        (
            "A previous attempt on this task hit the model's output cap and "
            "stopped mid-work. Its changes are preserved in this same "
            "worktree. **Verify the working state first — a previous attempt "
            "may have stopped mid-edit** (a half-written file, an uncommitted "
            "change, a partial refactor). Reconcile what's there, then finish "
            "the remaining work and end with the ODIN-STATUS block as usual. "
            "Do not restart from scratch — build on the work already here."
        ),
    ]
    if worktree_state:
        parts.append("### Worktree state (git)\n" + worktree_state)
    if trace_tail:
        parts.append(
            "### Tail of the previous attempt's trace\n```\n" + trace_tail + "\n```"
        )
    return "\n\n".join(parts)
