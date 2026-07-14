#!/usr/bin/env python
"""Token-usage profiler — classify tool events into find / do / check / other.

The orientation bucket (#220+) is judged on the *finding-share* of tokens:
how much of an agent's run is spent reading/searching versus writing/editing
versus running tests. This script classifies every tool event in a task's
harness JSONL trace into one of four phases so we can compute that number
honestly and decide whether the next orientation investment (warm starts,
breadcrumbs, etc.) actually pays off.

Input: the harness JSONL the orchestrator already writes. Sources, in
priority order:

  1. A positional path to a JSONL file (``python testing_tools/token_profile.py path/to.trace.jsonl``)
  2. A positional task id — pulls the trace from the latest ``trace:execution_jsonl``
     TaskComment attached to that task.
  3. ``--spec <odin_id>`` — aggregates across every task in the spec that has a trace.

Mapping table (kept dumb + inspectable on purpose; one short rule per cell):

  =============  =====  ============================================================
  Tool           Phase  Rule
  =============  =====  ============================================================
  read           find   always
  glob           find   always
  grep           find   always
  webfetch       find   always (research / outside-the-repo lookups)
  write          do     always
  edit           do     always
  patch          do     always
  todowrite      other  always (control-plane; not user-visible cost)
  taskkill       other  always
  bash           check  command starts with a test-runner verb
                          (pytest, manage.py test, npm test, npm run test,
                           cargo test, go test, manage.py shell -c with assert)
  bash           find   command starts with a read verb
                          (ls, cat, head, tail, wc, stat, tree, file, pwd,
                           grep, rg, find, fd)
  bash           do     command starts with a write/install verb
                          (cp, mv, mkdir, rm, touch, chmod, ln, rmdir,
                           git commit, git merge, git push,
                           pip install, npm install, npm i, apt, brew,
                           python -m pip, sed -i)
  bash           check  otherwise (default for bash; running a command
                          *is* the agent's check)
  task (subagent) find   prompt contains an exploration verb
                          (find, search, locate, explore, identify,
                           "what files", "where", "trace this")
  task (subagent) do     prompt contains a write verb
                          (edit, write, create, implement, refactor,
                           "make the change", "fix the bug")
  task (subagent) check  prompt contains a test/verify verb
                          (test, verify, run the suite, "make sure")
  task (subagent) other  otherwise
  anything else  other  unknown tools land in other; never silently
                          promoted into find/do/check
  =============  =====  ============================================================

Two on-disk JSON shapes are handled transparently:

  1. ``{"type":"tool","tool":"<name>","state":{...}}``  — claude-code /
     Anthropic SDK style. Tool name is the top-level ``tool`` key.
  2. ``{"type":"tool_use","part":{"type":"tool","tool":"<name>"},
        "state":{...}}``  — opencode / MiniMax step style. Tool name lives
     under ``part.tool``.

Both shapes carry ``state.input`` + ``state.output`` so the rest of the
pipeline doesn't care which wrote the trace. ``step_finish`` and
``step_start`` events carry no per-event token counts in this harness, so
they are dropped at parse time; the script reports that fact in the
"attribution" note.

Token attribution:

  Per-event token counts are NOT emitted in the opencode harness JSONL the
  way the older claude-code format emits ``step_finish`` deltas. We
  approximate per-event cost as the byte-length of the event's ``input``
  plus ``output`` strings, then divide by the total bytes seen across the
  trace. The aggregate total comes from the authoritative on-the-fly
  source via ``compute_usage_from_trace`` when profiling a task id (the
  same number the API's ``usage`` field reports); when profiling a raw
  JSONL file path with no task id, we still print the per-phase share but
  no absolute totals.

  Honesty over precision: the script prints an ``"attribution": "approx"``
  flag in JSON mode and a one-line caveat in standard mode so the reader
  can see we're not measuring tokens directly.

Output modes (mimic the rest of ``testing_tools/``):

  standard (default) — phase shares, top-5 expensive events, total when known
  brief              — one line: phase counts + finding share %
  json               — same data as ``standard`` plus raw per-event list

Usage:
    cd taskit/taskit-backend
    python testing_tools/token_profile.py path/to/task.trace.jsonl
    python testing_tools/token_profile.py <task_id>
    python testing_tools/token_profile.py --spec <odin_id>
    python testing_tools/token_profile.py <id> --brief
    python testing_tools/token_profile.py <id> --json
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _utils import setup_django, parse_args, print_json  # noqa: E402

setup_django()

from tasks.execution_processing import compute_usage_from_trace  # noqa: E402
from tasks.models import Spec, Task, TaskComment  # noqa: E402

ALL_SECTIONS = {"header", "phases", "top", "events"}

PHASES = ("find", "do", "check", "other")

PHASE_LABELS = {
    "find": "find",
    "do": "do",
    "check": "check",
    "other": "other",
}

PHASE_ORDER = ("find", "do", "check", "other")

# ── Mapping rules (single source of truth; also printed in the docstring) ──

FIND_TOOLS = {"read", "glob", "grep", "webfetch", "list"}
DO_TOOLS = {"write", "edit", "patch"}
OTHER_TOOLS = {"todowrite", "taskkill", "tasklist", "step_start", "step_finish"}

_TEST_VERBS = (
    "pytest", "manage.py test", "manage.py shell -c",
    "npm test", "npm run test", "npm run lint",
    "cargo test", "go test",
)
_FIND_VERBS = (
    "ls", "cat", "head", "tail", "wc", "stat", "tree", "file",
    "pwd", "grep", "rg ", "find ", "fd ",
)
_DO_VERBS = (
    "cp ", "mv ", "mkdir ", "rm ", "touch ", "chmod ", "ln ", "rmdir ",
    "git commit", "git merge", "git push", "git rm", "git mv",
    "pip install", "python -m pip",
    "npm install", "npm i ",
    "apt ", "brew ", "sed -i ",
)

_TASK_FIND = ("find ", "search ", "locate ", "explore ", "identify ",
              "what files", "where ", "trace this", "understand ")
_TASK_DO = ("edit ", "write ", "create ", "implement ", "refactor ",
            "make the change", "fix the bug", "add the file",
            "create the file", "write the script")
_TASK_CHECK = ("test ", "verify ", "run the suite", "make sure",
               "run the test", "check the build")


def classify(tool: str, args: dict) -> str:
    """Bucket a single tool event into find / do / check / other.

    Args:
        tool: harness tool name (``bash``, ``read``, ``grep``, ``task``...).
        args: the event's ``state.input`` dict. Empty when no input.

    Returns:
        One of PHASES. New tools land in ``other`` (never silently
        promoted to find/do/check).
    """
    if tool in FIND_TOOLS:
        return "find"
    if tool in DO_TOOLS:
        return "do"
    if tool in OTHER_TOOLS:
        return "other"

    if tool == "bash":
        cmd = (args.get("command") or args.get("cmd") or "").strip()
        low = cmd.lower()
        if not low:
            return "other"
        for verb in _TEST_VERBS:
            if low.startswith(verb):
                return "check"
        for verb in _FIND_VERBS:
            if low.startswith(verb) or low == verb.strip():
                return "find"
        for verb in _DO_VERBS:
            if low.startswith(verb):
                return "do"
        return "check"

    if tool == "task":
        prompt = (args.get("prompt") or args.get("description")
                  or args.get("input") or "").lower()
        if not prompt:
            return "other"
        for kw in _TASK_FIND:
            if kw in prompt:
                return "find"
        for kw in _TASK_DO:
            if kw in prompt:
                return "do"
        for kw in _TASK_CHECK:
            if kw in prompt:
                return "check"
        return "other"

    return "other"


# ── Event iteration ──────────────────────────────────────────────────────


def _event_bytes(event: dict) -> int:
    """Approximate per-event 'cost' as bytes in input + output strings."""
    state = event.get("state") or {}
    payload = state.get("input") or {}
    if not isinstance(payload, dict):
        payload = {"_": payload}
    out = state.get("output") or ""
    size = len(json.dumps(payload, default=str)) + len(str(out))
    return max(size, 1)


def _iter_json_objects(blob: str):
    """Streaming parse: each line may itself span multiple lines (the
    ``output`` field of a tool event holds full file contents with literal
    newlines), so we accumulate characters from the next leading ``{`` until
    the brace count returns to zero, then json.loads the buffer.

    Yields dicts. Skips things that aren't valid JSON, which includes the
    run-log lines (`timestamp=... level=INFO ...`) the orchestrator
    interleaves with tool events.
    """
    depth = 0
    in_str = False
    escape = False
    start = -1
    for i, ch in enumerate(blob):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = blob[start : i + 1]
                try:
                    yield json.loads(chunk)
                except json.JSONDecodeError:
                    pass
                start = -1
            continue
        if depth == 0 and ch not in " \t\r\n":
            # Non-JSON text noise between objects (run-log lines).
            pass


def _extract_tool(event: dict):
    """Pull (tool, state, args) out of either JSON shape we accept.

    Shape 1: ``{"type":"tool","tool":"<name>","state":{...}}``
    Shape 2: ``{"type":"tool_use","part":{"type":"tool","tool":"<name>",
                                        "state":{...}}}``

    In shape 2 the inner ``part.state`` carries the real input + output;
    the outer ``state`` is absent. Returns (tool, state_dict, args) — any
    of which may be None if the event isn't a usable tool call.
    """
    etype = event.get("type")
    tool = None
    inner_state = None

    if etype == "tool":
        tool = event.get("tool")
        inner_state = event.get("state") or {}
    elif etype == "tool_use":
        part = event.get("part") or {}
        if isinstance(part, dict):
            tool = part.get("tool")
            inner_state = part.get("state") or {}
    else:
        return None, None, None

    if not tool:
        return None, inner_state, None
    status = inner_state.get("status")
    if status and status != "completed":
        return tool, inner_state, None
    args = inner_state.get("input") or {}
    return tool, inner_state, args


def iter_events(trace_path: str):
    """Yield event dicts for each completed tool event in the trace file.

    The trace file is the opencode harness stream-json output, each tool
    event is one JSON object. We scan the whole file as a single string and
    extract top-level JSON objects because the ``output`` field carries full
    file contents (so a single event can span multiple physical lines).
    """
    p = Path(trace_path)
    try:
        blob = p.read_text(errors="replace")
    except FileNotFoundError:
        print(f"trace file not found: {trace_path}")
        sys.exit(1)

    objects = list(_iter_json_objects(blob))
    out = []
    for i, ev in enumerate(objects):
        tool, state, args = _extract_tool(ev)
        if not tool:
            continue
        if args is None:
            continue
        phase = classify(tool, args)
        out.append({
            "i": i,
            "tool": tool,
            "phase": phase,
            "bytes": _event_bytes({"state": state}),
            "input": args,
            "output_preview": str((state or {}).get("output", ""))[:120],
        })
    return out


# ── Profile assembly ────────────────────────────────────────────────────


def profile(events):
    """Aggregate per-event list into the bucket the orientation check uses."""
    total = sum(e["bytes"] for e in events) or 1

    by_phase = Counter()
    bytes_by_phase = defaultdict(int)
    tool_count = Counter()
    tool_bytes = defaultdict(int)

    for e in events:
        by_phase[e["phase"]] += 1
        bytes_by_phase[e["phase"]] += e["bytes"]
        tool_count[e["tool"]] += 1
        tool_bytes[e["tool"]] += e["bytes"]

    phase_shares = {
        p: round(bytes_by_phase[p] / total * 100, 1) for p in PHASES
    }
    phase_event_shares = {
        p: round(by_phase[p] / max(sum(by_phase.values()), 1) * 100, 1)
        for p in PHASES
    }

    sorted_events = sorted(events, key=lambda e: e["bytes"], reverse=True)
    top = [
        {
            "i": e["i"],
            "tool": e["tool"],
            "phase": e["phase"],
            "bytes": e["bytes"],
            "input_preview": _input_preview(e["input"]),
            "output_preview": e["output_preview"],
        }
        for e in sorted_events[:5]
    ]

    return {
        "event_count": len(events),
        "tool_count": dict(tool_count),
        "phase_event_counts": dict(by_phase),
        "phase_event_shares_pct": phase_event_shares,
        "phase_bytes": dict(bytes_by_phase),
        "phase_shares_pct": phase_shares,
        "find_share_pct": phase_shares["find"],
        "top_events": top,
        "attribution": "approx_per_event_bytes",
    }


def _input_preview(input_data) -> str:
    """Compact one-line summary of a tool event's input."""
    if not isinstance(input_data, dict):
        return str(input_data)[:80]
    if "command" in input_data:
        cmd = str(input_data["command"]).replace("\n", " ")
        return cmd[:80]
    if "filePath" in input_data:
        return str(input_data["filePath"])
    if "pattern" in input_data:
        return f"pattern={input_data['pattern']!r}"
    if "prompt" in input_data:
        return f"prompt={str(input_data['prompt'])[:80]!r}"
    return str(input_data)[:80]


# ── Sources ─────────────────────────────────────────────────────────────


def load_trace_from_task(task_id: int) -> tuple:
    """Find the latest trace TaskComment for a task. Returns (task, raw_jsonl).

    SQLite (used in tests) doesn't support ``JSONField __contains`` lookup,
    so we filter task-side: every comment's ``attachments`` is checked in
    Python. ``compute_usage_from_trace`` in execution_processing uses the
    same pattern — see the note there.
    """
    task = Task.objects.get(pk=task_id)
    comments = list(
        TaskComment.objects.filter(task=task).order_by("-created_at")
    )
    raw = None
    for c in comments:
        if "trace:execution_jsonl" in (c.attachments or []):
            raw = c.content
            break
    if raw is None:
        print(f"task #{task_id} has no trace:execution_jsonl comment")
        sys.exit(1)
    return task, raw


def profile_task(task_id: int):
    """End-to-end: task id -> usage (authoritative) + per-phase profile."""
    task, raw = load_trace_from_task(task_id)

    tmp = Path("/tmp/opencode_token_profile.trace.jsonl")
    tmp.write_text(raw)
    events = iter_events(str(tmp))
    summary = profile(events)
    usage = compute_usage_from_trace(task)
    summary["task_id"] = task_id
    summary["title"] = task.title
    summary["status"] = task.status
    summary["attribution"] = "approx_per_event_bytes"
    if usage:
        summary["authoritative_total_tokens"] = (
            usage.get("total_tokens")
            or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        )
        summary["authoritative_input_tokens"] = usage.get("input_tokens", 0)
        summary["authoritative_output_tokens"] = usage.get("output_tokens", 0)
    return summary


def profile_spec(odin_id: str):
    """Aggregate per-phase shares across every trace-bearing task in a spec."""
    spec = Spec.objects.get(odin_id=odin_id)
    tasks = (
        Task.objects
        .filter(spec=spec)
        .order_by("id")
    )
    per_task = []
    agg_bytes = defaultdict(int)
    agg_count = Counter()
    total_events = 0
    find_share_sum = 0.0
    find_share_n = 0
    for t in tasks:
        comments = list(
            TaskComment.objects.filter(task=t).order_by("-created_at")
        )
        raw = None
        for c in comments:
            if "trace:execution_jsonl" in (c.attachments or []):
                raw = c.content
                break
        if raw is None:
            continue
        tmp = Path(f"/tmp/opencode_token_profile_spec_{odin_id}_{t.id}.trace.jsonl")
        tmp.write_text(raw)
        events = iter_events(str(tmp))
        if not events:
            continue
        sub = profile(events)
        sub["task_id"] = t.id
        sub["title"] = t.title
        sub["status"] = t.status
        sub.pop("top_events", None)
        per_task.append(sub)
        for e in events:
            agg_bytes[e["phase"]] += e["bytes"]
            agg_count[e["phase"]] += 1
        total_events += len(events)
        if sub.get("find_share_pct") is not None:
            find_share_sum += sub["find_share_pct"]
            find_share_n += 1

    total_bytes = sum(agg_bytes.values()) or 1
    phase_shares = {p: round(agg_bytes[p] / total_bytes * 100, 1) for p in PHASES}
    return {
        "spec_id": spec.odin_id,
        "spec_title": spec.title,
        "task_count": len(per_task),
        "total_events": total_events,
        "phase_bytes": dict(agg_bytes),
        "phase_event_counts": dict(agg_count),
        "phase_shares_pct": phase_shares,
        "find_share_pct_avg": (
            round(find_share_sum / find_share_n, 1) if find_share_n else None
        ),
        "finding_share_pct": phase_shares["find"],
        "per_task": per_task,
        "attribution": "approx_per_event_bytes",
    }


def profile_file(path: str):
    """Raw file path → profile (no absolute tokens; phase shares only)."""
    events = iter_events(path)
    return profile(events)


# ── Output ──────────────────────────────────────────────────────────────


def _print_standard(out: dict, title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  TOKEN PROFILE — {title}")
    print(f"{'=' * 70}")
    if out.get("event_count", 0) == 0:
        print("  (no tool events in trace)")
        return
    print(f"  events:        {out['event_count']} total across "
          f"{len(out.get('tool_count', {}))} tools")
    if "authoritative_total_tokens" in out:
        print(f"  total tokens:  {out['authoritative_total_tokens']:,} "
              f"({out['authoritative_input_tokens']:,} in / "
              f"{out['authoritative_output_tokens']:,} out)")
    elif out.get("task_count") is not None:
        # spec profile has per-task entries
        print(f"  task_count:    {out['task_count']} profiled, "
              f"{out.get('total_events', 0)} events aggregated")

    print(f"  attribution:   {out.get('attribution')}")
    print()
    print(f"  {'phase':<8} {'events':>7} {'%':>6} {'approx tokens %':>17}")
    for p in PHASE_ORDER:
        cnt = out.get("phase_event_counts", {}).get(p, 0)
        ev_pct = out.get("phase_event_shares_pct", {}).get(p, 0)
        byte_pct = out.get("phase_shares_pct", {}).get(p, 0)
        print(f"  {p:<8} {cnt:>7} {ev_pct:>5.1f}% {byte_pct:>16.1f}%")
    print()
    print(f"  finding share: {out.get('find_share_pct', out.get('finding_share_pct', 0)):.1f}%  "
          "(orientation bucket judges investment on this number)")
    print()

    top = out.get("top_events")
    if top:
        print(f"  {'rank':<5} {'tool':<8} {'phase':<6} {'bytes':>7} preview")
        for j, e in enumerate(top, 1):
            preview = e.get("input_preview") or e.get("output_preview", "")[:80]
            print(f"  #{j:<4} {e['tool']:<8} {e['phase']:<6} {e['bytes']:>7} {preview[:60]}")
        print()


def _print_brief(out: dict, title: str) -> None:
    if out.get("event_count", 0) == 0:
        print(f"token_profile [{title}]: 0 events")
        return
    find_share = out.get("find_share_pct", out.get("finding_share_pct"))
    counts = out.get("phase_event_counts", {})
    parts = " / ".join(
        f"{p}={counts.get(p, 0)}" for p in PHASE_ORDER
    )
    token_total = ""
    if "authoritative_total_tokens" in out:
        token_total = f" · {out['authoritative_total_tokens']:,} tokens"
    share_str = f"{find_share:.1f}%" if find_share is not None else "—"
    print(
        f"token_profile [{title}]: {out['event_count']} events · "
        f"finding_share={share_str}{token_total} · {parts}"
    )


def main():
    argv = sys.argv
    spec_id = None
    raw_argv = list(argv)
    if "--spec" in raw_argv:
        i = raw_argv.index("--spec")
        if i + 1 < len(raw_argv):
            spec_id = raw_argv[i + 1]
            raw_argv.pop(i)
            raw_argv.pop(i)
        else:
            print("--spec requires an odin_id value")
            sys.exit(1)
    argv = raw_argv

    positional, mode, sections = parse_args(argv, positional_name="task_id_or_path")

    if spec_id:
        out = profile_spec(spec_id)
        title = f"spec {spec_id}"
    elif positional and Path(positional).is_file():
        out = profile_file(positional)
        title = Path(positional).name
    elif positional and positional.isdigit():
        out = profile_task(int(positional))
        title = f"task #{positional}"
    else:
        print(__doc__)
        sys.exit(1)

    if mode == "json":
        print_json(out)
        return
    if mode == "brief":
        _print_brief(out, title)
        return
    _print_standard(out, title)


if __name__ == "__main__":
    main()
