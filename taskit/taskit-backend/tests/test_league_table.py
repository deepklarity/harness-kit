"""League table — BACKLOG item 8.

Ranks agent+model pairs over landed tasks (status in {DONE, TESTING}).
Each row carries tasks_landed, hands_free_count/pct, redo_rounds_avg,
tokens/duration medians, merge_conflicts_caused, and cost_usd_total.

These tests pin the BEHAVIOR only — the implementation file
``tasks.league`` does not exist yet, so every test currently fails with
``ModuleNotFoundError`` (the desired red). Each test does its own
``from tasks.league import …`` inside the body so the failure traceback
points at the missing module rather than crashing collecttime.

Coverage:
  PureAggregation — aggregate_league_rows math (no DB).
  BoardScopedCompute — compute_league_for_board hits the ORM and respects
    landed-status, operator-takeover, spec-window, cost, merge-conflict,
    duration, and unknown-bucket rules.
  LeagueAPIEndpoint — /api/boards/<id>/league/ shape and 404/empty rules.
  CLILeagueOutputModes — testing_tools/league_table.py --brief/--json/
    --since-spec.
"""
import datetime
import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

TESTING_TOOLS_DIR = (
    Path(__file__).resolve().parent.parent / "testing_tools"
)
if str(TESTING_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTING_TOOLS_DIR))

import django  # noqa: E402

django.setup()

from django.test import SimpleTestCase, TestCase  # noqa: E402

from tasks.models import (  # noqa: E402
    Board,
    MergeAttempt,
    MergeMode,
    MergeOutcome,
    MergeTrigger,
    Spec,
    Task,
    TaskHistory,
    TaskStatus,
    User,
    UserRole,
)
from tasks.pricing import estimate_task_cost  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────


def _make_agent(name):
    user, _ = User.objects.get_or_create(
        email=f"{name}@odin.agent",
        defaults={"name": name, "role": UserRole.AGENT, "is_active": True,
                  "is_admin": False},
    )
    return user


def _make_operator(email="operator@example.com"):
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"name": email.split("@")[0], "role": UserRole.HUMAN,
                  "is_active": True, "is_admin": False},
    )
    return user


def _add_history(task, field_name, old_value, new_value, changed_by):
    return TaskHistory.objects.create(
        task=task,
        field_name=field_name,
        old_value=str(old_value),
        new_value=str(new_value),
        changed_by=changed_by,
    )


def _make_landed_task(
    board,
    assignee,
    model_name,
    *,
    status=TaskStatus.DONE,
    rework_count=0,
    last_duration_ms=None,
    spec=None,
    created_by=None,
):
    md = {}
    if rework_count:
        md["rework_count"] = rework_count
    if last_duration_ms is not None:
        md["last_duration_ms"] = last_duration_ms
    return Task.objects.create(
        board=board,
        spec=spec,
        title=f"{assignee.name}-{model_name}-task",
        status=status,
        created_by=created_by or f"{assignee.name}@odin.agent",
        assignee=assignee,
        model_name=model_name,
        metadata=md,
    )


# ── Group 1: pure aggregation ──────────────────────────────────────


