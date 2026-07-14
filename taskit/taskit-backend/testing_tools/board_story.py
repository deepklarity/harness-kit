#!/usr/bin/env python
"""Board story diagnostic — the two-minute handover.

Aggregates events across all specs on a board (or all boards) since a
timestamp: landings, failures, merge conflicts, reflection escalations,
wave open/close. Shares one builder with the API endpoint
(tasks/board_story.py) so the CLI and the endpoint never drift.

The operator one-liner — run it, publish the file, done:
    cd taskit/taskit-backend && python testing_tools/board_story.py <board_id> --since <iso> --html > handover.html

Usage:
    cd taskit/taskit-backend
    python testing_tools/board_story.py [board_id] [--since <iso>]
    python testing_tools/board_story.py 5 --since 2026-01-01T00:00:00+00:00 --brief
    python testing_tools/board_story.py 5 --html > handover.html
    python testing_tools/board_story.py 5 --since <iso> --json

--html composes the full handover page: TLDR, event timeline, the bucket
scoreboard (parsed straight from docs/fable_roadmap/fable_roadmap.md — it
is the source of truth, never duplicated here), the TESTING shelf for
this board, and the decisions only the human can make (parsed from
BACKLOG.md's "User decisions still open" section).
"""
import re
import sys
from pathlib import Path

from _utils import setup_django, print_json

setup_django()

from tasks.board_story import build_board_story, render_html  # noqa: E402
from tasks.models import Board  # noqa: E402


# Repo root walks up from this file: testing_tools/board_story.py → backend → taskit → repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROADMAP_DOC = _REPO_ROOT / "docs" / "fable_roadmap" / "fable_roadmap.md"
_BACKLOG_DOC = _REPO_ROOT / "docs" / "fable_roadmap" / "BACKLOG.md"

_BUCKET_LINK_RE = re.compile(r'^\[(?P<name>.+?)\]\((?P<link>.+?)\)$')
_TABLE_SEP_RE = re.compile(r'^\|[\s:-]+\|[\s:-]+\|[\s:-]+\|$')


def parse_bucket_scoreboard(markdown_text):
    """Parse the ``| Bucket | Score | One line |`` table out of
    fable_roadmap.md. The table is the source of truth — this reads it
    in table order, it never duplicates the scores elsewhere.

    Returns a list of ``{"name", "link", "score", "one_line"}`` dicts.
    """
    rows = []
    in_table = False
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not in_table:
            if stripped.startswith("|") and "Bucket" in stripped and "Score" in stripped:
                in_table = True
            continue
        if _TABLE_SEP_RE.match(stripped):
            continue
        if not stripped.startswith("|"):
            break
        cols = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cols) < 3:
            continue
        bucket_raw, score, one_line = cols[0], cols[1], cols[2]
        link_match = _BUCKET_LINK_RE.match(bucket_raw)
        if link_match:
            name, link = link_match.group("name"), link_match.group("link")
        else:
            name, link = bucket_raw, None
        rows.append({"name": name, "link": link, "score": score, "one_line": one_line})
    return rows


_DECISIONS_SECTION_RE = re.compile(
    r'^##\s*User decisions still open\s*$(?P<body>.*?)(?=^##\s|\Z)',
    re.M | re.S,
)
_DECISION_ITEM_RE = re.compile(r'^\d+\.\s+(.*)')


def parse_backlog_decisions(markdown_text):
    """Parse the numbered items under BACKLOG.md's "User decisions still
    open" section — the ones only the human can resolve. Wrapped
    continuation lines (indented, markdown-list style) are joined onto
    their item. The list ends at the first blank line followed by an
    unindented, non-numbered line — e.g. the "Resolved by the user,
    encoded" paragraph, which is answered and not waiting on anyone, so
    it's excluded.
    """
    section_match = _DECISIONS_SECTION_RE.search(markdown_text)
    if not section_match:
        return []

    decisions = []
    current = None
    for line in section_match.group("body").splitlines():
        item_match = _DECISION_ITEM_RE.match(line)
        if item_match:
            if current is not None:
                decisions.append(current)
            current = item_match.group(1)
        elif current is not None and line.strip() and line[:1].isspace():
            current += " " + line.strip()
        elif not line.strip():
            continue
        else:
            break
    if current is not None:
        decisions.append(current)

    return [re.sub(r'\s+', ' ', d).strip() for d in decisions]


