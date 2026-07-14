"""Spec-branch verify gate — async post-merge suite runner (W5 task #208).

Background — why this module exists
-----------------------------------
A clean merge after a passed review IS done — but two individually-green
tasks can still break each other after merging (live case: two tasks
minted the same Django migration number; each branch was green, the
combination broke the suite).  With promote-check retired (W5 task
#205), this gate is the only place suites still run after a merge:
once per landed merge, against the spec branch in a fresh temp
worktree, posted async so it doesn't block the next merge.

The contract (:func:`enqueue_spec_verify`) is called from
:func:`tasks.dag_executor.merge_task_on_reflection` after every
successful merge.  What happens from there:

  1. The merge path records the new spec-branch HEAD SHA and the
     merging branch on the spec's coordinator state.  If a verify
     runner is already in flight, **nothing is spawned** — the
     trailing-edge loop in the running thread will pick up the new
     HEAD when the current run completes.
  2. If no runner is in flight, a daemon thread starts ``verify.sh``
     against the spec branch in a fresh temp worktree (never the
     operator's main checkout — that's the W4 regression this gate
     was created to fix).  The merge path returns immediately; the
     next merge can land on the spec branch without waiting.
  3. When the run completes, the result is published:
     - **GREEN** → at most one task comment (a one-line "verified"
       stamp); no spec-level noise.  The spec metadata records
       ``verify_status="green"`` and ``verify_head_sha=<sha>`` so the
       board can show when the last successful verify ran.
     - **RED or ENV_MISSING** → one spec-level comment naming the
       failing suite(s), the merge task/branch/SHA that turned it
       red, and the log path.  ``spec.metadata.verify_status="red"``
       and ``verify_failed_suites`` are set so the operator sees it
       on the board.  The merge is NOT auto-reverted — the spec
       branch stays at the failed HEAD until the operator decides
       (root system principle: signal, not action).
  4. After publishing, the coordinator checks ``pending_sha`` — if a
     newer HEAD landed during the run, it runs the verify loop once
     more against the latest SHA.  The loop then idles; the next
     :func:`enqueue_spec_verify` call wakes it.

Why a thread (not a celery task): the run is sub-minute on this
machine and the alternative is a Celery worker bouncing on the same
loop.  Threads also keep state in-process (the per-spec coalescing
window is narrow — minutes at most).  Daemon threads never block
process shutdown.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("taskit.spec_verify")

_GATE_AUTHOR_EMAIL = "verify-gate@odin"
_GATE_AUTHOR_LABEL = "spec-verify-gate"
_TASK_AUTHOR_EMAIL = "verify-gate@odin"
_TASK_AUTHOR_LABEL = "spec-verify-gate"

# Wall-clock ceiling for a single verify.sh run.  The whole script
# typically completes under 5 min on this machine; 30 min covers a
# cold-start of all four suites plus any frontend-deps provisioning.
_RUN_TIMEOUT_SECONDS = 30 * 60

# Suite names verify.sh knows about.  Used to label the row in the
# spec-level comment when reading ``_results.txt``.
_KNOWN_SUITES = ("odin", "backend", "frontend", "snapshots")


@dataclass
class SuiteResult:
    """One row from verify.sh's summary table."""

    name: str          # "odin" | "backend" | "frontend" | "snapshots"
    verdict: str       # "PASS" | "FAIL" | "ENV_MISSING"
    tests: str = ""    # "1 failed, 38 passed"
    seconds: str = ""  # "12.34"

    @classmethod
    def coerce(cls, value) -> "SuiteResult":
        """Accept either a ``SuiteResult`` or a duck-typed dict.

        Tests pass plain dicts because they're shorter to construct;
        ``VerifyReport`` calls this on every incoming row so the
        downstream list-comprehensions can rely on attribute access.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                name=value.get("name", ""),
                verdict=value.get("verdict", ""),
                tests=value.get("tests", ""),
                seconds=value.get("seconds", ""),
            )
        return cls(name=str(value), verdict="FAIL")

    @property
    def is_red(self) -> bool:
        return self.verdict in ("FAIL", "ENV_MISSING")


@dataclass
class VerifyReport:
    """Outcome of a single ``sh scripts/verify.sh`` run against the spec branch."""

    head_sha: str
    spec_branch: str
    status: str                                # "green" | "red" | "env_missing"
    suites: List[SuiteResult] = field(default_factory=list)
    log_path: str = ""
    duration_s: float = 0.0
    run_at: float = 0.0
    error: str = ""

    def __post_init__(self) -> None:
        # Coerce every incoming row to a SuiteResult.  Tests pass dicts
        # (``{"name": "backend", "verdict": "FAIL"}``); production
        # passes SuiteResults.  Either path is fine — the rest of the
        # module only relies on attribute access.
        self.suites = [SuiteResult.coerce(s) for s in (self.suites or [])]

    @property
    def failed_suites(self) -> List[SuiteResult]:
        return [s for s in self.suites if s.is_red]

    @property
    def failed_suite_names(self) -> List[str]:
        return [s.name for s in self.failed_suites]

    @property
    def is_red(self) -> bool:
        return self.status in ("red", "env_missing")


class SpecVerifyCoordinator:
    """Per-spec state for coalescing + thread management.

    Lives in module-scope (``_coordinator``) — the in-process state is
    exactly what ``enqueue_spec_verify`` and ``_runner_loop`` share.
    :meth:`reset_for_tests` is the escape hatch for the test suite.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # spec_id -> {"last_verified_head": str|None, "pending_sha": str|None,
        #             "pending_branch": str|None, "running": bool}
        self._state: Dict[str, Dict[str, object]] = {}
        # Thread handle for the currently-active runner, so we don't
        # spawn duplicates.  Tests reach in via ``reset_for_tests``.
        self._threads: Dict[str, threading.Thread] = {}

    def _slot(self, spec_id: str) -> Dict[str, object]:
        slot = self._state.get(spec_id)
        if slot is None:
            slot = {
                "last_verified_head": None,
                "pending_sha": None,
                "pending_branch": None,
                "running": False,
            }
            self._state[spec_id] = slot
        return slot

    def enqueue(self, spec_id: str, head_sha: str, merge_branch: str) -> None:
        """Record a new merge → start a runner if none is active.

        Called from :func:`tasks.dag_executor.merge_task_on_reflection`
        after a successful merge.  Cheap — never blocks the caller;
        the actual ``verify.sh`` invocation happens on a daemon thread.
        """
        with self._lock:
            slot = self._slot(spec_id)
            slot["pending_sha"] = head_sha
            slot["pending_branch"] = merge_branch
            if slot["running"]:
                logger.info(
                    "[spec:%s] verify coalesced onto in-flight run (pending=%s)",
                    spec_id, head_sha,
                )
                return
            slot["running"] = True
            thread = threading.Thread(
                target=_runner_loop,
                args=(self, spec_id, head_sha, merge_branch),
                daemon=True,
                name=f"spec-verify-{spec_id}",
            )
            self._threads[spec_id] = thread
            thread.start()

    def mark_finished(self, spec_id: str, verified_head: str) -> None:
        """Called by the runner after it publishes a result for ``verified_head``.

        Records the SHA so a duplicate ``enqueue`` for the same HEAD is
        recognised as already-verified, and clears the running flag so
        the trailing-edge loop can decide whether to spin up another
        run for any SHA that landed during this one.
        """
        with self._lock:
            slot = self._slot(spec_id)
            slot["running"] = False
            slot["last_verified_head"] = verified_head
            self._threads.pop(spec_id, None)

    def maybe_reschedule(self, spec_id: str) -> bool:
        """If a newer HEAD landed during the just-completed run, return True
        and consume the pending SHA so the caller can spawn a fresh
        runner.  Returns False when there's nothing to do.
        """
        with self._lock:
            slot = self._slot(spec_id)
            if slot["running"]:
                return False
            pending = slot.get("pending_sha")
            last = slot.get("last_verified_head")
            if pending and pending != last:
                new_head = str(pending)
                new_branch = str(slot.get("pending_branch") or "")
                slot["pending_sha"] = None
                slot["pending_branch"] = None
                slot["running"] = True
                thread = threading.Thread(
                    target=_runner_loop,
                    args=(self, spec_id, new_head, new_branch),
                    daemon=True,
                    name=f"spec-verify-{spec_id}-trailing",
                )
                self._threads[spec_id] = thread
                thread.start()
                return True
            slot["pending_sha"] = None
            slot["pending_branch"] = None
            return False

    def _state_for_tests(self, spec_id: str) -> Dict[str, object]:
        """Read-only snapshot for assertions.  Not part of the public API."""
        with self._lock:
            slot = self._slot(spec_id)
            return {
                "running": slot["running"],
                "pending_sha": slot.get("pending_sha"),
                "last_verified_head": slot.get("last_verified_head"),
            }

    def reset_for_tests(self) -> None:
        """Clear all per-spec state.  Tests call this in tearDown."""
        with self._lock:
            self._state.clear()
            self._threads.clear()

    def _reset_for_tests(self) -> None:
        """Alias used by tests that reach in via the private name."""
        self.reset_for_tests()


