"""Board story assembly — one source of truth shared by the
``GET /boards/<id>/story/`` endpoint (tasks/views.py) and the CLI
diagnostic (testing_tools/board_story.py).

Turns a board's specs and tasks into a chronological event stream —
landings, failures, merge conflicts, reflection escalations, wave
open/close — so a renderer or an operator can produce a two-minute
handover without archaeology.

Reuses the hands-free definition from autonomy_metrics (task #226) and
the failure-fingerprint distillation from mistakes, so the board story
never drifts from those single sources of truth.
"""
import re
from datetime import datetime

from .models import ReflectionReport, TaskHistory, TaskStatus


# Statuses that count as "landed" — mirrors autonomy_metrics.LANDED_STATUSES.
_LANDED = {TaskStatus.TESTING, TaskStatus.DONE}

# Reflection verdicts that count as escalations (incidents).
_ESCALATION_VERDICTS = {"NEEDS_WORK", "FAIL", "ERROR"}

# Merge statuses that represent an unresolved conflict incident.
_CONFLICT_STATUSES = {"conflict", "needs_human"}


def _parse_since(value):
    """Parse an ISO-8601 string into an aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        from django.utils import timezone
        dt = timezone.make_aware(dt)
    return dt


def _failure_fingerprint(task):
    """One-line failure reason + class, reusing mistakes.distill_execution."""
    from .mistakes import distill_execution
    one_liner, failure_class = distill_execution(task)
    return one_liner, failure_class


def _merge_resolution(metadata):
    """How a merge conflict was resolved, as a short phrase."""
    if metadata.get("merge_agent_resolved"):
        return "agent resolved"
    status = metadata.get("merge_status")
    if status == "needs_human":
        return "escalated to human"
    if status == "conflict":
        return "auto-resolution failed"
    if status == "merged":
        return "resolved"
    if status == "error":
        return "merge error"
    return "unresolved"


def build_board_story(board, since=None):
    """Assemble the chronological event stream for a board.

    Args:
        board: Board model instance.
        since: Optional ISO string or aware datetime. Events at or after
            this moment are included; earlier events are dropped.

    Returns a dict with ``board_id``, ``board_name``, ``since`` (the
    parsed cutoff or None), ``events`` (ascending by timestamp), and
    ``tldr`` (summary counts).
    """
    from .models import Spec, Task, TaskComment

    if isinstance(since, str):
        since = _parse_since(since)

    events = []

    specs = list(
        Spec.objects.filter(board=board).order_by("created_at")
    )

    tasks = list(
        Task.objects.filter(board=board)
        .select_related("spec", "assignee")
        .order_by("id")
    )
    task_ids = [t.id for t in tasks]

    # ── Spec-created events (wave open) ──
    for spec in specs:
        events.append({
            "timestamp": spec.created_at,
            "kind": "spec_created",
            "line": f"Spec created: {spec.title}",
            "task_id": None,
            "spec_id": spec.id,
        })

    # ── Status-transition events: task_landed, task_failed ──
    histories = list(
        TaskHistory.objects.filter(
            task_id__in=task_ids, field_name="status",
        ).order_by("changed_at")
    )

    # Pre-compute hands-free flags using the task-226 definition (same
    # logic as autonomy_metrics.is_operator_intervention, inlined here
    # so production code doesn't import from testing_tools).
    comments_by_task = {}
    for c in TaskComment.objects.filter(task_id__in=task_ids):
        comments_by_task.setdefault(c.task_id, []).append(c)
    histories_by_task = {}
    for h in histories:
        histories_by_task.setdefault(h.task_id, []).append(h)
    hands_free_by_task = {
        t.id: _is_hands_free(
            histories_by_task.get(t.id, []),
            comments_by_task.get(t.id, []),
        )
        for t in tasks
    }

    landed_seen = set()
    failed_tasks = set()
    for h in histories:
        new_val = (h.new_value or "").upper()
        if new_val in _LANDED and h.task_id not in landed_seen:
            landed_seen.add(h.task_id)
            task = next((t for t in tasks if t.id == h.task_id), None)
            if task is None:
                continue
            hands_free = hands_free_by_task.get(task.id, False)
            tag = "hands-free" if hands_free else "operator-assisted"
            events.append({
                "timestamp": h.changed_at,
                "kind": "task_landed",
                "line": f"Task landed ({tag}): {task.title}",
                "task_id": task.id,
                "spec_id": task.spec_id,
                "hands_free": hands_free,
            })
        elif new_val == TaskStatus.FAILED:
            task = next((t for t in tasks if t.id == h.task_id), None)
            if task is None:
                continue
            failed_tasks.add(task.id)
            fingerprint, failure_class = _failure_fingerprint(task)
            events.append({
                "timestamp": h.changed_at,
                "kind": "task_failed",
                "line": f"Task failed: {task.title} — {fingerprint}",
                "task_id": task.id,
                "spec_id": task.spec_id,
                "fingerprint": fingerprint,
                "failure_class": failure_class,
            })

    # ── Reflection escalation events ──
    reflections = list(
        ReflectionReport.objects.filter(
            task_id__in=task_ids,
            verdict__in=_ESCALATION_VERDICTS,
        ).order_by("completed_at")
    )
    for r in reflections:
        task = next((t for t in tasks if t.id == r.task_id), None)
        if task is None:
            continue
        when = r.completed_at or r.created_at
        detail = (r.verdict_summary or "").split("\n", 1)[0][:120] if r.verdict_summary else task.title
        events.append({
            "timestamp": when,
            "kind": "reflection_escalation",
            "line": f"Review {r.verdict}: {detail}",
            "task_id": task.id,
            "spec_id": task.spec_id,
            "verdict": r.verdict,
        })

    # ── Merge conflict events ──
    waiting_on_human = 0
    for task in tasks:
        metadata = task.metadata or {}
        ms = metadata.get("merge_status")
        if ms not in _CONFLICT_STATUSES:
            continue
        if ms == "needs_human":
            waiting_on_human += 1
        when = _merge_event_time(task, histories)
        resolution = _merge_resolution(metadata)
        events.append({
            "timestamp": when,
            "kind": "merge_conflict",
            "line": f"Merge conflict on {task.title} — {resolution}",
            "task_id": task.id,
            "spec_id": task.spec_id,
            "resolution": resolution,
            "conflicting_files": metadata.get("merge_ambiguous_files") or [],
        })

    # ── Wave all-landed events (wave close) ──
    for spec in specs:
        spec_tasks = [t for t in tasks if t.spec_id == spec.id]
        if not spec_tasks:
            continue
        if all(t.status in _LANDED for t in spec_tasks):
            landing_times = [
                h.changed_at for h in histories
                if h.task_id in {t.id for t in spec_tasks}
                and (h.new_value or "").upper() in _LANDED
            ]
            if landing_times:
                events.append({
                    "timestamp": max(landing_times),
                    "kind": "wave_all_landed",
                    "line": f"Wave complete — all {len(spec_tasks)} tasks landed: {spec.title}",
                    "task_id": None,
                    "spec_id": spec.id,
                })

    # ── Filter and sort ──
    if since is not None:
        events = [e for e in events if e["timestamp"] is not None and e["timestamp"] >= since]
    events.sort(key=lambda e: e["timestamp"])

    # ── TLDR ──
    landed_task_ids = {e["task_id"] for e in events if e["kind"] == "task_landed"}
    hands_free_task_ids = {
        e["task_id"] for e in events
        if e["kind"] == "task_landed" and e.get("hands_free")
    }
    incidents = sum(
        1 for e in events
        if e["kind"] in ("task_failed", "merge_conflict", "reflection_escalation")
    )

    return {
        "board_id": board.id,
        "board_name": board.name,
        "since": since.isoformat() if since else None,
        "event_count": len(events),
        "events": events,
        "tldr": {
            "landed": len(landed_task_ids),
            "hands_free": len(hands_free_task_ids),
            "incidents": incidents,
            "waiting_on_human": waiting_on_human,
        },
    }


def _merge_event_time(task, histories):
    """Best-effort timestamp for when a merge conflict surfaced."""
    for h in histories:
        if h.task_id == task.id and h.field_name == "status":
            new_val = (h.new_value or "").upper()
            if new_val == TaskStatus.REVIEW:
                return h.changed_at
    metadata = task.metadata or {}
    dispatched = metadata.get("merge_dispatched_at")
    if dispatched:
        try:
            return datetime.fromisoformat(dispatched)
        except (ValueError, TypeError):
            pass
    return task.last_updated_at


def _is_hands_free(histories, comments):
    """Task-226 hands-free definition: dispatch → landed with no operator
    intervention. Inlined from autonomy_metrics.is_operator_intervention
    so production code doesn't depend on testing_tools/.

    Operator intervention = ANY operator-authored comment (any type) OR
    ANY operator manual status PATCH that is NOT the first dispatch
    transition (TODO/whatever → EXECUTING/IN_PROGRESS). Returns False
    when no dispatch was ever observed either.
    """
    from .agent_stats import DISPATCH_STATUSES, is_operator_email

    if any(is_operator_email(c.author_email) for c in comments):
        return False

    initial_dispatch_seen = False
    for h in histories:
        if h.field_name != "status":
            continue
        new_val = (h.new_value or "").upper()
        if (not initial_dispatch_seen) and new_val in DISPATCH_STATUSES:
            initial_dispatch_seen = True
            continue
        if is_operator_email(h.changed_by):
            return False

    return initial_dispatch_seen


def render_html(story, bucket_rows=None, testing_shelf=None, decisions=None):
    """Render a self-contained HTML handover page from a board story.

    No external assets — inline CSS only, both light and dark themes via
    CSS tokens. Composes, top to bottom: TLDR block (landed, hands-free,
    incidents, waiting-on-human), the chronological event timeline, the
    bucket scoreboard (``bucket_rows``, e.g. from
    testing_tools.board_story.parse_bucket_scoreboard), the TESTING shelf
    (``testing_shelf``), and decisions waiting on the human
    (``decisions``, e.g. from parse_backlog_decisions). Each of the three
    optional sections renders an honest em-dash placeholder when empty —
    never omitted — so the page is always the same shape.
    """
    tldr = story["tldr"]
    events = story["events"]

    event_rows = []
    for e in events:
        ts = e["timestamp"].strftime("%H:%M") if e["timestamp"] else "??:??"
        kind = e["kind"]
        line = _escape(e["line"])
        task_ref = ""
        if e.get("task_id"):
            task_ref = f' <span class="ref">#{e["task_id"]}</span>'
        spec_ref = ""
        if e.get("spec_id"):
            spec_ref = f' <span class="ref">spec {e["spec_id"]}</span>'
        cls = _event_class(kind)
        event_rows.append(
            f'<tr class="{cls}"><td class="ts">{ts}</td>'
            f'<td class="kind">{kind}</td>'
            f'<td>{line}{task_ref}{spec_ref}</td></tr>'
        )

    rows_html = "\n".join(event_rows) if event_rows else '<tr><td colspan="3">No events in this window.</td></tr>'

    since_label = story.get("since") or "all time"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{_FAVICON}">
<title>Board #{story["board_id"]} — { _escape(story["board_name"])}</title>
<style>
  :root {{
    --bg: #f6f8fa; --panel: #ffffff; --border: #d0d7de; --ink: #1f2328; --sub: #57606a;
    --accent: #0969da; --ok: #1a7f37; --warn: #cf222e; --purple: #8250df; --orange: #bc4c00;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0d1117; --panel: #161b22; --border: #30363d; --ink: #c9d1d9; --sub: #8b949e;
      --accent: #58a6ff; --ok: #3fb950; --warn: #f85149; --purple: #d2a8ff; --orange: #f0883e;
    }}
  }}
  html[data-theme="light"] {{
    --bg: #f6f8fa; --panel: #ffffff; --border: #d0d7de; --ink: #1f2328; --sub: #57606a;
    --accent: #0969da; --ok: #1a7f37; --warn: #cf222e; --purple: #8250df; --orange: #bc4c00;
  }}
  html[data-theme="dark"] {{
    --bg: #0d1117; --panel: #161b22; --border: #30363d; --ink: #c9d1d9; --sub: #8b949e;
    --accent: #58a6ff; --ok: #3fb950; --warn: #f85149; --purple: #d2a8ff; --orange: #f0883e;
  }}
  html, body {{ background: var(--bg) !important; color: var(--ink) !important; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 24px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  h2.section {{ font-size: 1rem; color: var(--sub); margin: 28px 0 8px; text-transform: uppercase;
               letter-spacing: 1px; }}
  h2.section:first-of-type {{ margin-top: 0; }}
  .sub {{ color: var(--sub); font-size: 0.85rem; margin-bottom: 20px; }}
  .tldr {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .stat {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
           padding: 12px 16px; min-width: 120px; }}
  .stat .num {{ font-size: 1.8rem; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }}
  .stat .lbl {{ font-size: 0.75rem; color: var(--sub); text-transform: uppercase;
               letter-spacing: 0.5px; }}
  .stat.warn .num {{ color: var(--warn); }}
  .stat.ok .num {{ color: var(--ok); }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; font-size: 0.75rem; color: var(--sub); text-transform: uppercase;
       letter-spacing: 0.5px; padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
  td.ts, td.num {{ color: var(--sub); white-space: nowrap; font-variant-numeric: tabular-nums; }}
  td.kind {{ color: var(--purple); font-size: 0.8rem; white-space: nowrap; }}
  .ref {{ color: var(--sub); font-size: 0.8rem; font-variant-numeric: tabular-nums; }}
  tr.task_failed td.kind {{ color: var(--warn); }}
  tr.merge_conflict td.kind {{ color: var(--orange); }}
  tr.task_landed td.kind {{ color: var(--ok); }}
  tr.reflection_escalation td.kind {{ color: var(--orange); }}
  ol.decisions {{ padding-left: 20px; margin: 0; }}
  ol.decisions li {{ margin-bottom: 8px; font-size: 0.9rem; }}
  a {{ color: var(--accent); }}
  .footer {{ margin-top: 32px; color: var(--sub); font-size: 0.8rem; border-top: 1px solid var(--border);
            padding-top: 12px; }}
</style>
</head>
<body>
<h1>Board #{story["board_id"]}: {_escape(story["board_name"])} — shift handover</h1>
<div class="sub">Since {since_label}</div>
<h2 class="section">TLDR</h2>
<div class="tldr">
  <div class="stat ok"><div class="num">{tldr["landed"]}</div><div class="lbl">Landed</div></div>
  <div class="stat ok"><div class="num">{tldr["hands_free"]}</div><div class="lbl">Hands-free</div></div>
  <div class="stat warn"><div class="num">{tldr["incidents"]}</div><div class="lbl">Incidents</div></div>
  <div class="stat warn"><div class="num">{tldr["waiting_on_human"]}</div><div class="lbl">Waiting on human</div></div>
</div>
<h2 class="section">Timeline</h2>
<div class="table-wrap">
<table>
  <thead><tr><th>Time</th><th>Event</th><th>Detail</th></tr></thead>
  <tbody>
{rows_html}
  </tbody>
</table>
</div>
<h2 class="section">Bucket scoreboard</h2>
<div class="table-wrap">
<table>
  <thead><tr><th>Bucket</th><th>Score</th><th>One line</th></tr></thead>
  <tbody>
{_render_bucket_rows(bucket_rows)}
  </tbody>
</table>
</div>
<h2 class="section">TESTING shelf</h2>
<div class="table-wrap">
<table>
  <thead><tr><th>Task</th><th>Title</th></tr></thead>
  <tbody>
{_render_testing_shelf(testing_shelf)}
  </tbody>
</table>
</div>
<h2 class="section">Waiting on you</h2>
<ol class="decisions">
{_render_decisions(decisions)}
</ol>
<div class="footer">Living document — regenerate with testing_tools/board_story.py --html. Ground truth: the board.</div>
</body>
</html>"""


