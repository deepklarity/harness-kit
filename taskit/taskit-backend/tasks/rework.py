"""Rework: compose a follow-up task from a parent task + a one-sentence
human instruction (task #259).

No model call — this is pure assembly, not a chat. A human points at any
shelved/merged task, types one sentence of intent, and this module builds
the follow-up brief: the parent's context (title, its WHY section parsed
out of its description, its proof pointer), the instruction verbatim as
the requirement, and the standard working-protocol footer. The resulting
Task row is a normal TODO task on the parent's board — twins/quote
(tasks/similarity.py, tasks/estimation.py) and agent routing (the odin
orchestrator) already fire automatically once a task exists, so this
module does not re-implement any of that.

Guard rails are intentionally simple string rules, not NLP: this is a fast
path for a one-liner, not a place to interpret ambiguous input.
"""

import re

from .kanban_ordering import move_task
from .models import Task, TaskHistory, TaskStatus

MIN_INSTRUCTION_LENGTH = 10

_WHY_RE = re.compile(
    r"^WHY\b.*?(?=\n\s*\n|\n##|\Z)",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)

WORKING_PROTOCOL_FOOTER_TEMPLATE = (
    "## Working protocol (applies to every fable task)\n"
    "- First principles: fix the CAUSE, not the symptom.\n"
    "- Follow test-first waves: failing tests before implementation.\n"
    "- Attach proof to `{proof_path}` in your worktree and post a summary comment.\n"
    "- odin already runs you on an isolated task branch inside a worktree — do NOT\n"
    "  create or switch branches, never run `odin init`, never write under .odin/.\n"
    "- Never use git stash in shared contexts. Never edit files outside your worktree.\n"
)


class ReworkValidationError(ValueError):
    """Raised when the instruction fails a guard rail. Message is user-facing."""


def validate_instruction(instruction):
    """Clean and validate a rework instruction. Returns the stripped string.

    This is a fast path, not a chat: an instruction that's too short or
    reads as a single word is rejected rather than guessed at.
    """
    cleaned = (instruction or "").strip()
    if not cleaned:
        raise ReworkValidationError("An instruction is required.")
    if len(cleaned) < MIN_INSTRUCTION_LENGTH:
        raise ReworkValidationError(
            f"Instruction is too short — give the agent an actionable "
            f"one-liner (at least {MIN_INSTRUCTION_LENGTH} characters)."
        )
    if len(cleaned.split()) < 2:
        raise ReworkValidationError(
            "Instruction reads as a single word — write one actionable sentence."
        )
    return cleaned


def _proof_path(task_id):
    # Matches the canonical per-task proof convention (odin/src/odin/promote_check.py,
    # also used by tasks/similarity.py's twins comment).
    return f".proof/task-{task_id}/proof.md"


def _extract_why_section(description):
    """Pull the WHY paragraph out of a task description, or "" if absent.

    Matches a line starting with WHY (optionally "WHY (bucket, ...): ...")
    through the next blank line or markdown heading. Never fabricates a WHY
    when the parent has none.
    """
    if not description:
        return ""
    match = _WHY_RE.search(description)
    return match.group(0).strip() if match else ""


def _build_title(instruction):
    cleaned = " ".join(instruction.split())
    return cleaned[:255]


def _compose_body(parent, instruction):
    lines = [
        "## Parent task",
        f'- #{parent.id} "{parent.title}" ({parent.status})',
        f"- Proof: {_proof_path(parent.id)}",
    ]
    why = _extract_why_section(parent.description)
    if why:
        lines.append("")
        lines.append(why)
    lines.append("")
    lines.append("## Requirement")
    lines.append(instruction)
    return "\n".join(lines)


def compose_rework_task(parent, instruction, *, created_by):
    """Validate the instruction and create the follow-up Task row.

    Raises ReworkValidationError on a guard-rail failure — no task is
    created in that case. On success, returns the new Task, already
    saved with its final description (the working-protocol footer needs
    the new task's own id, so it's appended after the initial insert).
    """
    cleaned_instruction = validate_instruction(instruction)

    task = Task.objects.create(
        board=parent.board,
        spec=parent.spec,
        title=_build_title(cleaned_instruction),
        description=_compose_body(parent, cleaned_instruction),
        status=TaskStatus.TODO,
        created_by=created_by,
        metadata={
            "parent_task_id": parent.id,
            "rework_instruction": cleaned_instruction,
            "created_via": "rework",
        },
    )

    footer = WORKING_PROTOCOL_FOOTER_TEMPLATE.format(proof_path=_proof_path(task.id))
    task.description = f"{task.description}\n\n{footer}"
    task.kanban_position = move_task(task, target_status=task.status, target_index=0)
    task.save(update_fields=["description", "kanban_position"])

    TaskHistory.objects.create(
        task=task,
        field_name="created",
        old_value="",
        new_value="Task created",
        changed_by=created_by,
    )

    return task