_coordinator = SpecVerifyCoordinator()


def enqueue_spec_verify(
    *,
    spec_id: str,
    head_sha: str,
    merge_branch: str,
) -> None:
    """Public entry point — called from `merge_task_on_reflection`.

    ``spec_id`` is the spec's odin_id (e.g. ``"sp_fable_w5"``).
    ``head_sha`` is the SHA the spec branch now points at after the
    merge — used to disambiguate coalesced runs and to name the SHA
    in a future red-run spec comment.
    ``merge_branch`` is the task branch that merged (e.g.
    ``"task/sp_fable_w5/210"``) — used to name the merge in the
    same future comment.

    This function returns immediately; the verify runs on a daemon
    thread.
    """
    if not spec_id or not head_sha:
        # Defensive: a merge_task_on_reflection caller that forgets to
        # pass a SHA should never spawn a runner against an empty
        # SHA.  Log and bail rather than running a no-context verify.
        logger.warning(
            "enqueue_spec_verify called with missing fields: spec_id=%r head_sha=%r",
            spec_id, head_sha,
        )
        return
    _coordinator.enqueue(spec_id, head_sha, merge_branch)


def run_spec_verify_blocking(
    *,
    spec_id: str,
    head_sha: str,
    merge_branch: str,
) -> VerifyReport:
    """Run one verify pass synchronously, publishing the result.

    Unlike the normal :func:`enqueue_spec_verify` path, this function
    does NOT spawn a thread — it runs ``_execute_verify`` inline and
    then publishes the report through :func:`_publish_report`.  Used
    by tests (so failures can be inspected immediately) and as a
    building block for the runner thread.

    Returns the published :class:`VerifyReport` so callers (and tests)
    can assert on the result inline.
    """
    base_dir = _make_temp_spec_worktree(spec_id, head_sha)
    try:
        report = _execute_verify(
            spec_id=spec_id,
            head_sha=head_sha,
            merge_branch=merge_branch,
            base_dir=base_dir,
        )
        _publish_report(
            spec_id=spec_id,
            head_sha=head_sha,
            merge_branch=merge_branch,
            report=report,
        )
        return report
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


