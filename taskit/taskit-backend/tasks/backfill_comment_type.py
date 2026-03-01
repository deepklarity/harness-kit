"""Backfill logic for comment_type — shared by data migration and tests.

Infers comment_type from attachments JSON and content patterns.
"""


def backfill_single_comment(comment) -> str:
    """Determine the correct comment_type for a pre-migration comment.

    Returns the inferred type string without modifying the comment.
    """
    attachments = comment.attachments or []
    for att in attachments:
        if isinstance(att, dict):
            att_type = att.get("type")
            if att_type == "question":
                return "question"
            if att_type == "reply":
                return "reply"
            if att_type == "proof":
                return "proof"

    return "status_update"
