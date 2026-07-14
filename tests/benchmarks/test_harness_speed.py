"""Unit tests for the speed probe (no subprocess, no API)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))

from harness_speed import (  # noqa: E402
    CSV_FIELDS,
    HARNESSES,
    Row,
    append_csv,
    build_command,
    estimate_cost,
    extract_text,
    load_harness_defaults,
    load_pricing,
    parse_tokens,
)


def test_load_harness_defaults_has_all_six():
    defaults = load_harness_defaults()
    assert set(defaults.keys()) == set(HARNESSES.keys())
    for info in defaults.values():
        assert info["cli"]
        assert info["model"]


@pytest.mark.parametrize("harness", list(HARNESSES.keys()))
def test_build_command_includes_model_and_cli(harness):
    cmd = build_command(harness, "fake-cli", "fake-model-xyz")
    assert isinstance(cmd, list) and len(cmd) >= 2
    assert cmd[0] == "fake-cli"
    assert "fake-model-xyz" in " ".join(cmd)


def test_append_csv_writes_header_then_preserves_rows(tmp_path):
    csv_path = tmp_path / "speed_log.csv"
    row_a = Row("r1", "2026-04-24T00:00:00+00:00", "claude", "m1", 1200, 0, 300, 12)
    row_b = Row("r1", "2026-04-24T00:00:01+00:00", "codex", "m2", 2100, 0, 500, 15)
    row_c = Row("r2", "2026-04-24T01:00:00+00:00", "gemini", "m3", 800, 0, 250, 10)

    append_csv([row_a, row_b], csv_path)
    append_csv([row_c], csv_path)

    text = csv_path.read_text()
    header, *data_lines = [ln for ln in text.strip().splitlines() if ln]
    assert header == ",".join(CSV_FIELDS)
    assert len(data_lines) == 3
    assert "r1" in text and "r2" in text
    assert text.count("run_id") == 1


def test_parse_tokens_claude_model_usage():
    stream = (
        '{"type":"content_block_delta","delta":{"text":"hi"}}\n'
        '{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":42,"outputTokens":17}}}\n'
    )
    assert parse_tokens(stream) == {"input_tokens": 42, "output_tokens": 17}


def test_parse_tokens_codex_turn_completed():
    stream = (
        '{"type":"thread.started","thread_id":"x"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"HELLO"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":11144,"cached_input_tokens":6528,"output_tokens":25}}\n'
    )
    assert parse_tokens(stream) == {"input_tokens": 11144, "output_tokens": 25}


def test_parse_tokens_gemini_result_stats():
    stream = (
        '{"type":"init"}\n'
        '{"type":"message","role":"assistant","content":"hi","delta":true}\n'
        '{"type":"result","stats":{"input_tokens":9997,"output_tokens":1,"total_tokens":10026}}\n'
    )
    assert parse_tokens(stream) == {"input_tokens": 9997, "output_tokens": 1}


def test_parse_tokens_result_usage():
    stream = '{"type":"result","usage":{"input_tokens":1234,"output_tokens":567,"cache_read_input_tokens":0}}'
    assert parse_tokens(stream) == {"input_tokens": 1234, "output_tokens": 567}


def test_parse_tokens_opencode_step_finish():
    stream = (
        '{"type":"text","content":"hi"}\n'
        '{"type":"step_finish","part":{"tokens":{"input":30,"output":12}}}\n'
    )
    assert parse_tokens(stream) == {"input_tokens": 30, "output_tokens": 12}


def test_parse_tokens_empty_on_garbage():
    assert parse_tokens("hello world\nno json here") == {}


def test_extract_text_opencode_part_text():
    """Opencode/kilo emit poem text inside {type:text, part:{text:"..."}}."""
    stream = (
        '{"type":"step_start","timestamp":1,"part":{"id":"s1","type":"step-start"}}\n'
        '{"type":"text","part":{"text":"Line one\\nLine two\\nLine three"}}\n'
        '{"type":"step_finish","part":{"reason":"stop","tokens":{"input":5,"output":3}}}\n'
    )
    assert extract_text(stream) == "Line one\nLine two\nLine three"


def test_extract_text_gemini_message_role_assistant():
    stream = (
        '{"type":"init","model":"x"}\n'
        '{"type":"message","role":"assistant","content":"hello world","delta":true}\n'
        '{"type":"result","stats":{"input_tokens":5,"output_tokens":2}}\n'
    )
    assert extract_text(stream) == "hello world"


def test_extract_text_codex_item_completed():
    stream = (
        '{"type":"thread.started"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"HELLO"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":1}}\n'
    )
    assert extract_text(stream) == "HELLO"


def test_extract_text_passes_plain_through():
    assert extract_text("just plain text\nno JSON") == "just plain text\nno JSON"


def test_extract_text_empty_for_empty():
    assert extract_text("") == ""
    assert extract_text("   \n  ") == ""


def test_parse_tokens_records_explicit_zero():
    """Auth-failure responses report input_tokens:0 — zero is legitimate data, not N/A."""
    stream = '{"type":"result","usage":{"input_tokens":0,"output_tokens":0}}'
    assert parse_tokens(stream) == {"input_tokens": 0, "output_tokens": 0}


def test_load_pricing_returns_numbers_for_known_model():
    p_in, p_out = load_pricing("claude", "claude-sonnet-4-5")
    assert p_in and p_out and p_in > 0 and p_out > 0


def test_load_pricing_returns_none_for_unknown_model():
    assert load_pricing("claude", "no-such-model") == (None, None)


def test_estimate_cost_math():
    assert estimate_cost(1_000_000, 500_000, 3.0, 15.0) == pytest.approx(3.0 + 7.5)
    assert estimate_cost(None, 500_000, 3.0, 15.0) is None
    assert estimate_cost(1000, 500, None, 15.0) is None


def test_append_csv_migrates_old_schema(tmp_path):
    csv_path = tmp_path / "speed_log.csv"
    csv_path.write_text(
        "run_id,timestamp,harness,model,wall_ms,exit_code,stdout_chars,stdout_lines,error\n"
        "r0,2026-04-24T00:00:00+00:00,gemini,coder-model,4000,0,1400,3,\n"
    )
    new_row = Row(
        "r1", "2026-04-24T01:00:00+00:00", "claude", "m1", 1200, 0, 300, 12,
        input_tokens=50, output_tokens=80, cost_usd=0.00125,
    )
    append_csv([new_row], csv_path)

    lines = [ln for ln in csv_path.read_text().strip().splitlines() if ln]
    assert lines[0] == ",".join(CSV_FIELDS)
    assert len(lines) == 3
    assert lines[1].startswith("r0,") and ",,,," in lines[1]
    assert "r1" in lines[2] and "0.00125" in lines[2]