# ----------------------------------------------------------------------------
# Runner thread
# ----------------------------------------------------------------------------


def _runner_loop(
    coordinator: SpecVerifyCoordinator,
    spec_id: str,
    head_sha: str,
    merge_branch: str,
) -> None:
    """Daemon-thread entry point.  Runs verify.sh, publishes, then
    drains any newer pending SHA before exiting.
    """
    try:
        report = _execute_verify(
            spec_id=spec_id,
            head_sha=head_sha,
            merge_branch=merge_branch,
        )
        _publish_report(
            spec_id=spec_id,
            head_sha=head_sha,
            merge_branch=merge_branch,
            report=report,
        )
        logger.info(
            "[spec:%s] verify run complete status=%s log=%s",
            spec_id, report.status, report.log_path,
        )
    except Exception as exc:
        import traceback
        logger.error(
            "[spec:%s] verify runner crashed: %s\n%s",
            spec_id, exc, traceback.format_exc(),
        )
        # Error ledger (task #222): the spec-branch verify gate crashed
        # — capture so the operator triages the underlying infra failure
        # (disk full, subprocess crash, etc.) instead of rediscovering it
        # from a red spec comment.
        try:
            from .errors import record_gate_crash
            spec = _spec_from_id(spec_id)
            log_tail = ""
            try:
                log_tail = traceback.format_exc(limit=8)
            except Exception:
                log_tail = ""
            record_gate_crash(
                spec=spec,
                symptom=f"verify runner crashed: {type(exc).__name__}: {exc}",
                log_path=str(Path(tempfile.gettempdir()) / f"spec-verify-{spec_id}.log"),
                log_tail=log_tail[:2000],
                head_sha=head_sha,
            )
        except Exception:
            logger.exception(
                "[spec:%s] error ledger: failed to record gate_crash",
                spec_id,
            )
        # Even on crash we must release the slot — otherwise the
        # spec is wedged forever and every subsequent enqueue is
        # dropped on the floor as "coalesced".
    finally:
        coordinator.mark_finished(spec_id, head_sha)
        # Drain trailing merges — run once more if a newer SHA
        # landed during this run, then idle.
        try:
            while coordinator.maybe_reschedule(spec_id):
                # The trailing run spawns its own thread; we just
                # yield until it lands.  ``mark_finished`` will be
                # called by the trailing thread, so the loop body
                # here is essentially "yield until the trailing run
                # finishes", but we don't want to busy-wait the CPU —
                # back off briefly between checks.
                time.sleep(0.05)
        except Exception:
            logger.exception("[spec:%s] trailing-drain crashed", spec_id)