class PureAggregation(SimpleTestCase):
    """Coverage: Aggregation math on raw rows."""

    def _row(self, agent, model, **kwargs):
        base = {
            "agent": agent,
            "model": model,
            "tasks_landed": 1,
            "hands_free_count": 1,
            "hands_free_pct": 1.0,
            "redo_rounds_avg": 0.0,
            "tokens_median": 0.0,
            "duration_ms_median": 0.0,
            "merge_conflicts_caused": 0,
            "cost_usd_total": 0.0,
        }
        base.update(kwargs)
        return base

    def test_happy_path_two_agents_two_models(self):
        from tasks.league import aggregate_league_rows
        rows = []
        rows.append(self._row("gemini", "gemini-2.5-pro",
                              tasks_landed=2, hands_free_count=2,
                              hands_free_pct=1.0))
        rows.append(self._row("gemini", "gemini-2.5-flash"))
        rows.append(self._row("claude", "claude-opus-4-5"))
        rows.append(self._row("claude", "claude-sonnet-5"))
        out = aggregate_league_rows(rows)
        agents = {r.agent for r in out}
        self.assertEqual(agents, {"gemini", "claude"})
        per_key = {(r.agent, r.model): r for r in out}
        self.assertEqual(per_key[("gemini", "gemini-2.5-pro")].tasks_landed, 2)
        self.assertEqual(per_key[("claude", "claude-opus-4-5")].tasks_landed, 1)

    def test_landed_includes_done_and_testing(self):
        from tasks.league import aggregate_league_rows
        rows = [
            self._row("gemini", "m1", tasks_landed=4, hands_free_count=4,
                      hands_free_pct=1.0),
            self._row("claude", "m2"),
            self._row("claude", "m2"),
        ]
        out = aggregate_league_rows(rows)
        claude_row = next(r for r in out if r.agent == "claude")
        self.assertEqual(claude_row.tasks_landed, 2)

    def test_operator_takeover_lowers_hands_free_pct(self):
        from tasks.league import aggregate_league_rows
        rows = [
            self._row("gemini", "m1", tasks_landed=1, hands_free_count=1,
                      hands_free_pct=1.0),
            self._row("gemini", "m1", tasks_landed=1, hands_free_count=1,
                      hands_free_pct=1.0),
            self._row("gemini", "m1", tasks_landed=1, hands_free_count=1,
                      hands_free_pct=1.0),
            self._row("gemini", "m1", tasks_landed=1, hands_free_count=0,
                      hands_free_pct=0.0),
        ]
        out = aggregate_league_rows(rows)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row.tasks_landed, 4)
        self.assertEqual(row.hands_free_count, 3)
        self.assertAlmostEqual(row.hands_free_pct, 3 / 4, places=2)

    def test_redo_rounds_average_from_metadata(self):
        from tasks.league import aggregate_league_rows
        rows = [
            self._row("gemini", "m1", redo_rounds_avg=2.0),
            self._row("gemini", "m1", redo_rounds_avg=4.0),
            self._row("claude", "m2", redo_rounds_avg=0.0),
        ]
        out = aggregate_league_rows(rows)
        gemini = next(r for r in out if r.agent == "gemini")
        self.assertEqual(gemini.tasks_landed, 2)
        self.assertAlmostEqual(gemini.redo_rounds_avg, 3.0, places=2)

    def test_capture_gap_yields_zero_median(self):
        from tasks.league import aggregate_league_rows
        rows = [
            self._row("gemini", "m1", tokens_median=0.0, duration_ms_median=0.0),
        ]
        out = aggregate_league_rows(rows)
        self.assertEqual(out[0].tokens_median, 0)
        self.assertEqual(out[0].duration_ms_median, 0)

    def test_missing_model_bucketed_as_unknown(self):
        from tasks.league import aggregate_league_rows
        rows = [
            {"agent": "gemini", "model": "unknown", "tasks_landed": 1,
             "hands_free_count": 1, "hands_free_pct": 1.0,
             "redo_rounds_avg": 0.0, "tokens_median": 0.0,
             "duration_ms_median": 0.0, "merge_conflicts_caused": 0,
             "cost_usd_total": 0.0},
        ]
        out = aggregate_league_rows(rows)
        self.assertEqual(out[0].model, "unknown")

    def test_empty_input_returns_empty_list(self):
        from tasks.league import aggregate_league_rows
        self.assertEqual(aggregate_league_rows([]), [])


# ── Group 2: Django ORM compute ────────────────────────────────────


