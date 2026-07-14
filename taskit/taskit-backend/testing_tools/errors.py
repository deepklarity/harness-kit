#!/usr/bin/env python
"""Error ledger triage surface (task #222).

Lists the ErrorEvent rows the system has captured — failure_tagger
misses, merge ladder failures, reflection ERROR verdicts, spec-verify
gate crashes, celery task exceptions — grouped by (source, signature)
with counts so an operator can spot the patterns at a glance.

Usage:
    cd taskit/taskit-backend
    python testing_tools/errors.py
    python testing_tools/errors.py --brief
    python testing_tools/errors.py --json
    python testing_tools/errors.py --disposition open
    python testing_tools/errors.py --source merge_failure
    python testing_tools/errors.py --set-disposition <event_id> <fixed|open|non-issue> [--note "..."]
    python testing_tools/errors.py --seed
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _utils import setup_django, parse_args, print_json  # noqa: E402

setup_django()

from tasks.errors import (  # noqa: E402
    group_by_signature,
    import_pending_ledger_entries,
    seed_from_ledger,
    serialize_error_event,
    set_disposition,
)
from tasks.models import ErrorEvent  # noqa: E402


# Repo root walks up from this file: testing_tools/errors.py → backend → taskit → repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_LEDGER_DOC = _REPO_ROOT / "docs" / "patterns" / "error_ledger.md"


def build_payload(*, disposition=None, source=None, include_events=False):
    """Structured payload (filters: disposition, source)."""
    groups = group_by_signature(disposition=disposition, source=source)
    # Add a flat list of all matching events so JSON consumers can drill in
    # without re-querying.
    qs = ErrorEvent.objects.all()
    if disposition:
        qs = qs.filter(disposition=disposition)
    if source:
        qs = qs.filter(source=source)
    events = list(qs.order_by("-created_at", "-id"))
    sources = Counter(e.source for e in events)
    dispositions = Counter(e.disposition for e in events)
    payload = {
        "count": len(events),
        "open_count": dispositions.get("open", 0),
        "by_source": dict(sources),
        "by_disposition": dict(dispositions),
        "group_count": len(groups),
        "groups": groups,
    }
    if include_events:
        payload["events"] = [serialize_error_event(e) for e in events]
    return payload


def list_errors(mode="standard", *, disposition=None, source=None):
    """Print (brief/standard) or return (json) the payload."""
    payload = build_payload(disposition=disposition, source=source)
    if mode == "brief":
        _print_brief(payload)
    elif mode in ("standard", "full"):
        _print_standard(payload)
    return payload


def _print_brief(payload):
    src = ", ".join(f"{c} {k}" for k, c in sorted(payload["by_source"].items())) or "none"
    print(
        f"Error ledger: {payload['count']} entries ({payload['open_count']} open) | "
        f"sources: {src} | {payload['group_count']} signatures"
    )


def _print_standard(payload):
    print(f"\n{'=' * 70}")
    print(f"  ERROR LEDGER")
    print(f"{'=' * 70}")
    print(f"  entries: {payload['count']} (open: {payload['open_count']})")
    if payload["by_source"]:
        src = ", ".join(f"{c} {k}" for k, c in sorted(payload["by_source"].items()))
        print(f"  by source: {src}")
    if payload["by_disposition"]:
        d = ", ".join(f"{c} {k}" for k, c in sorted(payload["by_disposition"].items()))
        print(f"  by disposition: {d}")
    print(f"  {'-' * 66}")
    if not payload["groups"]:
        print("  (no entries)")
        print()
        return
    for g in payload["groups"]:
        print(
            f"  [{g['count']:>2}× {g['source']:<18}] {g['latest_symptom'][:80]}"
        )
    print()


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Error ledger triage (task #222).",
    )
    parser.add_argument("--brief", action="store_true", help="one-line summary")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--full", action="store_true", help="alias for standard")
    parser.add_argument("--disposition", choices=["open", "fixed", "non-issue"],
                        help="filter by disposition")
    parser.add_argument("--source", choices=[
        "failure_tagger", "merge_failure", "reflection_error",
        "gate_crash", "celery_exception",
    ], help="filter by source")
    parser.add_argument("--set-disposition", nargs=2, metavar=("EVENT_ID", "VALUE"),
                        help="update one event's disposition")
    parser.add_argument("--note", default="",
                        help="note for --set-disposition")
    parser.add_argument("--seed", action="store_true",
                        help="import entries from docs/patterns/error_ledger.md")
    parser.add_argument("--ledger-doc", default=str(_DEFAULT_LEDGER_DOC),
                        help="override the ledger doc path for --seed")
    parser.add_argument("--import-pending", action="store_true",
                        help="import the W6 retrospective entries the doc "
                             "kept as narrative; idempotent on (source, source_id)")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.set_disposition:
        event_id, value = args.set_disposition
        try:
            updated = set_disposition(int(event_id), value, note=args.note)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(2)
        except ErrorEvent.DoesNotExist:
            print(f"ErrorEvent {event_id} not found.", file=sys.stderr)
            sys.exit(1)
        print(f"ErrorEvent {updated.id}: disposition={updated.disposition}")
        if updated.disposition_note:
            print(f"  note: {updated.disposition_note}")
        return

    if args.seed:
        path = Path(args.ledger_doc)
        if not path.exists():
            print(f"ledger doc not found: {path}", file=sys.stderr)
            sys.exit(1)
        created = seed_from_ledger(path)
        print(f"Seeded {created} new entries from {path}")
        return

    if args.import_pending:
        created = import_pending_ledger_entries()
        print(f"Imported {created} pending entries")
        return

    if args.brief:
        mode = "brief"
    elif args.json:
        mode = "json"
    elif args.full:
        mode = "standard"
    else:
        mode = "standard"

    payload = list_errors(
        mode=mode, disposition=args.disposition, source=args.source,
    )
    if mode == "json":
        print_json(payload)


if __name__ == "__main__":
    main()