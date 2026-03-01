#!/usr/bin/env python
"""Deep single-reflection inspection diagnostic.

Shows everything about one reflection report: metadata, verdict, section lengths,
token usage, task context, assembled prompt preview, and automatic problem detection.

Usage:
    cd taskit/taskit-backend
    python testing_tools/reflection_inspect.py <report_id>
    python testing_tools/reflection_inspect.py <report_id> --brief
    python testing_tools/reflection_inspect.py <report_id> --json
    python testing_tools/reflection_inspect.py <report_id> --full
    python testing_tools/reflection_inspect.py <report_id> --sections basic,verdict,diagnosis
"""
import json
import sys

from _utils import (
    setup_django, parse_args, want_section,
    format_token_parts, extract_token_parts, format_duration,
    detect_repetition, print_json,
)

setup_django()

from tasks.models import ReflectionReport, Task, TaskComment  # noqa: E402

ALL_SECTIONS = {"basic", "verdict", "sections", "tokens", "prompt", "task_context", "diagnosis"}

REPORT_SECTIONS = ("quality_assessment", "slop_detection", "improvements", "agent_optimization")


def _parse_ctx_markers(prompt):
    """Extract [CTX:xxx] marker keys from assembled prompt."""
    import re
    return re.findall(r"\[CTX:(\w+)\]", prompt or "")


