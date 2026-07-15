"""Board-driven planning: runs ``odin plan`` in a Celery worker so the
clarification gate conversation happens in spec comments instead of a
terminal.

Two-phase flow (mirrors the merge question pattern):

Phase 1 — ``run_board_driven_plan``
    Triggered by ``POST /specs/:id/request-board-plan/``.  Writes the spec
    content to a temp file, runs ``odin plan <file> --board-driven``, which
    dispatches the clarification gate and posts questions / summary / preview
    to the spec as SpecComments, then exits early (no task breakdown yet).
    The spec's ``metadata.board_plan_status`` is set to ``"awaiting_answers"``.

Phase 2 — ``resume_board_driven_plan``
    Triggered by the SpecComment signal when a human replies on a spec with
    ``board_plan_status == "awaiting_answers"``.  Runs
    ``odin plan --board-resume <spec_id>``, which reads the reply, appends it
    as clarification answers, runs task breakdown, and creates tasks on the
    board.  The spec is marked ``planning_complete``.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings

from .models import CommentType, Spec, SpecComment

logger = logging.getLogger("taskit.board_planner")

_BOARD_PLAN_AUTHOR = "odin+planner@odin"
_BOARD_PLAN_LABEL = "odin-planner"


def _resolve_odin_cli():
    return getattr(settings, "ODIN_CLI_PATH", "odin")


def _resolve_working_dir(spec):
    """Resolve the working directory for the odin plan subprocess."""
    board_wd = (spec.board.working_dir or "").strip()
    if board_wd:
        return board_wd
    return getattr(settings, "ODIN_WORKING_DIR", None) or str(Path.cwd())


def _write_spec_file(spec, suffix="_plan.md"):
    """Write spec content to a temp file in the working dir and return path."""
    wd = _resolve_working_dir(spec)
    wd_path = Path(wd)
    wd_path.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=suffix, dir=str(wd_path))
    with open(fd, "w") as f:
        f.write(spec.content or "")
    return path


def _build_board_plan_command(
    spec_path, spec_pk, planner_config=None, *,
    resume=False, reply_comment_id=None, spec_odin_id=None, reply_file=None,
):
    """Build the ``odin plan`` CLI command for board-driven mode."""
    config = planner_config or {}
    cmd = [_resolve_odin_cli(), "plan", spec_path]
    if resume:
        cmd.extend(["--board-resume", "--board-spec-pk", str(spec_pk)])
        if spec_odin_id:
            cmd.extend(["--board-spec-id", spec_odin_id])
        if reply_file:
            cmd.extend(["--reply-file", reply_file])
        cmd.append("--no-gate")
    else:
        cmd.extend(["--board-driven", "--board-spec-pk", str(spec_pk)])
    cmd.append("--quiet")

    base_agent = config.get("agent") or config.get("base_agent")
    base_model = config.get("base_model") or config.get("model")
    if base_agent:
        cmd.extend(["--base-agent", base_agent])
    if base_model and config.get("agent"):
        cmd.extend(["--base-model", base_model])
    if config.get("quick"):
        cmd.append("--quick")
    return cmd


def _run_odin_plan(cmd, working_dir, spec_pk):
    """Run the odin plan subprocess and log output."""
    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"board_plan_spec_{spec_pk}.log"

    logger.info(
        "Running board-driven plan: cmd=%s, cwd=%s, log=%s",
        cmd, working_dir, log_file.name,
    )
    try:
        result = subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=getattr(settings, "BOARD_PLAN_TIMEOUT", 1800),
        )
        log_file.write_text(
            f"CMD: {' '.join(cmd)}\n"
            f"EXIT: {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}\n",
        )
        return result
    except subprocess.TimeoutExpired:
        log_file.write_text(f"TIMEOUT after {getattr(settings, 'BOARD_PLAN_TIMEOUT', 1800)}s\nCMD: {' '.join(cmd)}\n")
        logger.error("Board-driven plan timed out for spec %s", spec_pk)
        return None
    except Exception:
        logger.exception("Board-driven plan failed for spec %s", spec_pk)
        return None


@shared_task(name="tasks.board_planner.run_board_driven_plan")
def run_board_driven_plan(spec_pk):
    """Phase 1: run ``odin plan --board-driven`` to post gate questions."""
    try:
        spec = Spec.objects.select_related("board").get(pk=spec_pk)
    except Spec.DoesNotExist:
        logger.error("Spec %s not found for board-driven plan", spec_pk)
        return

    working_dir = _resolve_working_dir(spec)
    spec_path = _write_spec_file(spec)

    cmd = _build_board_plan_command(
        spec_path, spec_pk, spec.planner_config, resume=False,
    )

    result = _run_odin_plan(cmd, working_dir, spec_pk)

    try:
        Path(spec_path).unlink(missing_ok=True)
    except Exception:
        pass

    if result is None or result.returncode != 0:
        stderr = result.stderr if result else "timeout/exception"
        meta = dict(spec.metadata or {})
        meta["board_plan_status"] = "error"
        meta["board_plan_error"] = (stderr or "")[:500]
        spec.metadata = meta
        spec.status = Spec.STATUS_PLANNING_FAILED
        spec.save()
        SpecComment.objects.create(
            spec=spec,
            author_email=_BOARD_PLAN_AUTHOR,
            author_label=_BOARD_PLAN_LABEL,
            content="Board-driven planning failed during the clarification gate. "
                    f"Error: {(stderr or 'unknown')[:200]}",
            comment_type=CommentType.STATUS_UPDATE,
        )
        logger.error(
            "Board-driven plan phase 1 failed for spec %s: rc=%s",
            spec_pk, result.returncode if result else "n/a",
        )
        return

    logger.info("Board-driven plan phase 1 completed for spec %s", spec_pk)


@shared_task(name="tasks.board_planner.resume_board_driven_plan")
def resume_board_driven_plan(spec_pk, comment_id):
    """Phase 2: run ``odin plan --board-resume`` to create tasks after reply."""
    try:
        spec = Spec.objects.select_related("board").get(pk=spec_pk)
    except Spec.DoesNotExist:
        logger.error("Spec %s not found for board-driven plan resume", spec_pk)
        return

    meta = dict(spec.metadata or {})
    if meta.get("board_plan_status") != "awaiting_answers":
        logger.warning(
            "Spec %s board_plan_status is %s, not awaiting_answers — skipping resume",
            spec_pk, meta.get("board_plan_status"),
        )
        return

    working_dir = _resolve_working_dir(spec)
    spec_path = _write_spec_file(spec)

    # Write reply text to a temp file for the CLI to read
    reply_file = None
    try:
        reply_comment = SpecComment.objects.get(id=comment_id)
        wd_path = Path(working_dir)
        wd_path.mkdir(parents=True, exist_ok=True)
        fd, reply_file = tempfile.mkstemp(suffix="_reply.txt", dir=str(wd_path))
        with open(fd, "w") as f:
            f.write(reply_comment.content)
    except SpecComment.DoesNotExist:
        logger.warning("Reply comment %s not found", comment_id)

    cmd = _build_board_plan_command(
        spec_path, spec_pk, spec.planner_config,
        resume=True, reply_comment_id=comment_id,
        spec_odin_id=spec.odin_id, reply_file=reply_file,
    )

    result = _run_odin_plan(cmd, working_dir, spec_pk)

    try:
        Path(spec_path).unlink(missing_ok=True)
    except Exception:
        pass
    if reply_file:
        try:
            Path(reply_file).unlink(missing_ok=True)
        except Exception:
            pass

    if result is None or result.returncode != 0:
        stderr = result.stderr if result else "timeout/exception"
        logger.error(
            "Board-driven plan phase 2 failed for spec %s: rc=%s, stderr=%s",
            spec_pk, result.returncode if result else "n/a",
            (stderr or "")[:200],
        )
        SpecComment.objects.create(
            spec=spec,
            author_email=_BOARD_PLAN_AUTHOR,
            author_label=_BOARD_PLAN_LABEL,
            content="Task breakdown failed after your reply. "
                    "You can retry planning from the spec page.",
            comment_type=CommentType.STATUS_UPDATE,
        )
        return

    logger.info("Board-driven plan phase 2 completed for spec %s", spec_pk)
