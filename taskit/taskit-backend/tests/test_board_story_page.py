"""Tests for the composed board-story handover page (task #251).

testing_tools/board_story.py --html grows from a bare TLDR+timeline page
into the full two-minute handover: a bucket scoreboard parsed from
docs/fable_roadmap/fable_roadmap.md (the table is the source of truth,
never duplicated), the TESTING shelf (tasks merged and waiting on the
human's optional DONE flip), and the decisions only the human can make
(parsed from BACKLOG.md's "User decisions still open" section).

Covers: the two markdown parsers against fixture text (not the real
docs, so these stay fast and don't drift with roadmap edits), the
TESTING-shelf query, and tasks.board_story.render_html composing all of
it — including the case where a section has nothing to show (must render
an honest em-dash placeholder, never disappear) and the CSS theme tokens
both themes rely on.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

# Put testing_tools on the path so we can import the CLI script under test,
# same pattern as tests/test_autonomy_metrics.py.
TESTING_TOOLS_DIR = Path(__file__).resolve().parent.parent / "testing_tools"
if str(TESTING_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTING_TOOLS_DIR))

from .base import APITestCase
from tasks.models import TaskStatus


ROADMAP_FIXTURE = """# Fable roadmap

Some prose before the table.

| Bucket | Score | One line |
|---|---|---|
| [Trust](buckets/trust.md) | 8/10 | The kit must not lie or panic |
| [Memory](buckets/memory.md) | 5/10 | It records everything and uses nothing |

Prose after the table.
"""

BACKLOG_FIXTURE = """# BACKLOG

## Now

Some wave text.

## User decisions still open

1. **A dedicated always-on Linux box.** Long wrapped
   text that spans
   multiple lines.
2. **Second decision.** Shorter one.

Resolved by the user, encoded: something already answered — not waiting.
"""

BACKLOG_NO_DECISIONS_FIXTURE = """# BACKLOG

## Now

Nothing here.
"""


class BucketScoreboardParsingTests(APITestCase):
    def test_parses_rows_in_table_order(self):
        from board_story import parse_bucket_scoreboard

        rows = parse_bucket_scoreboard(ROADMAP_FIXTURE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Trust")
        self.assertEqual(rows[0]["link"], "buckets/trust.md")
        self.assertEqual(rows[0]["score"], "8/10")
        self.assertIn("lie or panic", rows[0]["one_line"])
        self.assertEqual(rows[1]["name"], "Memory")
        self.assertEqual(rows[1]["score"], "5/10")

    def test_empty_when_no_table(self):
        from board_story import parse_bucket_scoreboard

        self.assertEqual(parse_bucket_scoreboard("# Nothing here"), [])


class BacklogDecisionsParsingTests(APITestCase):
    def test_parses_numbered_items_joins_wrapped_lines(self):
        from board_story import parse_backlog_decisions

        decisions = parse_backlog_decisions(BACKLOG_FIXTURE)
        self.assertEqual(len(decisions), 2)
        self.assertIn("Linux box", decisions[0])
        self.assertIn("multiple lines", decisions[0])
        self.assertNotIn("\n", decisions[0])
        self.assertIn("Second decision", decisions[1])

    def test_excludes_resolved_paragraph(self):
        from board_story import parse_backlog_decisions

        decisions = parse_backlog_decisions(BACKLOG_FIXTURE)
        joined = " ".join(decisions)
        self.assertNotIn("already answered", joined)

    def test_empty_when_no_section(self):
        from board_story import parse_backlog_decisions

        self.assertEqual(parse_backlog_decisions(BACKLOG_NO_DECISIONS_FIXTURE), [])


class TestingShelfTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_lists_testing_tasks_by_id(self):
        from board_story import get_testing_shelf

        t1 = self.make_task(self.board, title="Landed one", status=TaskStatus.TESTING)
        t2 = self.make_task(self.board, title="Landed two", status=TaskStatus.TESTING)
        self.make_task(self.board, title="Still open", status=TaskStatus.TODO)

        shelf = get_testing_shelf(self.board)
        self.assertEqual([s["id"] for s in shelf], sorted([t1.id, t2.id]))
        titles = {s["title"] for s in shelf}
        self.assertEqual(titles, {"Landed one", "Landed two"})

    def test_empty_when_no_testing_tasks(self):
        from board_story import get_testing_shelf

        self.assertEqual(get_testing_shelf(self.board), [])


class FullPageCompositionTests(APITestCase):
    """tasks.board_story.render_html composes TLDR + timeline + scoreboard
    + shelf + decisions into one page."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)

    def _story(self):
        from tasks.board_story import build_board_story

        return build_board_story(self.board)

    def test_full_page_includes_all_sections_with_data(self):
        from tasks.board_story import render_html

        bucket_rows = [
            {"name": "Trust", "link": "buckets/trust.md", "score": "8/10",
             "one_line": "The kit must not lie or panic"},
        ]
        testing_shelf = [{"id": 42, "title": "Shipped thing"}]
        decisions = ["Provision a Linux box for the factory."]

        html = render_html(
            self._story(), bucket_rows=bucket_rows,
            testing_shelf=testing_shelf, decisions=decisions,
        )

        self.assertIn("Trust", html)
        self.assertIn("8/10", html)
        self.assertIn("buckets/trust.md", html)
        self.assertIn("#42", html)
        self.assertIn("Shipped thing", html)
        self.assertIn("Provision a Linux box", html)

    def test_empty_sections_render_honestly_not_hidden(self):
        from tasks.board_story import render_html

        html = render_html(
            self._story(), bucket_rows=None, testing_shelf=None, decisions=None,
        )

        # Section headers still present even with nothing to show — never
        # silently dropped.
        self.assertIn("Bucket scoreboard", html)
        self.assertIn("TESTING shelf", html)
        self.assertIn("Waiting on you", html)
        # Honest em-dash placeholder rather than an empty/missing section.
        self.assertIn("—", html)

    def test_theme_tokens_present_for_both_themes(self):
        from tasks.board_story import render_html

        html = render_html(self._story())

        self.assertIn("prefers-color-scheme", html)
        self.assertIn("data-theme", html)
        self.assertIn("--bg", html)
        self.assertIn("--ink", html)
        self.assertIn("!important", html)

    def test_backward_compatible_defaults(self):
        """Existing call sites that only pass `story` still work."""
        from tasks.board_story import render_html

        html = render_html(self._story())
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("TLDR", html)
