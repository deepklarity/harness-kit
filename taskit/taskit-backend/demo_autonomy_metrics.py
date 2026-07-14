#!/usr/bin/env python
"""Demo runner: seed wave 1/2/3 fixtures, run autonomy_metrics.py, save outputs.

Not part of the regular test suite — invoked once to produce the
"output over waves 1-3" attachment for the task proof. Mirrors the
fixture shapes used in tests/test_autonomy_metrics.py.
"""
import io
import json
import os
import subprocess
import sys
import datetime
from contextlib import redirect_stdout
from pathlib import Path

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "testing_tools"))

import django
django.setup()

from django.core.management import call_command

# Wipe the dev DB and re-migrate.
call_command("flush", "--noinput")
call_command("migrate", "--noinput", verbosity=0)
call_command("seedmodels", verbosity=0)

from tasks.models import (  # noqa: E402
    Board, Spec, Task, TaskComment, TaskHistory, TaskStatus, User, UserRole,
)

OUT_DIR = Path("/tmp/autonomy_metrics_demo")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def make_agent(name):
    user, _ = User.objects.get_or_create(
        email=f"{name}@odin.agent",
        defaults={"name": name, "role": UserRole.AGENT, "is_active": True},
    )
    return user


def add_history(task, field, old_value, new_value, changed_by, when):
    return TaskHistory.objects.create(
        task=task, field_name=field,
        old_value=str(old_value), new_value=str(new_value),
        changed_by=changed_by,
    )


def add_comment(task, author, content, comment_type="status_update"):
    return TaskComment.objects.create(
        task=task, author_email=author, content=content,
        comment_type=comment_type,
    )