class BoardScopedCompute(TestCase):
    """Coverage: Board-scoped aggregation against the Django ORM."""

    def setUp(self):
        self.board = Board.objects.create(
            name="League Test Board", working_dir="/tmp/league"
        )
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_league",
            title="League spec",
        )
        self.claude = _make_agent("claude")
        self.gemini = _make_agent("gemini")

    def test_three_agents_three_models_yields_three_rows(self):
        from tasks.league import compute_league_for_board
        agents = [(_make_agent("lg_a"), "claude-opus-4-5"),
                  (_make_agent("lg_b"), "claude-sonnet-5"),
                  (_make_agent("lg_c"), "gemini-2.5-pro")]
        for agent, model in agents:
            t = _make_landed_task(self.board, agent, model, spec=self.spec)
            _add_history(t, "status", "EXECUTING", "DONE",
                         f"{agent.name}@odin.agent")
        rows = compute_league_for_board(board=self.board)
        names = sorted([(r.agent, r.model) for r in rows])
        self.assertEqual(names, sorted([
            ("lg_a", "claude-opus-4-5"),
            ("lg_b", "claude-sonnet-5"),
            ("lg_c", "gemini-2.5-pro"),
        ]))

    def test_testing_status_counts_as_landed(self):
        from tasks.league import compute_league_for_board
        for _ in range(2):
            t = _make_landed_task(self.board, self.claude, "claude-opus-4-5",
                                  spec=self.spec,
                                  status=TaskStatus.TESTING)
            _add_history(t, "status", "EXECUTING", "TESTING",
                         "claude@odin.agent")
        rows = compute_league_for_board(board=self.board)
        claude_rows = [r for r in rows if r.agent == "claude"]
        self.assertEqual(len(claude_rows), 1)
        self.assertEqual(claude_rows[0].tasks_landed, 2)

    def test_failed_excluded_from_league(self):
        from tasks.league import compute_league_for_board
        t = _make_landed_task(self.board, self.claude, "claude-opus-4-5",
                              spec=self.spec, status=TaskStatus.FAILED)
        _add_history(t, "status", "EXECUTING", "FAILED",
                     "claude@odin.agent")
        rows = compute_league_for_board(board=self.board)
        self.assertEqual(rows, [])

    def test_operator_takeover_detected(self):
        from tasks.league import compute_league_for_board
        op = _make_operator()
        t = _make_landed_task(self.board, self.claude, "claude-opus-4-5",
                              spec=self.spec)
        _add_history(t, "status", "EXECUTING", "DONE", op.email)
        rows = compute_league_for_board(board=self.board)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].hands_free_count, 0)
        self.assertEqual(rows[0].tasks_landed, 1)
        self.assertEqual(rows[0].hands_free_pct, 0.0)

    def test_window_since_spec_filters_old_tasks(self):
        from tasks.league import compute_league_for_board
        old = Spec.objects.create(
            board=self.board, odin_id="zzz_old_spec", title="Older",
        )
        new = Spec.objects.create(
            board=self.board, odin_id="aaa_new_spec", title="Newer",
        )
        t_old = _make_landed_task(self.board, self.claude, "claude-opus-4-5",
                                  spec=old)
        _add_history(t_old, "status", "EXECUTING", "DONE",
                     "claude@odin.agent")
        t_new = _make_landed_task(self.board, self.gemini, "gemini-2.5-pro",
                                  spec=new)
        _add_history(t_new, "status", "EXECUTING", "DONE",
                     "gemini@odin.agent")
        rows = compute_league_for_board(
            board=self.board, since_spec="aaa_new_spec",
        )
        agents = sorted([r.agent for r in rows])
        self.assertEqual(agents, ["gemini"])
        self.assertEqual(rows[0].tasks_landed, 1)

    def test_window_no_matching_spec_returns_empty(self):
        from tasks.league import compute_league_for_board
        t = _make_landed_task(self.board, self.claude, "claude-opus-4-5",
                              spec=self.spec)
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent")
        rows = compute_league_for_board(
            board=self.board, since_spec="zzz_nothing_matches",
        )
        self.assertEqual(rows, [])

    def test_cost_total_aggregates_pricing(self):
        from tasks.league import compute_league_for_board
        t = _make_landed_task(self.board, self.claude, "claude-opus-4-8",
                              spec=self.spec)
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent")
        trace_text = "\n".join([
            json.dumps({"type": "assistant",
                        "message": {"id": "m1", "type": "message",
                                    "role": "assistant", "content": []}}),
            json.dumps({"type": "result", "subtype": "success",
                        "usage": {"input_tokens": 1_000_000,
                                  "output_tokens": 500_000,
                                  "total_tokens": 1_500_000}}),
        ])
        from tasks.models import TaskComment
        TaskComment.objects.create(
            task=t,
            author_email="claude@odin.agent",
            content=trace_text,
            comment_type="status_update",
            attachments=["trace:execution_jsonl"],
        )
        rows = compute_league_for_board(board=self.board)
        claude_row = next(r for r in rows if r.agent == "claude")
        expected = estimate_task_cost("claude-opus-4-8", 1_000_000, 500_000)
        self.assertIsNotNone(expected)
        self.assertGreater(claude_row.cost_usd_total, 0)
        self.assertAlmostEqual(claude_row.cost_usd_total, expected, places=4)

    def test_merge_conflicts_counted(self):
        from tasks.league import compute_league_for_board
        agent = _make_agent("lg_merge")
        tasks = []
        for _ in range(3):
            t = _make_landed_task(self.board, agent, "claude-opus-4-5",
                                  spec=self.spec)
            _add_history(t, "status", "EXECUTING", "DONE",
                         "lg_merge@odin.agent")
            tasks.append(t)
        MergeAttempt.objects.create(
            task=tasks[0], spec=self.spec,
            trigger=MergeTrigger.REFLECTION_PASS,
            mode=MergeMode.AGENT, outcome=MergeOutcome.CONFLICT,
            started_at=datetime.datetime(2026, 7, 8, 9, 0,
                                         tzinfo=datetime.timezone.utc),
            finished_at=datetime.datetime(2026, 7, 8, 9, 5,
                                          tzinfo=datetime.timezone.utc),
        )
        MergeAttempt.objects.create(
            task=tasks[1], spec=self.spec,
            trigger=MergeTrigger.REFLECTION_PASS,
            mode=MergeMode.AGENT, outcome=MergeOutcome.CONFLICT,
            started_at=datetime.datetime(2026, 7, 8, 9, 0,
                                         tzinfo=datetime.timezone.utc),
            finished_at=datetime.datetime(2026, 7, 8, 9, 5,
                                          tzinfo=datetime.timezone.utc),
        )
        MergeAttempt.objects.create(
            task=tasks[2], spec=self.spec,
            trigger=MergeTrigger.REFLECTION_PASS,
            mode=MergeMode.AGENT, outcome=MergeOutcome.MERGED,
            started_at=datetime.datetime(2026, 7, 8, 9, 0,
                                         tzinfo=datetime.timezone.utc),
            finished_at=datetime.datetime(2026, 7, 8, 9, 5,
                                          tzinfo=datetime.timezone.utc),
        )
        rows = compute_league_for_board(board=self.board)
        m_row = next(r for r in rows if r.agent == "lg_merge")
        self.assertEqual(m_row.tasks_landed, 3)
        self.assertEqual(m_row.merge_conflicts_caused, 2)

    def test_duration_median_from_metadata(self):
        from tasks.league import compute_league_for_board
        agent = _make_agent("lg_dur")
        durations = [1000, 3000, 2000]
        for ms in durations:
            t = _make_landed_task(self.board, agent, "claude-opus-4-5",
                                  spec=self.spec, last_duration_ms=ms)
            _add_history(t, "status", "EXECUTING", "DONE",
                         "lg_dur@odin.agent")
        rows = compute_league_for_board(board=self.board)
        d_row = next(r for r in rows if r.agent == "lg_dur")
        self.assertEqual(d_row.tasks_landed, 3)
        self.assertEqual(d_row.duration_ms_median, 2000)

    def test_unknown_agent_when_no_assignee(self):
        from tasks.league import compute_league_for_board
        t = Task.objects.create(
            board=self.board, spec=self.spec,
            title="orphan",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=None,
            model_name="claude-opus-4-5",
            metadata={"last_duration_ms": 60_000},
        )
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent")
        rows = compute_league_for_board(board=self.board)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].agent, "unknown")

    def test_unknown_model_when_no_model_name(self):
        from tasks.league import compute_league_for_board
        t = Task.objects.create(
            board=self.board, spec=self.spec,
            title="no-model",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            model_name=None,
            metadata={"last_duration_ms": 60_000},
        )
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent")
        rows = compute_league_for_board(board=self.board)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].model, "unknown")


