"""System-comment repeat suppression.

The task page's comment stream used to fill with byte-identical system
messages on retried tasks: the "Memory — closest finished twins" card
posted once per retry (unchanged), the 8KB "Effective input" dump once
per attempt, and a failure burst landing twice in seconds when a requeue
raced the runner wrapper.

Every system-comment posting site asks this one question before writing:
*does a comment with this exact body already exist on this task?* If it
does, the message is a repeat of something the reader has already seen —
don't post it again. The check is content-based (not a time window), so a
message re-posts the moment the content genuinely changes: a new twin
appears, the assignee moves, the failure reason shifts. A retry that
changed nothing changes nothing on the page.

Callers scope the check to a system-message *kind* (``author_label`` /
``author_email``) so a twins card never collides with a failure-history
card that happens to share a body.
"""

from __future__ import annotations


def has_identical_comment(
    task,
    content: str,
    *,
    author_label: str | None = None,
    author_email: str | None = None,
) -> bool:
    """True if ``task`` already carries a comment with byte-identical ``content``.

    Optionally scoped to a system-message kind (``author_label`` and/or
    ``author_email``) so only comments of the same family are compared — a
    twins card (``author_label="memory"``) is never suppressed by a
    failure-history card that happens to share wording.

    Returns ``False`` for empty ``content`` (nothing to dedup against).
    """
    if not content:
        return False
    qs = task.comments.all()
    if author_label:
        qs = qs.filter(author_label=author_label)
    if author_email:
        qs = qs.filter(author_email=author_email)
    return qs.filter(content=content).exists()