def make_wave1(board, spec, claude):
    """17 agent-flow + 1 hand-completed (the operator #104).

    The wave-1 audit also tracks 4 merge conflicts hand-resolved by
    operator (F24), but those are git-side work — they don't appear as
    TaskHistory transitions, so we don't add them here. Steering
    comments ARE recorded as operator touches.
    """
    tasks = []
    for i in range(17):
        hour = (10 + (i // 60)) % 24
        minute = i % 60
        start = datetime.datetime(2026, 7, 5, hour, minute, tzinfo=datetime.timezone.utc)
        exec_minutes = 5 + (i % 25)
        end = start + datetime.timedelta(minutes=exec_minutes)
        t = Task.objects.create(
            board=board, spec=spec, title=f"Wave 1 task #{100 + i}",
            status=TaskStatus.DONE, created_by="claude@odin.agent",
            assignee=claude, metadata={"last_duration_ms": exec_minutes * 60 * 1000},
        )
        add_history(t, "status", "TODO", "EXECUTING", "system@taskit", start)
        add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent", end)
        tasks.append(t)
    # Hand-completed #104 — operator flipped to DONE (the only disqualifying event).
    t = Task.objects.create(
        board=board, spec=spec, title="Wave 1 task #104 (operator)",
        status=TaskStatus.DONE, created_by="claude@odin.agent",
        assignee=claude, metadata={"last_duration_ms": 18 * 60 * 1000},
    )
    start = datetime.datetime(2026, 7, 5, 11, 0, tzinfo=datetime.timezone.utc)
    end = start + datetime.timedelta(minutes=18)
    add_history(t, "status", "TODO", "EXECUTING", "system@taskit", start)
    add_history(t, "status", "EXECUTING", "DONE", "operator@example.com", end)
    # 11 steering comments scattered across the agent-flow tasks —
    # matches the audit's "~12 operator unstick transitions" (1 transition
    # + 11 comments).
    for i in range(11):
        add_comment(tasks[i], "operator@example.com", f"Use the v2 endpoint.",
                    comment_type="question")
    return tasks


def make_wave2(board, spec, claude):
    """18 agent-authored + 8 with steering comments (no operator unsticks on 10)."""
    tasks = []
    for i in range(18):
        hour = (9 + (i // 60)) % 24
        minute = i % 60
        start = datetime.datetime(2026, 7, 6, hour, minute, tzinfo=datetime.timezone.utc)
        exec_seconds = 55 + (i * 60)
        end = start + datetime.timedelta(seconds=exec_seconds)
        t = Task.objects.create(
            board=board, spec=spec, title=f"Wave 2 task #{200 + i}",
            status=TaskStatus.DONE, created_by="claude@odin.agent",
            assignee=claude, metadata={"last_duration_ms": exec_seconds * 1000},
        )
        add_history(t, "status", "TODO", "EXECUTING", "system@taskit", start)
        add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent", end)
        tasks.append(t)
    # 8 tasks with steering comments (matching audit: 7/11 wave-2 subset zero-unstick)
    for i in range(8):
        add_comment(tasks[i + 10], "operator@example.com", "Try claude-sonnet-5 instead.",
                    comment_type="question")
    return tasks


def make_wave3(board, spec, claude):
    """Wave 3 in flight — mix of DONE (verified) + IN_PROGRESS/EXECUTING (in flight)."""
    tasks = []
    base = datetime.datetime(2026, 7, 6, 14, 0, tzinfo=datetime.timezone.utc)
    # 4 verified DONE tasks (presumed wave-3 so far)
    for i in range(4):
        start = base + datetime.timedelta(minutes=i * 5)
        end = start + datetime.timedelta(minutes=4 + i)
        t = Task.objects.create(
            board=board, spec=spec, title=f"Wave 3 verified #{i}",
            status=TaskStatus.DONE, created_by="claude@odin.agent",
            assignee=claude, metadata={"last_duration_ms": (4 + i) * 60 * 1000},
        )
        add_history(t, "status", "TODO", "EXECUTING", "system@taskit", start)
        add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent", end)
        tasks.append(t)
    # 2 in flight: EXECUTING (no DONE history yet)
    for i in range(2):
        start = base + datetime.timedelta(minutes=30 + i * 5)
        Task.objects.create(
            board=board, spec=spec, title=f"Wave 3 in-flight #{i}",
            status=TaskStatus.EXECUTING, created_by="claude@odin.agent",
            assignee=claude, metadata={"last_duration_ms": None},
        )
    # 1 REVIEW (awaiting promotion)
    t = Task.objects.create(
        board=board, spec=spec, title="Wave 3 review pending",
        status=TaskStatus.REVIEW, created_by="claude@odin.agent",
        assignee=claude,
    )
    return tasks


def run_script(spec_id, mode):
    """Invoke autonomy_metrics.py via subprocess so the wrapper matches
    real CLI usage; capture stdout."""
    args = [
        sys.executable,
        str(BACKEND / "testing_tools" / "autonomy_metrics.py"),
        "--spec", str(spec_id),
    ]
    if mode == "brief":
        args.append("--brief")
    elif mode == "json":
        args.append("--json")
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(BACKEND) + os.pathsep
        + str(BACKEND / "testing_tools") + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    r = subprocess.run(args, capture_output=True, text=True, env=env)
    return r.stdout + r.stderr


def main():
    board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
    claude = make_agent("claude")

    wave1_spec = Spec.objects.create(
        board=board, odin_id="sp_wave1", title="Wave 1 (M1 close-out)")
    wave2_spec = Spec.objects.create(
        board=board, odin_id="sp_wave2", title="Wave 2 (smoke + lineage)")
    wave3_spec = Spec.objects.create(
        board=board, odin_id="sp_wave3", title="Wave 3 (in flight)")

    make_wave1(board, wave1_spec, claude)
    make_wave2(board, wave2_spec, claude)
    make_wave3(board, wave3_spec, claude)

    for label, spec_id in [("wave1", wave1_spec.id),
                           ("wave2", wave2_spec.id),
                           ("wave3", wave3_spec.id)]:
        for mode in ("brief", "standard", "json"):
            out = run_script(spec_id, mode)
            path = OUT_DIR / f"{label}_{mode}.txt"
            path.write_text(out)
            print(f"wrote {path}")

    # Aggregate view (whole board)
    out = run_script(board.id, "standard")
    path = OUT_DIR / "all_boards_standard.txt"
    path.write_text(out)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()