def get_testing_shelf(board):
    """Tasks currently on the TESTING shelf for this board — merged and
    waiting on the human's optional DONE flip."""
    from tasks.models import Task, TaskStatus

    return [
        {"id": t.id, "title": t.title}
        for t in Task.objects.filter(board=board, status=TaskStatus.TESTING).order_by("id")
    ]


def _read_doc(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def _parse_args(argv):
    """Parse: [board_id] [--since <iso>] [--brief|--full|--json|--html]"""
    positional = None
    since = None
    mode = "standard"
    skip_next = False
    args = argv[1:]

    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--since" and i + 1 < len(args):
            since = args[i + 1]
            skip_next = True
        elif arg == "--brief":
            mode = "brief"
        elif arg == "--full":
            mode = "full"
        elif arg == "--json":
            mode = "json"
        elif arg == "--html":
            mode = "html"
        elif not arg.startswith("--") and positional is None:
            positional = arg

    return positional, since, mode


def _print_brief(story):
    tldr = story["tldr"]
    since_label = story.get("since") or "all time"
    print(
        f"Board #{story['board_id']} '{story['board_name']}' "
        f"({story['event_count']} events since {since_label}): "
        f"{tldr['landed']} landed ({tldr['hands_free']} hands-free), "
        f"{tldr['incidents']} incidents, "
        f"{tldr['waiting_on_human']} waiting on human"
    )
    for e in story["events"]:
        ts = e["timestamp"].strftime("%m-%d %H:%M") if e["timestamp"] else "????"
        task = f" #{e['task_id']}" if e.get("task_id") else ""
        print(f"  {ts}  {e['kind']:<22} {e['line']}{task}")


def _print_standard(story):
    since_label = story.get("since") or "all time"
    print(f"\n{'=' * 70}")
    print(f"  BOARD STORY: {story['board_name']} (#{story['board_id']})")
    print(f"  Since: {since_label}  |  {story['event_count']} events")
    print(f"{'=' * 70}")
    tldr = story["tldr"]
    print(
        f"  TLDR: {tldr['landed']} landed ({tldr['hands_free']} hands-free) | "
        f"{tldr['incidents']} incidents | "
        f"{tldr['waiting_on_human']} waiting on human"
    )
    print(f"{'-' * 70}")
    for e in story["events"]:
        ts = e["timestamp"].strftime("%Y-%m-%d %H:%M") if e["timestamp"] else "????"
        task = f" [task #{e['task_id']}]" if e.get("task_id") else ""
        spec = f" [spec #{e['spec_id']}]" if e.get("spec_id") else ""
        print(f"  {ts}  {e['kind']:<22} {e['line']}{task}{spec}")
    print()


def main():
    positional, since, mode = _parse_args(sys.argv)

    if positional:
        try:
            board = Board.objects.get(pk=positional)
        except Board.DoesNotExist:
            print(f"Board #{positional} not found.")
            sys.exit(1)
        _render_board(board, since, mode)
    else:
        boards = Board.objects.all().order_by("id")
        if not boards.exists():
            print("No boards found.")
            sys.exit(0)
        for board in boards:
            _render_board(board, since, mode)


def _render_board(board, since, mode):
    story = build_board_story(board, since=since)

    if mode == "json":
        print_json(story)
        return
    if mode == "html":
        bucket_rows = parse_bucket_scoreboard(_read_doc(_ROADMAP_DOC))
        decisions = parse_backlog_decisions(_read_doc(_BACKLOG_DOC))
        testing_shelf = get_testing_shelf(board)
        print(render_html(
            story, bucket_rows=bucket_rows,
            testing_shelf=testing_shelf, decisions=decisions,
        ))
        return
    if mode == "brief":
        _print_brief(story)
        return
    _print_standard(story)


if __name__ == "__main__":
    main()
