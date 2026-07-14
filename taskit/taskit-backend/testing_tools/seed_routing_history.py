"""Seed a synthetic but realistic TaskIt board for the routing-suggester
proof artifact.

Production routing runs against Postgres. This env has SQLite only,
so we mirror the wave-2 distribution shape (gemini robustly merges
~90% of tasks, claude handles the harder ones, etc.) into a local
fixture. The proof then queries the seeded SQLite DB via the same
``odin.agent_routing.suggest_routing`` API the production setup would
hit.

The shape is conservative — every agent gets a mix of success,
disqualifying operator takeover, and capture gaps so all three of the
suggesters' inputs (success_rate, sample_count, median_tokens) are
exercised by the resulting table.

  cd taskit/taskit-backend
  USE_SQLITE=True python testing_tools/routing_suggest.py --seed
"""

import datetime
import os
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))
import django  # noqa: E402

django.setup()

from tasks.agent_stats import compute_agent_stats_for_board  # noqa: E402
from tasks.models import (  # noqa: E402
    Board,
    Spec,
    Task,
    TaskHistory,
    TaskStatus,
    User,
    UserRole,
)


# Distribution designed to look like a healthy wave-2 environment:
#   gemini: 12 observations, 11 successes (operator stole 1 back)
#   qwen:   10 observations, 7 successes, 1 capture gap (median = real)
#   claude: 8 observations, 8 successes (always converges, but expensive)
#   glm:    4 observations, 2 successes (thin history — fallback path)
#
# Tokens are realistic medians; one capture gap per agent at minimum
# so median_tokens is never a lie.
SEED_BOARD_NAME = "Fable Roadmap (wave-4 seed)"

# Per-agent recipe: (total, success_count, median_tokens, capture_gaps, cost_tier)
RECIPE = {
    # Cheap tier — the routine stuff.
    "gemini": (12, 11, 4500, 1, "low"),
    "qwen":   (10, 7,  3800, 2, "low"),
    "glm":    (4,  2,  5500, 1, "low"),
    # Expensive tier — high correctness, used when cheap is thin.
    "claude": (8,  8,  22000, 1, "high"),
}


def _seed() -> Board:
    """Reset and re-seed the demo board. Idempotent."""
    board, _ = Board.objects.get_or_create(
        name=SEED_BOARD_NAME,
        defaults={"working_dir": "/tmp/fable-seed"},
    )
    # Wipe and reseed — proof wants deterministic shape.
    Task.objects.filter(board=board).delete()
    Spec.objects.filter(board=board).delete()
    User.objects.filter(email__endswith="@odin.agent").delete()

    spec = Spec.objects.create(
        board=board,
        odin_id="sp_wave4_seed",
        title="Wave-4 routing seed",
    )

    for agent_name, (total, success_count, median_tokens, capture_gaps, cost_tier) in RECIPE.items():
        agent = User.objects.create(
            email=f"{agent_name}@odin.agent",
            name=agent_name,
            role=UserRole.AGENT,
            is_admin=False,
            cost_tier=cost_tier,
            capabilities=["coding", "writing", "run_shell_command", "read_file", "write_file"],
        )
        operator_closes = total - success_count  # remaining → agent-authored_done=success_count, operator_done=operator_closes - capture_gaps-ish
        for i in range(total):
            is_success = i < success_count
            capture_gap = i < capture_gaps
            t = Task.objects.create(
                board=board,
                spec=spec,
                title=f"{agent_name}-{i}",
                status=TaskStatus.DONE,
                created_by=f"{agent_name}@odin.agent",
                assignee=agent,
                metadata=(
                    {}
                    if capture_gap
                    else {"last_usage": {"total_tokens": median_tokens}}
                ),
            )
            TaskHistory.objects.create(
                task=t,
                field_name="status",
                old_value="TODO",
                new_value="DONE",
                changed_by=(
                    f"{agent_name}@odin.agent"
                    if is_success
                    else "alice@example.com"  # disqualifying operator flip
                ),
                changed_at=datetime.datetime(
                    2026, 6, 1 + i, 10, 0,
                    tzinfo=datetime.timezone.utc,
                ),
            )

    return board


def main() -> None:
    board = _seed()
    stats = compute_agent_stats_for_board(board)
    print(f"Seeded board id={board.id} name={board.name!r}")
    print(f"  total tasks: {sum(s.sample_count for s in stats.values())}")
    print(f"  observed agents: {sorted(stats.keys())}")
    print()
    print("Run `python testing_tools/routing_suggest.py` to see the "
          "ranking table over this seed.")


if __name__ == "__main__":
    main()
