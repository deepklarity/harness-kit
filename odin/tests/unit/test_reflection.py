"""Unit tests for odin.reflection — prompt builder and report parser.

Pure logic, no I/O, no mocks.
"""

import json
import pytest

from odin.reflection import build_reflection_prompt, parse_reflection_report, _strip_odin_envelopes
from odin.harnesses.claude import _extract_token_usage


class TestBuildReflectionPrompt:
    """build_reflection_prompt() generates structured audit prompts."""

    def _context(self, **overrides):
        base = {
            "title": "Implement user login",
            "status": "REVIEW",
            "agent": "claude",
            "model": "claude-sonnet-4-5",
            "duration_ms": 45000,
            "tokens": "12,000",
            "description": "Add JWT-based login endpoint",
            "execution_output": "Created /auth/login endpoint with JWT",
            "comments": "- Completed successfully",
            "dependencies": "- Task #1: Setup DB (DONE)",
        }
        base.update(overrides)
        return base

    def test_prompt_contains_readonly_instruction(self):
        prompt = build_reflection_prompt(self._context())
        assert "READ-ONLY" in prompt

    def test_prompt_includes_task_title_and_description(self):
        prompt = build_reflection_prompt(self._context())
        assert "Implement user login" in prompt
        assert "Add JWT-based login endpoint" in prompt

    def test_prompt_includes_execution_output(self):
        prompt = build_reflection_prompt(self._context())
        assert "Created /auth/login endpoint with JWT" in prompt

    def test_prompt_includes_dependent_tasks(self):
        prompt = build_reflection_prompt(self._context())
        assert "Setup DB (DONE)" in prompt

    def test_prompt_includes_custom_prompt_when_provided(self):
        prompt = build_reflection_prompt(
            self._context(), custom_prompt="Focus on error handling patterns"
        )
        assert "Focus on error handling patterns" in prompt

    def test_prompt_omits_custom_prompt_section_when_empty(self):
        prompt = build_reflection_prompt(self._context(), custom_prompt="")
        assert "ADDITIONAL FOCUS" not in prompt

    def test_prompt_includes_agent_and_model_info(self):
        prompt = build_reflection_prompt(self._context())
        assert "claude" in prompt
        assert "claude-sonnet-4-5" in prompt

    def test_prompt_includes_section_headers(self):
        prompt = build_reflection_prompt(self._context())
        assert "### Quality Assessment" in prompt
        assert "### Slop Detection" in prompt
        assert "### Actionable Improvements" in prompt
        assert "### Agent Optimization" in prompt
        assert "### Verdict" in prompt


