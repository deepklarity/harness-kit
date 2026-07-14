#!/usr/bin/env python
"""Live demonstration: a zombie run's write is rejected; the live run's passes.

Not a pytest test — a narrated, runnable proof for task #210's acceptance
criterion ("a simulated zombie (old run_token) cannot flip status or post
results; the live run can"). Uses the real Django ORM + DRF test client
against the dev sqlite database.

Usage:
    cd taskit/taskit-backend
    USE_SQLITE=True FIREBASE_AUTH_ENABLED=False python testing_tools/demo_stale_write_rejection.py
"""
import json
import sys

from _utils import setup_django

setup_django()

from rest_framework.test import APIClient  # noqa: E402

from tasks import task_runs  # noqa: E402
from tasks.models import Board, Task, TaskRun, TaskRunState, TaskStatus  # noqa: E402


def line(msg=""):
    print(msg)
    sys.stdout.flush()


def main():
    client = APIClient()

    board = Board.objects.create(name="Task210 Demo Board")
    task = Task.objects.create(
        board=board, title="Zombie fencing demo", created_by="demo@test.com",
        status=TaskStatus.EXECUTING,
    )
    line(f"Created task #{task.id} in EXECUTING.")

    # Attempt A: the original dispatch.
    run_a = task_runs.start_run(task, "run-token-A", pid=11111, sandbox_name="odin-msb-a")
    line(f"Dispatch A: TaskRun(run_token={run_a.run_token}, state={run_a.state}) created.")

    # The worker restarts / times out; the task is re-dispatched. Attempt A's
    # sandbox process is still alive on disk somewhere (a zombie) — it just
    # never got the memo. Attempt B supersedes it.
    run_b = task_runs.start_run(task, "run-token-B", pid=22222, sandbox_name="odin-msb-b")
    run_a.refresh_from_db()
    line(f"Redispatch B: TaskRun(run_token={run_b.run_token}, state={run_b.state}) created.")
    line(f"  -> Attempt A superseded: state is now {run_a.state} (expected EXPIRED).")
    assert run_a.state == TaskRunState.EXPIRED

    def post_execution_result(run_token):
        return client.post(
            f"/tasks/{task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "zombie or live output",
                    "duration_ms": 1000.0,
                    "agent": "claude",
                    "metadata": {"taskit_run_token": run_token},
                },
                "status": "REVIEW",
                "updated_by": "claude+claude-sonnet-4-5@odin.agent",
            },
            format="json",
        )

    line("")
    line("--- Zombie (attempt A, run-token-A) posts its execution_result ---")
    resp_zombie = post_execution_result("run-token-A")
    line(f"HTTP {resp_zombie.status_code}: {json.dumps(resp_zombie.data)}")
    task.refresh_from_db()
    line(f"Task status after zombie write: {task.status} (expected unchanged: EXECUTING)")
    assert resp_zombie.status_code == 409
    assert resp_zombie.data["code"] == "stale_run_token"
    assert task.status == TaskStatus.EXECUTING

    line("")
    line("--- Live run (attempt B, run-token-B) posts its execution_result ---")
    resp_live = post_execution_result("run-token-B")
    line(f"HTTP {resp_live.status_code}")
    task.refresh_from_db()
    line(f"Task status after live write: {task.status} (expected: REVIEW or auto-advanced TESTING)")
    run_b.refresh_from_db()
    line(f"TaskRun B state after live write: {run_b.state} (expected: FINISHED)")
    assert resp_live.status_code == 200
    assert task.status != TaskStatus.EXECUTING
    assert run_b.state == TaskRunState.FINISHED

    line("")
    line("RESULT: zombie (old run_token) write REJECTED 409 stale_run_token; "
          "live (current run_token) write ACCEPTED and finished the run.")

    # Cleanup — this script writes into the dev sqlite DB; leave no trace.
    task.delete()
    board.delete()
    line("Cleaned up demo board/task.")


if __name__ == "__main__":
    main()