_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Ctext y='.9em' font-size='90'%3E%F0%9F%93%8B%3C/text%3E%3C/svg%3E"
)


def _render_bucket_rows(bucket_rows):
    if not bucket_rows:
        return '<tr><td colspan="3">—</td></tr>'
    rows = []
    for b in bucket_rows:
        name = _escape(b.get("name") or "—")
        link = b.get("link")
        name_html = f'<a href="{_escape(link)}">{name}</a>' if link else name
        score = _escape(b.get("score") or "—")
        one_line = _escape(b.get("one_line") or "—")
        rows.append(f'<tr><td>{name_html}</td><td class="num">{score}</td><td>{one_line}</td></tr>')
    return "\n".join(rows)


def _render_testing_shelf(testing_shelf):
    if not testing_shelf:
        return '<tr><td colspan="2">—</td></tr>'
    rows = []
    for t in testing_shelf:
        rows.append(f'<tr><td class="ref">#{t["id"]}</td><td>{_escape(t["title"])}</td></tr>')
    return "\n".join(rows)


def _render_decisions(decisions):
    if not decisions:
        return '<li>—</li>'
    return "\n".join(f'<li>{_bold_and_escape(d)}</li>' for d in decisions)


_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')


def _bold_and_escape(text):
    """Escape text, converting markdown ``**bold**`` spans to <strong>."""
    parts = _BOLD_RE.split(text or "")
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(f"<strong>{_escape(part)}</strong>")
        else:
            out.append(_escape(part))
    return "".join(out)


def _event_class(kind):
    return kind


def _escape(text):
    """Minimal HTML escaping for safe rendering."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