def diagnose_report(report, section_contents):
    """Auto-detect problems. Returns list of problem strings."""
    problems = []
    usage = report.token_usage or {}

    # Verdict/summary mismatch
    if report.verdict and report.verdict_summary:
        verdict_lower = report.verdict.lower()
        summary_lower = report.verdict_summary.lower()
        for v in ["pass", "needs_work", "fail"]:
            bold_v = f"**{v}**"
            if bold_v in summary_lower and v != verdict_lower.replace("_", " "):
                problems.append(
                    f"Verdict/summary mismatch: verdict is '{report.verdict}' but summary contains '{bold_v}'"
                )

    # Empty token_usage
    if not usage or not any(v for v in usage.values() if v):
        problems.append("Token usage is empty — cost estimation will show '-' in the UI")

    # Check if cost can be computed from tokens
    has_tokens = usage and any(v for v in usage.values() if v)
    if has_tokens:
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost(
            report.reviewer_model,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        if cost is None:
            problems.append(f"Token usage present but no pricing for model '{report.reviewer_model}' — cost will show '-'")

    # Repeated text
    for r in detect_repetition(report.verdict_summary):
        problems.append(f"Verdict summary repetition: {r}")
    for name, content in section_contents:
        for r in detect_repetition(content):
            problems.append(f"{name} repetition: {r}")

    # Empty assembled_prompt
    if not report.assembled_prompt:
        problems.append("Assembled prompt not captured")

    # CTX marker validation: context_selections vs markers in prompt
    if report.assembled_prompt and report.context_selections:
        markers = set(_parse_ctx_markers(report.assembled_prompt))
        selections = set(report.context_selections)
        missing = selections - markers
        for sel in sorted(missing):
            problems.append(f"Context selection '{sel}' requested but [CTX:{sel}] marker not found in prompt")

    # Empty context sections (marker present but placeholder content)
    if report.assembled_prompt:
        import re
        empty_patterns = [
            "No description provided.",
            "No execution output available.",
            "No comments.",
            "No dependencies.",
            "No metadata.",
        ]
        for pattern in empty_patterns:
            if pattern in report.assembled_prompt:
                problems.append(f"Context section contains placeholder: '{pattern}'")

    # Placeholder requested_by
    if report.requested_by == "unknown@user":
        problems.append("requested_by is 'unknown@user' — auth context was missing")

    # No verdict on completed report
    if report.status == "COMPLETED" and not report.verdict:
        problems.append("Report is COMPLETED but verdict is empty")

    # Empty sections on completed report
    for name, content in section_contents:
        if report.status == "COMPLETED" and not content:
            problems.append(f"{name} is empty on a COMPLETED report")

    return problems


def inspect_reflection(report_id, mode="standard", sections=None):
    """Inspect a single reflection report.

    Args:
        report_id: ReflectionReport PK.
        mode: 'brief' | 'standard' | 'full' | 'json'.
        sections: Set of section names, or None for all.
                  Options: basic, verdict, sections, tokens, prompt, task_context, diagnosis.
    """
    try:
        report = ReflectionReport.objects.select_related("task", "task__board").get(pk=report_id)
    except ReflectionReport.DoesNotExist:
        print(f"Reflection #{report_id} not found.")
        return

    section_contents = [
        (name, getattr(report, name, None))
        for name in REPORT_SECTIONS
    ]
    problems = diagnose_report(report, section_contents)
    usage = report.token_usage or {}
    total, inp, out = 0, 0, 0
    if isinstance(usage, dict):
        inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        total = usage.get("total_tokens") or (inp + out)

    # ── JSON mode ──
    if mode == "json":
        data = {
            "id": report.id,
            "task_id": report.task_id,
            "task_title": report.task.title,
            "status": report.status,
            "verdict": report.verdict,
            "reviewer_agent": report.reviewer_agent,
            "reviewer_model": report.reviewer_model,
            "tokens": {"total": total, "input": inp, "output": out},
            "duration_ms": report.duration_ms,
            "problem_count": len(problems),
            "problems": problems,
        }
        if want_section("verdict", sections):
            data["verdict_summary"] = report.verdict_summary
        if want_section("sections", sections):
            data["section_lengths"] = {
                name: len(content) if content else 0
                for name, content in section_contents
            }
        print_json(data)
        return

    # ── Brief mode ──
    if mode == "brief":
        dur = format_duration(report.duration_ms)
        tok = f"{total:,}" if total else "-"
        from tasks.pricing import estimate_task_cost
        est_cost = estimate_task_cost(report.reviewer_model, inp, out) if total else None
        cost = f"${est_cost:.4f}" if est_cost is not None else "-"
        prob = f"{len(problems)} problems" if problems else "ok"
        print(f"Reflection #{report.id}: {report.status} | verdict={report.verdict or '-'} | {tok} tokens | {cost} | {dur} | {prob}")
        if problems:
            for p in problems:
                print(f"  x {p}")
        return

    # ── Standard / Full mode ──
    print(f"\n{'=' * 70}")
    print(f"  REFLECTION INSPECT: #{report.id}")
    print(f"{'=' * 70}")

    if want_section("basic", sections):
        print(f"\n  BASIC INFO")
        print(f"  {'-' * 50}")
        print(f"  ID:            {report.id}")
        print(f"  Task:          #{report.task_id} — {report.task.title}")
        print(f"  Board:         {report.task.board.name} (#{report.task.board_id})")
        print(f"  Status:        {report.status}")
        print(f"  Verdict:       {report.verdict or '(empty)'}")
        print(f"  Agent:         {report.reviewer_agent}")
        print(f"  Model:         {report.reviewer_model}")
        print(f"  Requested by:  {report.requested_by or '(empty)'}")
        print(f"  Custom prompt: {'yes' if report.custom_prompt else 'no'}")
        print(f"  Context sels:  {report.context_selections or '(default)'}")
        print(f"  Created at:    {report.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Completed at:  {report.completed_at.strftime('%Y-%m-%d %H:%M:%S') if report.completed_at else '-'}")

    if want_section("verdict", sections):
        print(f"\n  VERDICT SUMMARY")
        print(f"  {'-' * 50}")
        if report.verdict_summary:
            max_lines = None if mode == "full" else 10
            lines = report.verdict_summary.split("\n")
            show = lines if max_lines is None else lines[:max_lines]
            for line in show:
                print(f"  {line}")
            if max_lines and len(lines) > max_lines:
                print(f"  ... ({len(report.verdict_summary)} chars total)")
        else:
            print("  (empty)")

    if want_section("sections", sections):
        print(f"\n  REPORT SECTIONS")
        print(f"  {'-' * 50}")
        for name, content in section_contents:
            if content:
                lines = content.strip().splitlines()
                print(f"  {name}: {len(content)} chars, {len(lines)} lines")
                # Full mode: show content
                if mode == "full":
                    for line in lines:
                        print(f"    {line}")
            else:
                print(f"  {name}: (empty)")

    if want_section("tokens", sections):
        print(f"\n  TOKEN USAGE & COST")
        print(f"  {'-' * 50}")
        if total:
            parts = [f"{total:,} total"]
            if inp:
                parts.append(f"{inp:,} in")
            if out:
                parts.append(f"{out:,} out")
            print(f"  tokens:   {' / '.join(parts)}")
        else:
            print(f"  tokens:   (empty — token_usage is {json.dumps(usage)})")
        if report.duration_ms:
            print(f"  duration: {report.duration_ms / 1000:.1f}s")
        else:
            print(f"  duration: (not recorded)")
        from tasks.pricing import estimate_task_cost
        est_cost = estimate_task_cost(report.reviewer_model, inp, out) if total else None
        if est_cost is not None:
            print(f"  est cost: ${est_cost:.4f}")
        elif total:
            print(f"  est cost: (no pricing for model '{report.reviewer_model}')")

    if want_section("prompt", sections):
        print(f"\n  ASSEMBLED PROMPT")
        print(f"  {'-' * 50}")
        if report.assembled_prompt:
            print(f"  {len(report.assembled_prompt)} chars")
            markers = _parse_ctx_markers(report.assembled_prompt)
            if markers:
                print(f"  CTX markers: {', '.join(markers)}")
            else:
                print(f"  CTX markers: (none — legacy prompt format)")
            if mode == "full":
                for line in report.assembled_prompt.split("\n"):
                    print(f"    {line}")
            else:
                preview = report.assembled_prompt[:200].replace("\n", "\\n")
                print(f"  preview: {preview}...")
        else:
            print("  (empty — prompt was not captured)")

    if want_section("task_context", sections):
        print(f"\n  TASK CONTEXT")
        print(f"  {'-' * 50}")
        task = report.task
        print(f"  Task status:   {task.status}")
        task_meta = task.metadata or {}
        if task_meta.get("last_usage"):
            u = task_meta["last_usage"]
            print(f"  Task tokens:   {u.get('total_tokens', '?'):,}")
        else:
            print(f"  Task tokens:   (not captured)")
        if task_meta.get("last_duration_ms"):
            print(f"  Task duration: {task_meta['last_duration_ms'] / 1000:.1f}s")
        comments = TaskComment.objects.filter(task_id=task.id).count()
        print(f"  Task comments: {comments}")

    # Error message (always shown if present)
    if report.error_message:
        print(f"\n  ERROR MESSAGE")
        print(f"  {'-' * 50}")
        max_lines = None if mode == "full" else 5
        lines = report.error_message.split("\n")
        show = lines if max_lines is None else lines[:max_lines]
        for line in show:
            print(f"  {line}")
        if max_lines and len(lines) > max_lines:
            print(f"  ... ({len(lines) - max_lines} more lines)")

    if want_section("diagnosis", sections):
        print(f"\n  DIAGNOSIS")
        print(f"  {'-' * 50}")
        if not problems:
            print("  ok — no problems detected")
        else:
            for p in problems:
                print(f"  x {p}")

    print()


def main():
    positional, mode, sections = parse_args(sys.argv, positional_name="report_id")
    if not positional:
        print("Usage: python testing_tools/reflection_inspect.py <report_id> [--brief|--full|--json] [--sections a,b,c]")
        print(f"\nSections: {', '.join(sorted(ALL_SECTIONS))}")
        sys.exit(1)

    inspect_reflection(positional, mode=mode, sections=sections)


if __name__ == "__main__":
    main()