class TestParseReflectionReport:
    """parse_reflection_report() splits agent output into structured sections."""

    FULL_REPORT = """Some preamble text.

### Quality Assessment
The code is well-structured and follows conventions.
Tests cover the happy path adequately.

### Slop Detection
Minor: unnecessary docstring on a self-evident method in auth.py:45.

### Actionable Improvements
1. **Critical**: Add input validation for email field
2. **Important**: Handle token expiry gracefully

### Agent Optimization
- Task description was clear enough
- Model tier was appropriate (sonnet for moderate complexity)
- Token usage was efficient

### Verdict
NEEDS_WORK
Solid implementation but missing critical input validation.
"""

    def test_parse_extracts_all_five_sections(self):
        result = parse_reflection_report(self.FULL_REPORT)
        assert result["quality_assessment"] != ""
        assert result["slop_detection"] != ""
        assert result["improvements"] != ""
        assert result["agent_optimization"] != ""
        assert result["verdict"] != ""

    def test_parse_extracts_verdict_pass(self):
        report = self.FULL_REPORT.replace("NEEDS_WORK", "PASS").replace(
            "Solid implementation but missing critical input validation.",
            "Everything looks good.",
        )
        result = parse_reflection_report(report)
        assert result["verdict"] == "PASS"

    def test_parse_extracts_verdict_needs_work(self):
        result = parse_reflection_report(self.FULL_REPORT)
        assert result["verdict"] == "NEEDS_WORK"

    def test_parse_extracts_verdict_fail(self):
        report = self.FULL_REPORT.replace("NEEDS_WORK", "FAIL").replace(
            "Solid implementation but missing critical input validation.",
            "Major issues found.",
        )
        result = parse_reflection_report(report)
        assert result["verdict"] == "FAIL"

    def test_parse_extracts_verdict_summary(self):
        result = parse_reflection_report(self.FULL_REPORT)
        assert "missing critical input validation" in result["verdict_summary"]

    def test_parse_handles_missing_sections_gracefully(self):
        partial = """### Quality Assessment
Looks fine.

### Verdict
PASS
All good.
"""
        result = parse_reflection_report(partial)
        assert result["quality_assessment"] != ""
        assert result["slop_detection"] == ""
        assert result["improvements"] == ""
        assert result["agent_optimization"] == ""
        assert result["verdict"] == "PASS"

    def test_parse_handles_empty_output(self):
        result = parse_reflection_report("")
        assert result["quality_assessment"] == ""
        assert result["verdict"] == ""
        assert result["verdict_summary"] == ""

    def test_parse_handles_no_headers(self):
        result = parse_reflection_report("Just some random text without headers.")
        assert result["quality_assessment"] == ""
        assert result["verdict"] == ""

    def test_parse_verdict_with_markdown_bold(self):
        """**PASS** should be parsed as PASS verdict (strip markdown formatting)."""
        report = """### Verdict
**PASS** — Task completed correctly.
"""
        result = parse_reflection_report(report)
        assert result["verdict"] == "PASS"
        assert "Task completed correctly" in result["verdict_summary"]

    def test_parse_verdict_with_markdown_italic(self):
        report = """### Verdict
*FAIL* — Major issues.
"""
        result = parse_reflection_report(report)
        assert result["verdict"] == "FAIL"

    def test_parse_verdict_with_backticks(self):
        report = """### Verdict
`NEEDS_WORK` Some issues to address.
"""
        result = parse_reflection_report(report)
        assert result["verdict"] == "NEEDS_WORK"

    def test_parse_verdict_with_mixed_formatting(self):
        report = """### Verdict
**`PASS`** — Looks good.
"""
        result = parse_reflection_report(report)
        assert result["verdict"] == "PASS"

    def test_parse_verdict_deduplicates_stuttered_summary(self):
        """Agent output that repeats the summary should be deduplicated."""
        report = """### Verdict
PASS — Task completed correctly.
The review is complete. Here's the summary:
**Verdict: PASS** — The task was done correctly.
The review is complete. Here's the summary:
**Verdict: PASS** — The task was done correctly.
"""
        result = parse_reflection_report(report)
        assert result["verdict"] == "PASS"
        # Should contain the first occurrence but not the duplicate
        assert "The review is complete" in result["verdict_summary"]
        # Count occurrences — should appear only once
        assert result["verdict_summary"].count("The review is complete") == 1

    def test_parse_plain_verdict_still_works(self):
        """Regression guard: plain PASS/NEEDS_WORK/FAIL without formatting."""
        result = parse_reflection_report(self.FULL_REPORT)
        assert result["verdict"] == "NEEDS_WORK"
        assert "missing critical input validation" in result["verdict_summary"]


