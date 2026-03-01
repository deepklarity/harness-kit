"""Shared utilities for testing_tools diagnostic scripts.

Provides Django bootstrap, common formatters, output mode handling,
and argument parsing. All diagnostic scripts import from here.
"""
import json
import os
import sys
from datetime import datetime

# ── Django bootstrap ──────────────────────────────────────────────
# Call setup_django() once at script entry. After that, Django models are importable.

_django_ready = False


def setup_django():
    """Initialize Django settings and ORM. Idempotent."""
    global _django_ready
    if _django_ready:
        return
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    _django_ready = True


# ── Output modes ──────────────────────────────────────────────────
# brief:    Minimal — status + key metrics + problem count (3-8 lines)
# standard: Current default — all sections, truncated content
# full:     Everything — full comments, full descriptions, full metadata
# json:     Structured JSON — token-efficient for LLM consumption

MODES = ("brief", "standard", "full", "json")


def parse_args(argv, positional_name="id"):
    """Parse script arguments: <positional> [--mode MODE] [--sections a,b,c]

    Returns (positional_value, mode, sections_set_or_None).
    """
    mode = "standard"
    sections = None
    positional = None
    skip_next = False

    args = argv[1:]  # skip script name
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            if mode not in MODES:
                print(f"Unknown mode '{mode}'. Options: {', '.join(MODES)}")
                sys.exit(1)
            skip_next = True
        elif arg == "--sections" and i + 1 < len(args):
            sections = set(args[i + 1].split(","))
            skip_next = True
        elif arg == "--brief":
            mode = "brief"
        elif arg == "--full":
            mode = "full"
        elif arg == "--json":
            mode = "json"
        elif arg == "--slim":
            # snapshot_extractor specific — passed through
            pass
        elif not arg.startswith("--") and positional is None:
            positional = arg

    return positional, mode, sections


def want_section(name, sections):
    """Check whether a section should be included. None means all sections."""
    return sections is None or name in sections


# ── Formatters ────────────────────────────────────────────────────

def format_duration(ms):
    """Format milliseconds to human-readable duration."""
    if not ms:
        return "-"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"


def format_tokens(metadata):
    """Extract formatted token string from task metadata."""
    usage = metadata.get("last_usage", {}) if metadata else {}
    if isinstance(usage, dict):
        total = usage.get("total_tokens") or (
            (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)
        )
        if total:
            return f"{total:,}"
    return "-"


def extract_token_parts(metadata):
    """Extract (total, input, output) token counts from metadata. Returns ints or 0."""
    usage = metadata.get("last_usage", {}) if metadata else {}
    if not isinstance(usage, dict):
        return 0, 0, 0
    inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    total = usage.get("total_tokens") or (inp + out)
    return total, inp, out


def format_token_parts(metadata):
    """Format token counts as 'TOTAL total / IN in / OUT out' string."""
    total, inp, out = extract_token_parts(metadata)
    if not total:
        return "(not captured)"
    parts = [f"{total:,} total"]
    if inp:
        parts.append(f"{inp:,} in")
    if out:
        parts.append(f"{out:,} out")
    return " / ".join(parts)


# ── JSON output ───────────────────────────────────────────────────

def serialize_datetime(obj):
    """JSON serializer for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def print_json(data):
    """Print data as compact JSON to stdout."""
    print(json.dumps(data, indent=2, default=serialize_datetime))


# ── Text analysis ─────────────────────────────────────────────────

def detect_repetition(text):
    """Detect repeated paragraphs in text (LLM stutter). Returns list of problem strings."""
    import re
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    seen = {}
    problems = []
    for p in paragraphs:
        normalized = " ".join(p.split())
        if normalized in seen:
            problems.append(f"Paragraph repeated (first {min(60, len(p))} chars): '{p[:60]}...'")
        else:
            seen[normalized] = True
    return problems
