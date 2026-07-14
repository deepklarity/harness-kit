"""Upload ``.proof/task-<id>/`` files from the worktree as board attachments.

After a task's reflection passes and its merge lands, the proof files the
agent wrote in the worktree are uploaded as :class:`CommentAttachment` rows
linked to a single ``PROOF`` comment.  The evidence lives on the task board,
not in the git history (which bloats diffs and collides at merge time).

Called from ``dag_executor.merge_task_on_reflection`` after the merge
succeeds and the task advances to TESTING.  Wrapped by the caller in
try/except so a flaky upload never regresses the merge or the status flip.
"""

import logging
from pathlib import Path

from django.core.files.base import ContentFile

from .models import CommentAttachment, CommentType, TaskComment

logger = logging.getLogger(__name__)

MAX_PROOF_FILE_SIZE = 512 * 1024  # 512 KB — generous for text, caps pathological dumps

_TRUNCATION_MARKER = (
    "\n\n... [TRUNCATED: original {orig} bytes, showing first {shown} bytes]\n"
)


def attach_task_proof_files(task):
    """Upload ``.proof/task-<id>/`` files from the worktree as attachments.

    Reads files from ``task.metadata["worktree_path"]`` (falling back to
    ``"working_dir"``), creates one :class:`CommentAttachment` per file
    (truncating oversized files with a clear marker so the file count stays
    honest), and links them to a single ``PROOF`` comment.

    Graceful no-op (returns ``None``) when the proof directory or worktree is
    absent — a task that produced no proof files simply gets no attachment
    comment.  Never raises; filesystem errors are logged and skipped.
    """
    metadata = task.metadata or {}
    worktree_path = metadata.get("worktree_path") or metadata.get("working_dir")
    if not worktree_path:
        logger.debug("[task:%s] no worktree_path in metadata — skipping proof upload", task.id)
        return None

    proof_dir = Path(worktree_path) / ".proof" / f"task-{task.id}"
    if not proof_dir.is_dir():
        logger.debug(
            "[task:%s] no .proof/task-%s/ at %s — skipping",
            task.id, task.id, proof_dir,
        )
        return None

    files = sorted(p for p in proof_dir.rglob("*") if p.is_file())
    if not files:
        logger.info("[task:%s] proof dir %s is empty — skipping", task.id, proof_dir)
        return None

    attachments = []
    for fpath in files:
        rel_name = str(fpath.relative_to(proof_dir))
        try:
            raw = fpath.read_bytes()
        except OSError as exc:
            logger.warning("[task:%s] could not read proof file %s: %s", task.id, fpath, exc)
            continue

        original_size = len(raw)
        if original_size > MAX_PROOF_FILE_SIZE:
            marker = _TRUNCATION_MARKER.format(
                orig=original_size, shown=MAX_PROOF_FILE_SIZE,
            ).encode()
            raw = raw[:MAX_PROOF_FILE_SIZE] + marker

        storage_name = f"task-{task.id}__{rel_name.replace('/', '__')}"
        att = CommentAttachment.objects.create(
            task=task,
            file=ContentFile(raw, name=storage_name),
            original_filename=rel_name,
            content_type="text/plain",
            file_size=len(raw),
            uploaded_by="proof-upload@taskit",
        )
        attachments.append(att)

    if not attachments:
        return None

    count = len(attachments)
    comment = TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="proof-upload@taskit",
        author_label="proof-upload",
        content=(
            f"Proof files uploaded from worktree `.proof/task-{task.id}/` "
            f"({count} file{'s' if count != 1 else ''})."
        ),
        comment_type=CommentType.PROOF,
        attachments=[{
            "type": "proof_upload",
            "source": "worktree",
            "file_count": count,
        }],
    )
    CommentAttachment.objects.filter(
        id__in=[a.id for a in attachments], task=task,
    ).update(comment=comment)
    logger.info("[task:%s] uploaded %d proof file(s) as attachments", task.id, count)
    return comment