class TestStripOdinEnvelopes:
    """_strip_odin_envelopes() removes ODIN-STATUS/SUMMARY protocol framing."""

    def test_strips_single_envelope(self):
        raw = (
            "### Verdict\nNEEDS_WORK — bad joke\n\n"
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            "Review complete: NEEDS_WORK"
        )
        result = _strip_odin_envelopes(raw)
        assert "ODIN-STATUS" not in result
        assert "ODIN-SUMMARY" not in result
        assert "### Verdict" in result
        assert "NEEDS_WORK — bad joke" in result

    def test_strips_repeated_envelopes(self):
        """Reproduces the bug: agent outputs envelope multiple times."""
        raw = (
            "Review content here.\n\n"
            "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nDone\n\n"
            "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nDone\n"
        )
        result = _strip_odin_envelopes(raw)
        assert result == "Review content here."
        assert "ODIN-STATUS" not in result

    def test_preserves_output_without_envelope(self):
        raw = "### Verdict\nPASS — all good"
        assert _strip_odin_envelopes(raw) == raw

    def test_parse_after_strip_produces_clean_summary(self):
        """End-to-end: strip then parse gives clean verdict_summary."""
        raw = (
            "### Quality Assessment\nGood work.\n\n"
            "### Verdict\nNEEDS_WORK — missing validation\n\n"
            "-------ODIN-STATUS-------\nSUCCESS\n"
            "-------ODIN-SUMMARY-------\nReview complete: NEEDS_WORK"
        )
        clean = _strip_odin_envelopes(raw)
        parsed = parse_reflection_report(clean)
        assert parsed["verdict"] == "NEEDS_WORK"
        assert "missing validation" in parsed["verdict_summary"]
        assert "ODIN-STATUS" not in parsed["verdict_summary"]
        assert "ODIN-SUMMARY" not in parsed["verdict_summary"]


class TestExtractTokenUsage:
    """_extract_token_usage() sums token data from Claude stream-json step_finish events."""

    def _make_step_finish(self, input_t=100, output_t=50, cache_read=0, cache_write=0):
        return json.dumps({
            "type": "step_finish",
            "part": {
                "tokens": {
                    "total": input_t + output_t,
                    "input": input_t,
                    "output": output_t,
                    "cache": {"read": cache_read, "write": cache_write},
                }
            }
        })

    def test_sums_multiple_steps(self):
        raw = "\n".join([
            self._make_step_finish(100, 50, 80, 20),
            self._make_step_finish(200, 80, 150, 30),
        ])
        result = _extract_token_usage(raw)
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 130
        assert result["total_tokens"] == 430
        assert result["cache_read_tokens"] == 230
        assert result["cache_write_tokens"] == 50

    def test_returns_empty_for_no_tokens(self):
        raw = '{"type": "step_start"}\n{"type": "text", "text": "hello"}\n'
        result = _extract_token_usage(raw)
        assert result == {}

    def test_handles_non_json_lines(self):
        raw = "some plain text\n" + self._make_step_finish(50, 25) + "\n"
        result = _extract_token_usage(raw)
        assert result["total_tokens"] == 75

    def test_extracts_from_model_usage_event(self):
        """Claude Code CLI puts aggregate tokens in a modelUsage event."""
        raw = "\n".join([
            '{"type": "content_block_delta", "delta": {"text": "hello"}}',
            json.dumps({"modelUsage": {
                "claude-opus-4-6": {
                    "inputTokens": 5000,
                    "outputTokens": 1200,
                    "cacheReadInputTokens": 3000,
                    "cacheCreationInputTokens": 800,
                }
            }}),
        ])
        result = _extract_token_usage(raw)
        assert result["input_tokens"] == 5000
        assert result["output_tokens"] == 1200
        assert result["total_tokens"] == 6200
        assert result["cache_read_tokens"] == 3000
        assert result["cache_write_tokens"] == 800

    def test_model_usage_preferred_over_step_finish(self):
        """When both modelUsage and step_finish exist, modelUsage wins."""
        raw = "\n".join([
            self._make_step_finish(100, 50),
            self._make_step_finish(200, 80),
            json.dumps({"modelUsage": {
                "claude-opus-4-6": {
                    "inputTokens": 9000,
                    "outputTokens": 2000,
                }
            }}),
        ])
        result = _extract_token_usage(raw)
        # modelUsage aggregate should take precedence
        assert result["input_tokens"] == 9000
        assert result["output_tokens"] == 2000
        assert result["total_tokens"] == 11000
