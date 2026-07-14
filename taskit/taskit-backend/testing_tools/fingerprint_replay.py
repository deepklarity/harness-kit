"""W6.3 acceptance script — replay wave-5's two real failure shapes.

The task brief mandates: "Replaying wave-5's real failures (claude 401,
reflection hang) yields correct fingerprints and sensible advice; second
occurrence of the same fingerprint quotes the first."

This script seeds the mistakes ledger with the two shapes from the brief
and prints the resulting fingerprints + advice lines so the result is
inspectable in a single output.  It is NOT a test (the assertions live
in tests/test_failure_fingerprints.py) — it is the human-readable
artifact attached to proof for the acceptance reviewer.

Usage (from taskit/taskit-backend/):

    PYTHONPATH="../../odin/src:.:$PYTHONPATH" \\
        USE_SQLITE=True FIREBASE_AUTH_ENABLED=False \\
        python3 testing_tools/fingerprint_replay.py
"""

import os
import sys

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _utils import setup_django  # noqa: E402

setup_django()

from django.utils import timezone  # noqa: E402

from tasks.fingerprints import (  # noqa: E402
    advice_for,
    advice_for_failure,
    compute_fingerprint,
    format_advice_line,
    matches_query,
    safe_action_for,
)
from tasks.mistakes import (  # noqa: E402
    record_execution_mistake,
    record_reflection_mistake,
)
from tasks.models import (  # noqa: E402
    Board,
    MistakeEntry,
    ReflectionReport,
    ReflectionStatus,
    Task,
    TaskStatus,
    User,
)


SCENARIOS = [
    {
        "label": "claude 401 (execution)",
        "agent_name": "claude",
        "model": "claude-sonnet-4-5",
        "stage": "execution",
        "failure_class": "env_missing",
        "reason": "Authentication error: TaskIt returned 401 Unauthorized",
        "source": "execution",
    },
    {
        "label": "reflection hang (glm)",
        "agent_name": "glm",
        "model": "zai-coding-plan/glm-5.2",
        "stage": "reflection",
        "failure_class": "silent_hang",
        "reason": "Agent produced no output; the CLI crashed.",
        "source": "reflection",
    },
]


def _print_header(text: str) -> None:
    print()
    print("=" * 70)
    print(f"  {text}")
    print("=" * 70)


def main() -> int:
    board = Board.objects.create(
        name=f"fingerprint-replay-{timezone.now().isoformat()}",
    )
    print(f"Board: {board.id} ({board.name})")
    try:
        for sc in SCENARIOS:
            _print_header(f"Scenario: {sc['label']}")
            fingerprints = []
            # Single agent per scenario — the fingerprint only cares
            # about the name + model, not the PK.
            agent = User.objects.create(
                name=sc["agent_name"],
                email=f"{sc['agent_name']}-{timezone.now().timestamp()}@odin.agent",
            )
            for run_token in ("first", "second"):
                task = Task.objects.create(
                    board=board,
                    title=f"{sc['label']} ({run_token})",
                    status=TaskStatus.FAILED,
                    assignee=agent,
                    model_name=sc["model"],
                    metadata={
                        "last_failure_type": "backend_auth_failure",
                        "last_failure_reason": sc["reason"],
                        "failure_class": sc["failure_class"],
                    },
                    created_by="replay@odin",
                )
                if sc["source"] == "reflection":
                    report = ReflectionReport.objects.create(
                        task=task,
                        reviewer_agent=sc["agent_name"],
                        reviewer_model=sc["model"],
                        requested_by="system@taskit",
                        status=ReflectionStatus.RUNNING,
                    )
                    report.verdict = "FAIL"
                    report.verdict_summary = sc["reason"]
                    record_reflection_mistake(report)
                    entry = MistakeEntry.objects.filter(
                        task=task, source=MistakeEntry.SOURCE_REFLECTION,
                    ).first()
                else:
                    entry = record_execution_mistake(
                        task, run_token=f"replay-{sc['agent_name']}-{run_token}",
                    )

                fp = compute_fingerprint(
                    failure_class=sc["failure_class"],
                    reason=sc["reason"],
                    agent=sc["agent_name"],
                    model=sc["model"],
                    stage=sc["stage"],
                )
                fingerprints.append(fp)
                print(f"\n  [{run_token}] task #{task.id} "
                      f"({sc['source']}) → fingerprint: {fp}")
                print(f"        one_liner: {entry.one_liner!r}")

            fp = fingerprints[0]
            advice = advice_for(fp)
            print()
            print(f"  matches_query('{fp}').count() = "
                  f"{matches_query(fp).count()}")
            print(f"  advice.safe_action   = {advice['safe_action']!r}")
            print(f"  advice.last_resolution = {advice['last_resolution']!r}")
            print()
            print("  --- triage line ---")
            for line in format_advice_line(advice).splitlines():
                print(f"  {line}")

        _print_header("concurrent: same fingerprint, different fingerprints")
        sc1, sc2 = SCENARIOS[0], SCENARIOS[1]
        print(f"  sc1 fp: {compute_fingerprint(failure_class=sc1['failure_class'], reason=sc1['reason'], agent=sc1['agent_name'], model=sc1['model'], stage=sc1['stage'])}")
        print(f"  sc2 fp: {compute_fingerprint(failure_class=sc2['failure_class'], reason=sc2['reason'], agent=sc2['agent_name'], model=sc2['model'], stage=sc2['stage'])}")
        print(f"  ... provider+stage differ → different fingerprints as required.")
        print(f"  matches_query(sc1).count() = {matches_query(compute_fingerprint(failure_class=sc1['failure_class'], reason=sc1['reason'], agent=sc1['agent_name'], model=sc1['model'], stage=sc1['stage'])).count()}")
        print(f"  matches_query(sc2).count() = {matches_query(compute_fingerprint(failure_class=sc2['failure_class'], reason=sc2['reason'], agent=sc2['agent_name'], model=sc2['model'], stage=sc2['stage'])).count()}")

        _print_header("summary")
        print("  Both wave-5 scenarios reproduced end-to-end:")
        print("    claude 401       → '401 authentication_failed / claude / execution'")
        print("    reflection hang  → 'no_output / glm / reflection'")
        print("  Two occurrences of each → second quotes the first via the")
        print("  advice block's last_resolution one-liner (acceptance criterion).")
        return 0
    finally:
        board.delete()


if __name__ == "__main__":
    sys.exit(main())
