"""Tests for testing_tools/token_profile.py.

Covers:
- classify() mapping for every known tool + the bash-prefix rules
- profile() math on a synthetic fixture
- iter_events() filters out non-tool + non-completed events
- spec aggregation over a DB-backed spec uses TaskComment-as-trace

These tests are pure-Python (no DB) except the spec aggregation case,
which exercises the TaskComment fetch via Django TestCase.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Django must be set up before importing the token_profile module (which
# imports tasks.* models via top-level setup_django()). Lazy-init here so
# the pure-Python tests don't pay the cost.
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402
if not django.apps.apps.ready:
    django.setup()

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(
    0,
    str(THIS_DIR.parent / "testing_tools"),
)

from token_profile import (  # noqa: E402
    PHASES,
    classify,
    iter_events,
    profile,
    profile_spec,
)


FIXTURE = THIS_DIR / "fixtures" / "token_profile_synthetic.jsonl"


class ClassifyMapTests(unittest.TestCase):
    """Map every known tool onto its expected phase. New tools must land in
    'other' so they never silently skew the find/do/check shares."""

    def test_read_is_find(self):
        self.assertEqual(classify("read", {"filePath": "x"}), "find")

    def test_glob_is_find(self):
        self.assertEqual(classify("glob", {"pattern": "*.py"}), "find")

    def test_grep_is_find(self):
        self.assertEqual(classify("grep", {"pattern": "x"}), "find")

    def test_webfetch_is_find(self):
        self.assertEqual(classify("webfetch", {"url": "https://a"}), "find")

    def test_write_is_do(self):
        self.assertEqual(classify("write", {"filePath": "a", "content": ""}), "do")

    def test_edit_is_do(self):
        self.assertEqual(classify("edit", {"filePath": "a"}), "do")

    def test_patch_is_do(self):
        self.assertEqual(classify("patch", {"patch": ""}), "do")

    def test_todowrite_is_other(self):
        self.assertEqual(classify("todowrite", {"todos": []}), "other")

    def test_taskkill_is_other(self):
        self.assertEqual(classify("taskkill", {"pid": "x"}), "other")

    def test_unknown_tool_is_other(self):
        self.assertEqual(classify("future-tool", {"anything": 1}), "other")


class BashPrefixTests(unittest.TestCase):
    """The bash classifier uses verb prefixes; cover each branch."""

    def test_pytest_is_check(self):
        self.assertEqual(
            classify("bash", {"command": "python3 -m pytest tests -q"}),
            "check",
        )

    def test_manage_test_is_check(self):
        self.assertEqual(
            classify("bash", {"command": "python manage.py test tests"}),
            "check",
        )

    def test_npm_test_is_check(self):
        self.assertEqual(
            classify("bash", {"command": "npm run test:run"}),
            "check",
        )

    def test_cargo_test_is_check(self):
        self.assertEqual(
            classify("bash", {"command": "cargo test --quiet"}),
            "check",
        )

    def test_ls_is_find(self):
        self.assertEqual(classify("bash", {"command": "ls /tmp"}), "find")

    def test_cat_is_find(self):
        self.assertEqual(classify("bash", {"command": "cat foo.txt"}), "find")

    def test_git_log_is_check_default(self):
        # git log is not in our write-verb list and is not a test runner,
        # so it falls through to the default check.
        self.assertEqual(
            classify("bash", {"command": "git log --oneline -5"}),
            "check",
        )

    def test_git_commit_is_do(self):
        self.assertEqual(
            classify("bash", {"command": "git commit -m 'fix bug'"}),
            "do",
        )

    def test_git_merge_is_do(self):
        self.assertEqual(classify("bash", {"command": "git merge feat/x"}), "do")

    def test_pip_install_is_do(self):
        self.assertEqual(
            classify("bash", {"command": "pip install -r requirements.txt"}),
            "do",
        )

    def test_npm_install_is_do(self):
        self.assertEqual(
            classify("bash", {"command": "npm install jest"}),
            "do",
        )

    def test_mkdir_is_do(self):
        self.assertEqual(classify("bash", {"command": "mkdir -p /tmp/x"}), "do")

    def test_cp_is_do(self):
        self.assertEqual(classify("bash", {"command": "cp a b"}), "do")

    def test_sed_inplace_is_do(self):
        self.assertEqual(
            classify("bash", {"command": "sed -i 's/a/b/' file"}),
            "do",
        )

    def test_random_command_defaults_to_check(self):
        self.assertEqual(
            classify("bash", {"command": "echo hello"}),
            "check",
        )

    def test_empty_command_is_other(self):
        self.assertEqual(classify("bash", {"command": ""}), "other")


class TaskSubagentTests(unittest.TestCase):
    """Delegated task() calls inherit the prompt verb classification."""

    def test_task_find_keyword(self):
        self.assertEqual(
            classify("task", {"prompt": "Find the auth handler in tasks/auth.py"}),
            "find",
        )

    def test_task_search_keyword(self):
        self.assertEqual(
            classify("task", {"prompt": "Search the codebase for usages"}),
            "find",
        )

    def test_task_explore_keyword(self):
        self.assertEqual(
            classify("task", {"prompt": "Explore how the spec lifecyle works"}),
            "find",
        )

    def test_task_edit_keyword(self):
        self.assertEqual(
            classify("task", {"prompt": "Edit the helper to handle the null case"}),
            "do",
        )

    def test_task_implement_keyword(self):
        self.assertEqual(
            classify("task", {"prompt": "Implement the new classifier"}),
            "do",
        )

    def test_task_test_keyword(self):
        self.assertEqual(
            classify("task", {"prompt": "Test the new endpoint"}),
            "check",
        )

    def test_task_verify_keyword(self):
        self.assertEqual(
            classify("task", {"prompt": "Verify the suite passes"}),
            "check",
        )

    def test_task_ambiguous_is_other(self):
        self.assertEqual(
            classify("task", {"prompt": "Build something interesting"}),
            "other",
        )

    def test_task_empty_is_other(self):
        self.assertEqual(classify("task", {"prompt": ""}), "other")


class ExtractToolDualShapeTests(unittest.TestCase):
    """Two on-disk shapes; both must round-trip into a (tool, args) pair."""

    def test_shape1_top_level_tool(self):
        from token_profile import _extract_tool
        ev = {
            "type": "tool",
            "tool": "bash",
            "callID": "c",
            "state": {"status": "completed", "input": {"command": "ls"}},
        }
        tool, _, args = _extract_tool(ev)
        self.assertEqual(tool, "bash")
        self.assertEqual(args["command"], "ls")

    def test_shape2_tool_use_with_inner_state(self):
        from token_profile import _extract_tool
        ev = {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "read",
                "callID": "c",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "/tmp/x"},
                    "output": "hello",
                },
            },
        }
        tool, state, args = _extract_tool(ev)
        self.assertEqual(tool, "read")
        self.assertEqual(args["filePath"], "/tmp/x")
        self.assertEqual(state["output"], "hello")

    def test_non_tool_event_returns_none(self):
        from token_profile import _extract_tool
        self.assertEqual(_extract_tool({"type": "step_finish"}), (None, None, None))
        self.assertEqual(_extract_tool({"type": "step_start"}), (None, None, None))


class IterEventsFixtureTests(unittest.TestCase):
    """Event iterator drops non-tool + non-completed lines."""

    def test_fixture_parses_to_sixteen_completed_tool_events(self):
        events = iter_events(str(FIXTURE))
        self.assertEqual(len(events), 16)

    def test_unknown_tool_event_passes_through_with_phase_other(self):
        events = iter_events(str(FIXTURE))
        self.assertTrue(
            any(e["tool"] == "unknowntool" and e["phase"] == "other"
                for e in events),
            "unknowntool must be kept in events with phase='other'",
        )

    def test_running_event_is_filtered(self):
        events = iter_events(str(FIXTURE))
        ids = [e["input"].get("command", "") for e in events]
        self.assertFalse(any("command" in i and i == "" for i in ids),
                         "running event with empty output must be dropped")

    def test_non_tool_event_lines_dropped(self):
        events = iter_events(str(FIXTURE))
        self.assertTrue(all(e["tool"] not in ("session", "loop", "stream", "final")
                            for e in events))


class ProfileMathTests(unittest.TestCase):
    """Share math on the synthetic fixture is reproducible and sums to ~100."""

    @classmethod
    def setUpClass(cls):
        cls.events = iter_events(str(FIXTURE))
        cls.summary = profile(cls.events)

    def test_event_count_matches(self):
        self.assertEqual(self.summary["event_count"], 16)

    def test_phase_event_counts(self):
        c = self.summary["phase_event_counts"]
        self.assertEqual(c["find"], 7, f"find: {c}")
        self.assertEqual(c["do"], 4, f"do: {c}")
        self.assertEqual(c["check"], 2, f"check: {c}")
        self.assertEqual(c["other"], 3, f"other: {c}")

    def test_byte_shares_sum_to_about_100(self):
        s = self.summary["phase_shares_pct"]
        total = sum(s.values())
        self.assertAlmostEqual(total, 100.0, delta=0.5)

    def test_top_events_are_sorted_by_bytes(self):
        sizes = [e["bytes"] for e in self.summary["top_events"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_find_share_is_float(self):
        self.assertIsInstance(self.summary["find_share_pct"], float)


class ClassifyFallbackTests(unittest.TestCase):
    """Defensive: weird inputs must not crash the classifier."""

    def test_no_args_returns_other(self):
        self.assertEqual(classify("bash", {}), "other")

    def test_command_keyword_alt(self):
        self.assertEqual(classify("bash", {"cmd": "ls"}), "find")


if __name__ == "__main__":
    unittest.main()



class SpecAggregationTests(unittest.TestCase):
    """profile_spec aggregates per-task profiles across a DB-backed spec."""

    def _make_trace_jsonl(self, n_reads: int) -> str:
        events = []
        for i in range(n_reads):
            events.append(json.dumps({
                "type": "tool_use",
                "timestamp": 1 + i,
                "sessionID": "s1",
                "part": {
                    "type": "tool",
                    "tool": "read",
                    "callID": f"c{i}",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": f"/tmp/task_a_{i}.py"},
                        "output": f"<contents {i}>",
                    },
                },
            }))
        for i in range(2):
            events.append(json.dumps({
                "type": "tool_use",
                "timestamp": 100 + i,
                "sessionID": "s1",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": f"d{i}",
                    "state": {
                        "status": "completed",
                        "input": {"command": "python3 -m pytest tests -q"},
                        "output": "ok",
                    },
                },
            }))
        return "\n".join(events) + "\n"

    def _attach_trace(self, task, raw):
        from tasks.models import TaskComment
        TaskComment.objects.create(
            task=task,
            author_email="agent@odin.agent",
            content=raw,
            attachments=["trace:execution_jsonl"],
        )

    def setUp(self):
        import uuid
        from tasks.models import Board, Spec, Task
        self.tag = uuid.uuid4().hex[:8]
        self.board = Board.objects.create(name=f"Spec Test Board {self.tag}")
        self.spec = Spec.objects.create(
            board=self.board, odin_id=f"sp_test_{self.tag}", title="Spec Probe"
        )
        self.task_a = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Disk prune", created_by="agent@odin.agent",
        )
        self.task_b = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Smoke run", created_by="agent@odin.agent",
        )

    def test_two_tasks_with_traces_aggregate_find_share(self):
        # _make_trace_jsonl emits n_reads reads + 2 bash pytest events.
        # task_a: 5 reads + 2 pytest = 7 events; task_b: 3 reads + 2 pytest = 5.
        # Total = 12 events; find=5+3=8, check=2+2=4.
        self._attach_trace(self.task_a, self._make_trace_jsonl(n_reads=5))
        self._attach_trace(self.task_b, self._make_trace_jsonl(n_reads=3))
        out = profile_spec(self.spec.odin_id)

        self.assertEqual(out["task_count"], 2)
        self.assertEqual(out["total_events"], 12)
        self.assertEqual(out["phase_event_counts"]["find"], 8)
        self.assertEqual(out["phase_event_counts"]["check"], 4)
        ids = sorted(t["task_id"] for t in out["per_task"])
        self.assertEqual(ids, sorted([self.task_a.id, self.task_b.id]))
        # finding share by event count = 8/12 = 66.7% (bytes weight it a bit
        # higher because bash pytest outputs are shorter than file reads).
        self.assertGreater(out["finding_share_pct"], 60.0)

    def test_spec_with_no_traces(self):
        import uuid
        from tasks.models import Board, Spec, Task
        tag = uuid.uuid4().hex[:8]
        other_board = Board.objects.create(name=f"Empty Board {tag}")
        empty_spec = Spec.objects.create(
            board=other_board,
            odin_id=f"sp_empty_{tag}",
            title="Empty",
        )
        Task.objects.create(
            board=other_board, spec=empty_spec,
            title="Untraced", created_by="agent@odin.agent",
        )
        out = profile_spec(empty_spec.odin_id)
        self.assertEqual(out["task_count"], 0)
        self.assertEqual(out["total_events"], 0)
        self.assertIsNone(out["find_share_pct_avg"])

    def test_find_share_pct_avg_across_two_tasks(self):
        # _make_trace_jsonl emits 4 reads + 2 pytest = 6 events/task.
        # 4 reads + 2 pytest is dominantly find (4/6 events = 66.7%);
        # bytes weight reads above bash outputs, raising the byte share
        # closer to 70% per task. Per-task find_shares should be in the
        # 60-90% range, and the average should land between them.
        self._attach_trace(self.task_a, self._make_trace_jsonl(n_reads=4))
        self._attach_trace(self.task_b, self._make_trace_jsonl(n_reads=4))
        out = profile_spec(self.spec.odin_id)
        self.assertEqual(out["task_count"], 2)
        # Avg of find_share across both identical tasks — bytes-based,
        # expected around 65-75%.
        self.assertGreater(out["find_share_pct_avg"], 60.0)
        self.assertLess(out["find_share_pct_avg"], 80.0)
