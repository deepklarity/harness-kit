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

--html composes the handover page: TLDR, event timeline and the TESTING
shelf for this board.
"""
import sys

from _utils import setup_django, print_json

setup_django()

from tasks.board_story import build_board_story, render_html  # noqa: E402
from tasks.models import Board  # noqa: E402


def get_testing_shelf(board):
    """Tasks currently on the TESTING shelf for this board — merged and
    waiting on the human's optional DONE flip."""
    from tasks.models import Task, TaskStatus

    return [
        {"id": t.id, "title": t.title}
        for t in Task.objects.filter(board=board, status=TaskStatus.TESTING).order_by("id")
    ]


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
        print(render_html(story, testing_shelf=get_testing_shelf(board)))
        return
    if mode == "brief":
        _print_brief(story)
        return
    _print_standard(story)


if __name__ == "__main__":
    main()