# ----------------------------------------------------------------------------
# Execution — actually run verify.sh
# ----------------------------------------------------------------------------


def _project_root() -> str:
    """Resolve the git project root (where scripts/verify.sh lives).

    In production this is the executor's CWD (the operator's main
    checkout).  Tests patch this to a temp git checkout.
    """
    return os.environ.get("VERIFY_PROJECT_ROOT", os.getcwd())


def _make_temp_spec_worktree(spec_id: str, head_sha: str) -> str:
    """Create a fresh temporary git worktree pointing at ``spec/<spec_id>``.

    CRITICAL: this worktree is OUTSIDE the operator's main checkout.
    verify.sh itself runs ``git rev-parse``/``git worktree`` and a
    verify run inside the operator's checkout would create a nested
    worktree that races with the operator's own commands — exactly
    the phantom-gap shape from W4.  Fresh temp dir, fresh worktree,
    one use, deleted on ``TemporaryDirectory`` cleanup.
    """
    project_root = _project_root()
    base = Path(tempfile.mkdtemp(prefix=f"spec-verify-wt-{spec_id}-"))
    cmd = [
        "git", "-C", project_root, "worktree", "add",
        "--detach", str(base), f"spec/{spec_id}",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        raise RuntimeError(
            f"Failed to create spec worktree at {base} from "
            f"spec/{spec_id}: {result.stderr.strip()}"
        )
    return str(base)


def _execute_verify(
    *,
    spec_id: str,
    head_sha: str,
    merge_branch: str,
    base_dir: Optional[str] = None,
) -> VerifyReport:
    """Run ``sh scripts/verify.sh`` in a fresh temp worktree and parse the
    result.

    ``base_dir`` is normally ``None`` — the function creates a fresh
    temp git worktree pointing at the spec branch and runs the suite
    inside it.  Tests pass an explicit ``base_dir`` to bypass the
    git-worktree step (which depends on a real repo) and exercise the
    downstream ``verify.sh`` runner in isolation.

    Returns a :class:`VerifyReport` with status ``green`` if every
    suite passed, ``red`` if any suite failed (FAIL) or went
    env-missing, and ``error`` set if the run itself couldn't start.
    Always writes a log under ``base_dir/.spec-verify.log`` so the
    spec-level comment can point at it.
    """
    started = time.time()
    spec_branch = f"spec/{spec_id}"
    owns_base_dir = base_dir is None
    if owns_base_dir:
        base_dir = _make_temp_spec_worktree(spec_id, head_sha)
    log_path = os.path.join(base_dir, ".spec-verify.log")

    try:
        suites, rc, error = _run_verify_subprocess(base_dir, log_path)
    finally:
        if owns_base_dir:
            shutil.rmtree(base_dir, ignore_errors=True)

    red_suites = [s for s in suites if s.is_red]
    env_missing = [s for s in suites if s.verdict == "ENV_MISSING"]
    if env_missing:
        status = "env_missing"
    elif red_suites or rc != 0:
        status = "red"
    elif error:
        status = "red"
    else:
        status = "green"

    return VerifyReport(
        head_sha=head_sha,
        spec_branch=spec_branch,
        status=status,
        suites=suites,
        log_path=log_path,
        duration_s=time.time() - started,
        run_at=started,
        error=error,
    )


def _run_verify_subprocess(
    base_dir: str, log_path: str
) -> tuple:
    """Invoke ``sh scripts/verify.sh`` in ``base_dir`` and parse its result.

    Returns ``(suites, returncode, error_string)``.  ``error_string``
    is empty for a clean run (PASS suites, exit 0); populated when
    the run couldn't start (no ``scripts/verify.sh`` on disk — the
    test-mode fast path; subprocess crashes; or timeout).
    """
    suites: List[SuiteResult] = []
    error = ""
    rc = 0

    if not Path(base_dir, "scripts/verify.sh").exists():
        # Test-mode: there is no real verify.sh, so return a synthetic
        # GREEN run.  Production always has the script (this branch
        # only fires when the temp worktree can't be checked out for
        # some other reason, in which case returning GREEN would lie
        # — instead, surface a FAIL so the operator knows the gate
        # couldn't actually run, and the run log explains why).
        Path(log_path).write_text(
            "[spec-verify] scripts/verify.sh missing from worktree — gate "
            "could not run; treating as RED so the operator is not lied to.\n"
        )
        return (
            [SuiteResult(name="verify", verdict="FAIL", seconds="0")],
            1,
            "scripts/verify.sh missing from worktree",
        )

    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(
                ["sh", "scripts/verify.sh"],
                cwd=base_dir,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=_RUN_TIMEOUT_SECONDS,
            )
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        Path(log_path).write_text(
            f"[spec-verify] TIMEOUT after {_RUN_TIMEOUT_SECONDS}s\n"
        )
        return (
            [SuiteResult(name="verify", verdict="FAIL", seconds="timeout")],
            1,
            "verify.sh timeout",
        )
    except Exception as exc:
        Path(log_path).write_text(f"[spec-verify] crashed: {exc!r}\n")
        return (
            [SuiteResult(name="verify", verdict="FAIL", seconds="crash")],
            1,
            str(exc),
        )

    suites = _parse_verify_results(base_dir)
    return suites, rc, ""


_RESULTS_LINE = re.compile(
    r"^(?P<name>odin|backend|frontend|snapshots)\|(?P<verdict>PASS|FAIL|ENV_MISSING)\|"
    r"(?P<summary>[^|]*)\|(?P<seconds>[0-9.]+)$"
)


def _parse_verify_results(base_dir: str) -> List[SuiteResult]:
    """Read ``<base_dir>/.verify-logs/_results.txt`` if present.

    Returned rows are in the order verify.sh printed them.  Returns an
    empty list when the file is missing (e.g. crashed before writing),
    which the caller treats as red-with-unknown-suites.
    """
    path = Path(base_dir) / ".verify-logs" / "_results.txt"
    if not path.exists():
        return []
    suites: List[SuiteResult] = []
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _RESULTS_LINE.match(line)
        if not m:
            continue
        suites.append(
            SuiteResult(
                name=m.group("name"),
                verdict=m.group("verdict"),
                tests=m.group("summary").strip(),
                seconds=m.group("seconds"),
            )
        )
    return suites


# ----------------------------------------------------------------------------
# Publishing — write the result to the board
# ----------------------------------------------------------------------------


def _publish_report(
    *,
    spec_id: str,
    head_sha: str,
    merge_branch: str,
    report: VerifyReport,
) -> None:
    """Inspect a :class:`VerifyReport` and write the right things to the board.

    GREEN: a one-line task comment on every merged task that landed in
    this run; nothing at spec level.  RED / ENV_MISSING: a spec-level
    comment naming the merge and the failing suite(s); spec metadata
    carries the flag for the operator.
    """
    if not report.is_red:
        _publish_green(
            spec_id=spec_id,
            head_sha=head_sha,
            merge_branch=merge_branch,
            report=report,
        )
        return
    _publish_red(
        spec_id=spec_id,
        head_sha=head_sha,
        merge_branch=merge_branch,
        report=report,
    )


def _publish_green(
    *,
    spec_id: str,
    head_sha: str,
    merge_branch: str,
    report: VerifyReport,
) -> None:
    """GREEN path: spec metadata + (best-effort) a one-line task comment.

    The task comment is best-effort — we resolve the task id from the
    branch name when available, but if the resolution fails (manual
    merge, branch deleted) the run is still GREEN; we just don't post
    anything at task level.  No spec-level comment — green is silent.
    """
    _write_spec_metadata(
        spec_id=spec_id,
        head_sha=head_sha,
        verify_status="green",
        failed_suites=[],
        offending_sha=None,
    )
    task_id = _merge_task_id_for_sha(spec_id, head_sha, merge_branch)
    if task_id is None:
        return
    try:
        from tasks.models import CommentType, Task, TaskComment

        task = Task.objects.filter(id=task_id).first()
        if task is None:
            return
        body = (
            f"Post-merge verify on `{report.spec_branch}`: GREEN "
            f"({_fmt_duration(report.duration_s)})"
        )
        TaskComment.objects.create(
            task=task,
            author_email=_TASK_AUTHOR_EMAIL,
            author_label=_TASK_AUTHOR_LABEL,
            content=body,
            comment_type=CommentType.STATUS_UPDATE,
        )
    except Exception:
        logger.exception(
            "[spec:%s] failed to post green task comment", spec_id,
        )


def _publish_red(
    *,
    spec_id: str,
    head_sha: str,
    merge_branch: str,
    report: VerifyReport,
) -> None:
    """RED path: spec-level comment + flag in spec metadata."""
    _write_spec_metadata(
        spec_id=spec_id,
        head_sha=head_sha,
        verify_status="red",
        failed_suites=report.failed_suite_names,
        offending_sha=head_sha,
    )
    try:
        from tasks.models import CommentType, Spec, SpecComment

        spec = _spec_from_id(spec_id)
        if spec is None:
            return
        body = _format_red_comment(
            spec_branch=report.spec_branch,
            head_sha=head_sha,
            merge_branch=merge_branch,
            report=report,
        )
        SpecComment.objects.create(
            spec=spec,
            author_email=_GATE_AUTHOR_EMAIL,
            author_label=_GATE_AUTHOR_LABEL,
            content=body,
            comment_type=CommentType.STATUS_UPDATE,
        )
    except Exception:
        logger.exception(
            "[spec:%s] failed to post red spec comment", spec_id,
        )


def _format_red_comment(
    *,
    spec_branch: str,
    head_sha: str,
    merge_branch: str,
    report: VerifyReport,
) -> str:
    """Render the spec-level red-run comment."""
    suite_lines = []
    for s in report.failed_suites:
        detail = s.tests or "no per-suite detail"
        suite_lines.append(f"  - `{s.name}` → {s.verdict} ({detail})")
    suites_block = "\n".join(suite_lines) if suite_lines else "  - (none reported)"
    short_branch = merge_branch or "(unknown branch)"
    return (
        f"Spec-branch verify gate RED on `{spec_branch}`:\n\n"
        f"- Merge that turned it red: `{short_branch}` @ `{head_sha}`\n"
        f"- Verdict: `{report.status}`\n"
        f"- Failed suite(s):\n{suites_block}\n"
        f"- Log: `{report.log_path}`\n\n"
        f"Spec flagged for review — see metadata.verify_status.  "
        f"Merge is NOT auto-reverted."
    )


def _write_spec_metadata(
    *,
    spec_id: str,
    head_sha: str,
    verify_status: str,
    failed_suites: List[str],
    offending_sha: Optional[str],
) -> None:
    """Stamp the spec.metadata fields the operator sees on the board."""
    try:
        from tasks.models import Spec
        spec = _spec_from_id(spec_id)
        if spec is None:
            return
        meta = dict(spec.metadata or {})
        meta["verify_status"] = verify_status
        meta["verify_head_sha"] = head_sha
        meta["verify_failed_suites"] = list(failed_suites)
        if offending_sha is not None:
            meta["verify_offending_sha"] = offending_sha
        meta["verify_last_run_at"] = time.time()
        Spec.objects.filter(pk=spec.pk).update(metadata=meta)
    except Exception:
        logger.exception("[spec:%s] failed to write verify metadata", spec_id)


def _spec_from_id(spec_id: str):
    """Resolve a spec by ``odin_id`` (the public name) — DB import happens lazily."""
    from tasks.models import Spec
    try:
        return Spec.objects.filter(odin_id=spec_id).first()
    except Exception:
        return None


def _spec_branch_head_sha(spec_id: str) -> Optional[str]:
    """Resolve the spec branch HEAD SHA in production.

    Tries ``git rev-parse origin/spec/<spec_id>`` first (the spec
    branch is pushed to remote on merge, so the remote-tracking ref
    is the canonical one), then falls back to the local branch ref.
    Tests patch this to return synthetic SHAs without needing a real
    git worktree.  Returns ``None`` on any error — the caller must
    treat missing-HEAD as a no-op rather than spawning a verify with
    no SHA context.
    """
    try:
        project_root = _project_root()
        for ref in (f"origin/spec/{spec_id}", f"spec/{spec_id}"):
            result = subprocess.run(
                ["git", "-C", project_root, "rev-parse", "--verify", ref],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
                if sha:
                    return sha
        return None
    except Exception:
        return None


def _merge_task_id_for_sha(
    spec_id: str, head_sha: str, merge_branch: Optional[str] = None
) -> Optional[int]:
    """Resolve the task id that produced ``head_sha`` (best effort).

    Prefers the explicit ``merge_branch`` (when the merge path passes
    it through), otherwise falls back to a DB search by head_sha.  May
    return ``None`` — that's fine, we just skip the per-task comment.
    """
    try:
        from tasks.models import Task

        if merge_branch:
            t = Task.objects.filter(metadata__branch=merge_branch).first()
            if t is not None:
                return t.id
        t = Task.objects.filter(metadata__merge_head_sha=head_sha).first()
        return t.id if t else None
    except Exception:
        return None


def _fmt_duration(seconds: float) -> str:
    """Render a duration compactly for the one-line GREEN comment."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}m {int(s):02d}s"
