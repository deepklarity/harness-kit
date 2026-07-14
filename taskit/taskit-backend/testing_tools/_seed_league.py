"""Seed a fixture board for live verification of the league table.

Run from taskit/taskit-backend:
    python3 testing_tools/_seed_league.py
"""
import datetime
import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import django

django.setup()

from tasks.models import (
    Board,
    MergeAttempt,
    MergeMode,
    MergeOutcome,
    MergeTrigger,
    Spec,
    Task,
    TaskComment,
    TaskHistory,
    TaskStatus,
    User,
    UserRole,
)


def make_agent(name):
    user, _ = User.objects.get_or_create(
        email=f"{name}@odin.agent",
        defaults={"name": name, "role": UserRole.AGENT, "is_active": True},
    )
    return user


def main():
    board_name = "League Demo Board"
    board, _ = Board.objects.get_or_create(
        name=board_name,
        defaults={"working_dir": "/tmp/league-demo"},
    )
    print(f"Board #{board.id}: {board.name}")

    spec_a, _ = Spec.objects.get_or_create(
        board=board,
        odin_id="sp_league_wave_a",
        defaults={"title": "Wave A"},
    )
    spec_b, _ = Spec.objects.get_or_create(
        board=board,
        odin_id="sp_league_wave_b",
        defaults={"title": "Wave B"},
    )

    claude = make_agent("claude")
    gemini = make_agent("gemini")
    glm = make_agent("glm")

    base = datetime.datetime(2026, 7, 1, 9, 0, tzinfo=datetime.timezone.utc)

    fixtures = [
        ("claude", "claude-opus-4-6", spec_a, TaskStatus.DONE, 0, 180_000),
        ("claude", "claude-opus-4-6", spec_a, TaskStatus.DONE, 1, 220_000),
        ("claude", "claude-opus-4-6", spec_b, TaskStatus.TESTING, 0, 240_000),
        ("claude", "claude-sonnet-4-6", spec_a, TaskStatus.DONE, 0, 90_000),
        ("claude", "claude-sonnet-4-6", spec_b, TaskStatus.DONE, 2, 150_000),
        ("gemini", "gemini-2.5-pro", spec_a, TaskStatus.DONE, 0, 120_000),
        ("gemini", "gemini-2.5-pro", spec_a, TaskStatus.DONE, 0, 110_000),
        ("gemini", "gemini-2.5-flash", spec_b, TaskStatus.TESTING, 0, 60_000),
        ("gemini", "gemini-2.5-flash", spec_b, TaskStatus.DONE, 1, 75_000),
        ("glm", "zai-coding-plan/glm-5.1", spec_b, TaskStatus.DONE, 0, 45_000),
        ("glm", "zai-coding-plan/glm-5.1", spec_b, TaskStatus.DONE, 0, 55_000),
    ]

    for i, (agent_name, model_name, spec, status, rework, duration_ms) in enumerate(fixtures):
        agent = make_agent(agent_name)
        exists = Task.objects.filter(
            board=board, spec=spec, assignee=agent, model_name=model_name,
            title=f"{agent_name}-{model_name}-seed-{i}",
        ).exists()
        if exists:
            continue
        input_tokens = 50_000 + i * 30_000
        output_tokens = 10_000 + i * 5_000
        task = Task.objects.create(
            board=board, spec=spec,
            title=f"{agent_name}-{model_name}-seed-{i}",
            status=status, created_by=f"{agent_name}@odin.agent",
            assignee=agent, model_name=model_name,
            metadata={
                "rework_count": rework,
                "last_duration_ms": duration_ms,
                "last_usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
            },
        )
        trace_text = "\n".join([
            json.dumps({"type": "assistant",
                        "message": {"id": "m1", "type": "message",
                                    "role": "assistant", "content": []}}),
            json.dumps({"type": "result", "subtype": "success",
                        "usage": {"input_tokens": input_tokens,
                                  "output_tokens": output_tokens,
                                  "total_tokens": input_tokens + output_tokens}}),
        ])
        TaskComment.objects.create(
            task=task,
            author_email=f"{agent_name}@odin.agent",
            content=trace_text,
            comment_type="status_update",
            attachments=["trace:execution_jsonl"],
        )
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="EXECUTING",
            new_value="DONE", changed_by=f"{agent_name}@odin.agent",
        )

        if i % 5 == 0 and i > 0:
            MergeAttempt.objects.create(
                task=task, spec=spec,
                trigger=MergeTrigger.REFLECTION_PASS,
                mode=MergeMode.AGENT,
                outcome=MergeOutcome.CONFLICT,
                started_at=base, finished_at=base + datetime.timedelta(minutes=5),
            )

    print(f"Seeded {Task.objects.filter(board=board).count()} tasks.")
    print(f"Boards in DB: {Board.objects.count()}")
    print(f"Board ID for CLI: {board.id}")


if __name__ == "__main__":
    main()