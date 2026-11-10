"""Tests for the composed board-story handover page (task #251).

testing_tools/board_story.py --html is the two-minute handover: TLDR,
timeline, the TESTING shelf (tasks merged and waiting on the human's
optional DONE flip), plus optional scoreboard and decisions sections.

Covers: the TESTING-shelf query and tasks.board_story.render_html
composing all of it — including the case where a section has nothing to show (must render
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