# ── Group 3: REST endpoint ─────────────────────────────────────────


class LeagueAPIEndpoint(TestCase):
    """Coverage: API endpoint shape and behavior."""

    def setUp(self):
        self.board = Board.objects.create(
            name="REST League Board", working_dir="/tmp/rest-league"
        )

    def test_endpoint_returns_200_and_rows(self):
        claude = _make_agent("claude")
        spec = Spec.objects.create(
            board=self.board, odin_id="sp_rest_league", title="RL",
        )
        for _ in range(2):
            t = _make_landed_task(self.board, claude, "claude-opus-4-5",
                                  spec=spec)
            _add_history(t, "status", "EXECUTING", "DONE",
                         "claude@odin.agent")
        resp = self.client.get(f"/api/boards/{self.board.id}/league/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("rows", data)
        self.assertIn("meta", data)
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        for field in ("agent", "model", "tasks_landed", "hands_free_count",
                      "hands_free_pct", "redo_rounds_avg", "tokens_median",
                      "duration_ms_median", "merge_conflicts_caused",
                      "cost_usd_total"):
            self.assertIn(field, row)
        self.assertEqual(row["agent"], "claude")
        self.assertEqual(row["tasks_landed"], 2)

    def test_endpoint_404_on_missing_board(self):
        resp = self.client.get("/api/boards/99999/league/")
        self.assertEqual(resp.status_code, 404)
        body = resp.json() if resp.get("Content-Type", "").startswith(
            "application/json"
        ) else {}
        self.assertIn("detail", body)

    def test_endpoint_empty_board_returns_empty_rows(self):
        resp = self.client.get(f"/api/boards/{self.board.id}/league/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["rows"], [])

    def test_endpoint_since_spec_query_param(self):
        spec_old = Spec.objects.create(
            board=self.board, odin_id="zzz_old", title="Old",
        )
        spec_new = Spec.objects.create(
            board=self.board, odin_id="aaa_new", title="New",
        )
        claude = _make_agent("claude")
        gemini = _make_agent("gemini")
        t1 = _make_landed_task(self.board, claude, "claude-opus-4-5",
                               spec=spec_old)
        _add_history(t1, "status", "EXECUTING", "DONE", "claude@odin.agent")
        t2 = _make_landed_task(self.board, gemini, "gemini-2.5-pro",
                               spec=spec_new)
        _add_history(t2, "status", "EXECUTING", "DONE", "gemini@odin.agent")
        resp = self.client.get(
            f"/api/boards/{self.board.id}/league/?since_spec=aaa_new"
        )
        data = resp.json()
        agents = {row["agent"] for row in data["rows"]}
        self.assertEqual(agents, {"gemini"})


# ── Group 4: CLI script ────────────────────────────────────────────


class CLILeagueOutputModes(TestCase):
    """Coverage: CLI output modes."""

    def setUp(self):
        self.board = Board.objects.create(
            name="CLI League Board", working_dir="/tmp/cli-league",
        )
        claude = _make_agent("claude")
        spec = Spec.objects.create(
            board=self.board, odin_id="sp_cli_league", title="CLI",
        )
        t = _make_landed_task(
            self.board, claude, "claude-opus-4-5", spec=spec,
            last_duration_ms=180_000,
        )
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent")

    def _run_cli(self, *argv):
        from league_table import main
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["league_table.py", str(self.board.id), *list(argv)]
        try:
            with redirect_stdout(buf):
                main()
        finally:
            sys.argv = old_argv
        return buf.getvalue()

    def test_cli_runs_without_error_on_empty_board(self):
        empty = Board.objects.create(
            name="CLI League Empty", working_dir="/tmp/cli-league-empty",
        )
        from league_table import main
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["league_table.py", str(empty.id)]
        try:
            with redirect_stdout(buf):
                main()
        finally:
            sys.argv = old_argv

    def test_cli_json_outputs_valid_json(self):
        out = self._run_cli("--json")
        data = json.loads(out)
        self.assertIn("rows", data)

    def test_cli_brief_outputs_short_lines(self):
        out = self._run_cli("--brief")
        lines = [ln for ln in out.splitlines() if ln.strip()]
        for ln in lines:
            self.assertLessEqual(
                len(ln), 120,
                msg=f"line too long: {ln!r}",
            )

    def test_cli_since_spec_filter(self):
        spec_new = Spec.objects.create(
            board=self.board, odin_id="aaa_cli_new", title="New",
        )
        claude = _make_agent("claude")
        gemini = _make_agent("gemini")
        for spec, agent in [
            (Spec.objects.get(board=self.board, odin_id="sp_cli_league"),
             claude),
            (spec_new, gemini),
        ]:
            model = "claude-opus-4-5" if agent is claude else "gemini-2.5-pro"
            t = _make_landed_task(self.board, agent, model, spec=spec)
            _add_history(t, "status", "EXECUTING", "DONE",
                         f"{agent.name}@odin.agent")
        out = self._run_cli("--json", "--since-spec", "aaa_cli_new")
        data = json.loads(out)
        agents = {row.get("agent") for row in data["rows"]}
        self.assertEqual(agents, {"gemini"})
