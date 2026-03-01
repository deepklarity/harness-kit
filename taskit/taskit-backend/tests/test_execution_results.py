"""Tests for execution result processing and the execution_result endpoint.

Covers:
- extract_agent_text: Claude JSONL, Qwen JSON, plain text
- parse_envelope: ODIN-STATUS block extraction
- compose_comment: metrics formatting
- POST /tasks/:id/execution_result/: atomic status + comment creation
"""

import json
from pathlib import Path

from .base import APITestCase
from tasks.execution_processing import extract_agent_text, parse_envelope, compose_comment
from tasks.models import TaskComment, TaskHistory


# ── Unit tests: extract_agent_text ───────────────────────────────────


class TestExtractAgentText(APITestCase):
    """Extract human-readable text from structured CLI output."""

    def test_plain_text_passthrough(self):
        """Non-JSON output is returned as-is."""
        text = "Hello world\n\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nDone."
        result, usage = extract_agent_text(text)
        self.assertEqual(result, text)
        self.assertEqual(usage, {})

    def test_empty_input(self):
        result, usage = extract_agent_text("")
        self.assertEqual(result, "")
        result2, usage2 = extract_agent_text("   ")
        self.assertEqual(result2, "   ")

    def test_claude_jsonl(self):
        """Claude Code JSONL format — extract text from {"type":"text"} events."""
        lines = [
            '{"type":"text","part":{"text":"Hello from Claude."}}',
            '{"type":"step_finish","part":{"reason":"stop"}}',
        ]
        raw = "\n".join(lines)
        result, usage = extract_agent_text(raw)
        self.assertEqual(result, "Hello from Claude.")

    def test_claude_jsonl_multi_parts(self):
        """Multiple text events are joined."""
        lines = [
            '{"type":"text","part":{"text":"Part 1"}}',
            '{"type":"text","part":{"text":"Part 2"}}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "Part 1\nPart 2")

    def test_qwen_json(self):
        """Qwen CLI format — extract result from {"subtype":"success"} event."""
        lines = [
            '{"type":"result","subtype":"success","result":"Qwen completed the task."}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "Qwen completed the task.")

    def test_mixed_json_and_non_json(self):
        """Non-JSON lines (warnings) are skipped."""
        lines = [
            "WARNING: something happened",
            '{"type":"text","part":{"text":"Actual output."}}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "Actual output.")

    def test_dirty_claude_output(self):
        """Real-world dirty output: JSON fragments from CLI streaming."""
        raw = (
            'Completed in 49.3s\n\n'
            '\\nWrote a reflective paragraph.","time":{"start":123,"end":123}}}\n'
            '{"type":"text","part":{"text":"Clean text here."}}\n'
            '{"type":"step_finish","timestamp":123,"part":{"reason":"stop"}}'
        )
        result, _ = extract_agent_text(raw)
        self.assertEqual(result, "Clean text here.")

    def test_gemini_stream_json(self):
        """Gemini stream-json format — {"type":"text","text":"..."}."""
        lines = [
            '{"type":"text","text":"Hello from Gemini."}',
            '{"type":"tool_use","tool_name":"write_file","parameters":{"content":"<html>"}}',
            '{"type":"text","text":" Task complete."}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "Hello from Gemini.\n Task complete.")

    def test_gemini_with_envelope(self):
        """Gemini stream-json with ODIN-STATUS envelope embedded in text events."""
        lines = [
            '{"type":"text","text":"Created the file.\\n\\n-------ODIN-STATUS-------\\nSUCCESS\\n-------ODIN-SUMMARY-------\\nWrote table HTML."}',
            '{"type":"tool_use","tool_name":"write_file","parameters":{"content":"<h2>Table</h2>"}}',
        ]
        raw = "\n".join(lines)
        agent_text, _ = extract_agent_text(raw)
        clean, success, summary = parse_envelope(agent_text)
        self.assertTrue(success)
        self.assertEqual(summary, "Wrote table HTML.")

    def test_claude_content_block_delta(self):
        """Claude stream-json delta format — {"type":"content_block_delta"}."""
        lines = [
            '{"type":"content_block_delta","delta":{"text":"Hello "}}',
            '{"type":"content_block_delta","delta":{"text":"world."}}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "Hello \nworld.")

    def test_claude_result_type(self):
        """Claude stream-json result event — {"type":"result","result":"..."}."""
        lines = [
            '{"type":"result","result":"Final answer here."}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "Final answer here.")


# ── Unit tests: parse_envelope ───────────────────────────────────────


class TestParseEnvelope(APITestCase):
    """Parse ODIN-STATUS envelope from agent output."""

    def test_success_with_summary(self):
        text = "Work done.\n\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nCompleted successfully."
        clean, success, summary = parse_envelope(text)
        self.assertEqual(clean, "Work done.")
        self.assertTrue(success)
        self.assertEqual(summary, "Completed successfully.")

    def test_failed_with_summary(self):
        text = "Error.\n\n-------ODIN-STATUS-------\nFAILED\n-------ODIN-SUMMARY-------\nSyntax error."
        clean, success, summary = parse_envelope(text)
        self.assertEqual(clean, "Error.")
        self.assertFalse(success)
        self.assertEqual(summary, "Syntax error.")

    def test_no_envelope(self):
        text = "Just regular output."
        clean, success, summary = parse_envelope(text)
        self.assertEqual(clean, "Just regular output.")
        self.assertIsNone(success)
        self.assertIsNone(summary)

    def test_status_without_summary(self):
        text = "Output\n\n-------ODIN-STATUS-------\nSUCCESS"
        clean, success, summary = parse_envelope(text)
        self.assertEqual(clean, "Output")
        self.assertTrue(success)
        self.assertIsNone(summary)


# ── Unit tests: compose_comment ──────────────────────────────────────


class TestComposeComment(APITestCase):
    """Compose metrics-inline comments."""

    def test_with_duration_and_tokens(self):
        metadata = {
            "usage": {
                "total_tokens": 8420,
                "input_tokens": 5200,
                "output_tokens": 3220,
            }
        }
        result = compose_comment("Completed", 12300.0, metadata, "Task done.")
        self.assertIn("Completed in 12.3s", result)
        self.assertIn("8,420 tokens", result)
        self.assertIn("5,200 in", result)
        self.assertIn("3,220 out", result)
        self.assertIn("Task done.", result)

    def test_duration_only(self):
        result = compose_comment("Failed", 5000.0, {}, "Error occurred.")
        self.assertIn("Failed in 5.0s", result)
        self.assertIn("Error occurred.", result)

    def test_no_metrics(self):
        result = compose_comment("Completed", None, {}, "Done.")
        self.assertEqual(result, "Done.")

    def test_tokens_without_breakdown(self):
        metadata = {"usage": {"total_tokens": 1000}}
        result = compose_comment("Completed", None, metadata, "Done.")
        self.assertIn("1,000 tokens", result)


# ── Integration tests: execution_result endpoint ─────────────────────


class TestExecutionResultEndpoint(APITestCase):
    """POST /tasks/:id/execution_result/ — atomic execution recording."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status="IN_PROGRESS")

    def test_success_creates_comment_and_updates_status(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "Work done.\n\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nCompleted successfully.",
                    "error": None,
                    "duration_ms": 12345.6,
                    "agent": "minimax",
                    "metadata": {
                        "usage": {"total_tokens": 8420, "input_tokens": 5200, "output_tokens": 3220}
                    },
                },
                "status": "DONE",
                "updated_by": "minimax+MiniMax-M2.5@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        # Task status updated
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "DONE")

        # Comment created
        comments = TaskComment.objects.filter(task=self.task)
        self.assertEqual(comments.count(), 1)
        comment = comments.first()
        self.assertEqual(comment.author_email, "minimax+MiniMax-M2.5@odin.agent")
        self.assertIn("Completed in 12.3s", comment.content)
        self.assertIn("8,420 tokens", comment.content)
        self.assertIn("Completed successfully.", comment.content)

        # History recorded
        history = TaskHistory.objects.filter(task=self.task, field_name="status")
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().new_value, "DONE")

    def test_failure_creates_error_comment(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": False,
                    "raw_output": "",
                    "error": "syntax error in generated file",
                    "duration_ms": 23100.0,
                    "agent": "gemini",
                    "metadata": {
                        "usage": {"total_tokens": 15200, "input_tokens": 10000, "output_tokens": 5200}
                    },
                },
                "status": "FAILED",
                "updated_by": "gemini+gemini-2.0@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "FAILED")

        comment = TaskComment.objects.filter(task=self.task).first()
        self.assertIn("Failed in 23.1s", comment.content)
        self.assertIn("15,200 tokens", comment.content)
        self.assertIn("syntax error", comment.content)

    def test_failure_with_structured_reason_fields(self):
        resp = self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": False,
                    "raw_output": "",
                    "error": "upstream api error",
                    "duration_ms": 1200.0,
                    "agent": "qwen",
                    "failure_type": "llm_call_failure",
                    "failure_reason": "Qwen API returned HTTP 429",
                    "failure_origin": "orchestrator:task_execution",
                    "metadata": {"failure_debug": "trace-id=abc"},
                },
                "status": "FAILED",
                "updated_by": "qwen+qwen3@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "FAILED")
        self.assertEqual(self.task.metadata.get("last_failure_type"), "llm_call_failure")
        self.assertEqual(self.task.metadata.get("last_failure_origin"), "orchestrator:task_execution")

        comment = TaskComment.objects.filter(task=self.task).first()
        self.assertIn("Failure type: llm_call_failure", comment.content)
        self.assertIn("Reason: Qwen API returned HTTP 429", comment.content)
        self.assertIn("Origin: orchestrator:task_execution", comment.content)

    def test_claude_jsonl_extraction(self):
        """Dirty Claude JSONL output is cleaned before comment."""
        raw_output = "\n".join([
            '{"type":"text","part":{"text":"I wrote the code."}}',
            '{"type":"text","part":{"text":"\\n\\n-------ODIN-STATUS-------\\nSUCCESS\\n-------ODIN-SUMMARY-------\\nCode written."}}',
            '{"type":"step_finish","part":{"reason":"stop"}}',
        ])
        resp = self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": raw_output,
                    "duration_ms": 5000.0,
                    "agent": "claude",
                    "metadata": {},
                },
                "status": "DONE",
                "updated_by": "claude+sonnet-4-5@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        comment = TaskComment.objects.filter(task=self.task).first()
        self.assertIn("Code written.", comment.content)

    def test_plain_text_without_envelope(self):
        """When no ODIN-STATUS envelope, uses default summary."""
        resp = self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "Just some plain output.",
                    "duration_ms": 3000.0,
                    "agent": "mock",
                    "metadata": {},
                },
                "status": "DONE",
                "updated_by": "mock@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        comment = TaskComment.objects.filter(task=self.task).first()
        self.assertIn("Completed successfully", comment.content)

    def test_nonexistent_task_404(self):
        resp = self.client.post(
            "/tasks/99999/execution_result/",
            {
                "execution_result": {"success": True, "raw_output": ""},
                "status": "DONE",
                "updated_by": "test@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_stores_execution_metadata(self):
        """Duration is stored in task.metadata; usage is NOT cached (computed from trace)."""
        self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "",
                    "duration_ms": 7000.0,
                    "agent": "minimax",
                    "metadata": {
                        "usage": {"total_tokens": 500, "input_tokens": 300, "output_tokens": 200}
                    },
                },
                "status": "DONE",
                "updated_by": "minimax@odin.agent",
            },
            format="json",
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.metadata["last_duration_ms"], 7000.0)
        # Usage is no longer cached in metadata — it's computed from trace comments
        self.assertNotIn("last_usage", self.task.metadata)

    def test_usage_computed_from_trace_comment_claude(self):
        """Usage is computed on-the-fly from the trace comment, not cached in metadata.

        Simulates the full flow: orchestrator posts a trace comment with
        attachments=["trace:execution_jsonl"], then the API response's usage
        field reflects tokens extracted from that trace.
        """
        raw_output = "\n".join([
            '{"type":"text","part":{"text":"-------ODIN-STATUS-------\\nSUCCESS\\n-------ODIN-SUMMARY-------\\nDone."}}',
            '{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":38,"outputTokens":925,"cacheReadInputTokens":296555,"cacheCreationInputTokens":15559}}}',
        ])
        # Simulate the orchestrator posting the trace comment
        TaskComment.objects.create(
            task=self.task,
            author_email="odin@system",
            content=raw_output,
            attachments=["trace:execution_jsonl"],
        )
        self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": raw_output,
                    "duration_ms": 42000.0,
                    "agent": "Claude Code",
                    "metadata": {},
                },
                "status": "REVIEW",
                "updated_by": "claude+claude-sonnet-4-5@odin.agent",
            },
            format="json",
        )
        # Usage should NOT be cached in metadata
        self.task.refresh_from_db()
        self.assertNotIn("last_usage", self.task.metadata)
        # Usage should be computed from trace and returned in API response
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        usage = resp.data.get("usage")
        self.assertIsNotNone(usage, "usage not computed — trace comment extraction failed")
        self.assertEqual(usage["input_tokens"], 38)
        self.assertEqual(usage["output_tokens"], 925)
        self.assertEqual(usage["total_tokens"], 963)
        self.assertEqual(usage["cache_read_input_tokens"], 296555)

    def test_usage_computed_from_trace_comment_glm(self):
        """Usage is computed on-the-fly from GLM trace comment."""
        raw_output = "\n".join([
            '{"type":"text","text":"-------ODIN-STATUS-------\\nSUCCESS\\n-------ODIN-SUMMARY-------\\nCreated jokes/glm.txt"}',
            '{"type":"step_finish","timestamp":123,"part":{"tokens":{"total":15119,"input":13,"output":46,"cache":{"read":15060}}}}',
        ])
        TaskComment.objects.create(
            task=self.task,
            author_email="odin@system",
            content=raw_output,
            attachments=["trace:execution_jsonl"],
        )
        self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": raw_output,
                    "duration_ms": 35000.0,
                    "agent": "GLM",
                    "metadata": {},
                },
                "status": "REVIEW",
                "updated_by": "glm+zai-coding-plan/glm-4.7@odin.agent",
            },
            format="json",
        )
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        usage = resp.data.get("usage")
        self.assertIsNotNone(usage, "usage not computed — step_finish extraction failed")
        self.assertEqual(usage["input_tokens"], 13)
        self.assertEqual(usage["output_tokens"], 46)
        self.assertEqual(usage["total_tokens"], 15119)

    def test_usage_from_trace_not_from_metadata(self):
        """Usage comes from trace comment, not from metadata.usage passed in the request."""
        raw_output = '{"type":"text","part":{"text":"done"}}\n{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":100,"outputTokens":200}}}'
        # Trace comment has the real data
        TaskComment.objects.create(
            task=self.task,
            author_email="odin@system",
            content=raw_output,
            attachments=["trace:execution_jsonl"],
        )
        self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": raw_output,
                    "duration_ms": 5000.0,
                    "agent": "Claude Code",
                    "metadata": {
                        "usage": {"total_tokens": 999, "input_tokens": 999, "output_tokens": 999}
                    },
                },
                "status": "REVIEW",
                "updated_by": "claude@odin.agent",
            },
            format="json",
        )
        # Usage NOT cached in metadata
        self.task.refresh_from_db()
        self.assertNotIn("last_usage", self.task.metadata)
        # Usage computed from trace
        resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        usage = resp.data.get("usage")
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 200)

    def test_no_status_change_no_history(self):
        """If status doesn't change, no history record is created."""
        # Set task to DONE first
        self.task.status = "DONE"
        self.task.save()

        self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {"success": True, "raw_output": ""},
                "status": "DONE",
                "updated_by": "test@odin.agent",
            },
            format="json",
        )
        # No status history (only the initial "created" from setUp if any)
        status_history = TaskHistory.objects.filter(task=self.task, field_name="status")
        self.assertEqual(status_history.count(), 0)


# ── Unit tests: Codex output parsing ─────────────────────────────────


class TestExtractCodexOutput(APITestCase):
    """Codex CLI produces plain-text output with section headers.

    extract_agent_text() should detect the Codex format and extract only
    the agent's 'codex' blocks, stripping CLI boilerplate.
    """

    CODEX_OUTPUT = (
        "OpenAI Codex v0.101.0 (research preview)\n"
        "--------\n"
        "workdir: /tmp/test\n"
        "model: codex-mini-latest\n"
        "approval: full-auto\n"
        "sandbox: none\n"
        "--------\n"
        "user\n"
        "Write a poem about testing.\n"
        "thinking\n"
        "I need to write a poem about testing.\n"
        "codex\n"
        "Here is a poem about testing:\n"
        "\n"
        "Tests are the bedrock of code,\n"
        "They light the path on every road.\n"
        "exec\n"
        "cat poem.txt\n"
        "Tests are the bedrock...\n"
        "tokens used\n"
        "1234\n"
    )

    def test_extracts_codex_block(self):
        """Only the 'codex' block content is extracted."""
        result, usage = extract_agent_text(self.CODEX_OUTPUT)
        self.assertIn("Here is a poem about testing:", result)
        self.assertIn("They light the path on every road.", result)

    def test_strips_header(self):
        """CLI header (version, workdir, model) is not in output."""
        result, _ = extract_agent_text(self.CODEX_OUTPUT)
        self.assertNotIn("OpenAI Codex v", result)
        self.assertNotIn("workdir:", result)
        self.assertNotIn("codex-mini-latest", result)

    def test_strips_user_block(self):
        """User prompt is not in output."""
        result, _ = extract_agent_text(self.CODEX_OUTPUT)
        self.assertNotIn("Write a poem about testing.", result)

    def test_strips_thinking_block(self):
        """Thinking content is not in output."""
        result, _ = extract_agent_text(self.CODEX_OUTPUT)
        self.assertNotIn("I need to write a poem about testing.", result)

    def test_strips_exec_block(self):
        """Exec/shell command output is not in output."""
        result, _ = extract_agent_text(self.CODEX_OUTPUT)
        self.assertNotIn("cat poem.txt", result)

    def test_strips_token_count(self):
        """Token count footer is not in output."""
        result, _ = extract_agent_text(self.CODEX_OUTPUT)
        self.assertNotIn("tokens used", result)

    def test_non_codex_output_passes_through(self):
        """Plain text without Codex header is returned as-is."""
        text = "Just some regular output."
        result, _ = extract_agent_text(text)
        self.assertEqual(result, text)

    def test_multiple_codex_blocks(self):
        """Multiple codex blocks are concatenated."""
        raw = (
            "OpenAI Codex v0.102.0 (research preview)\n"
            "--------\n"
            "workdir: /tmp\n"
            "--------\n"
            "user\n"
            "Do two things.\n"
            "codex\n"
            "First thing done.\n"
            "exec\n"
            "ls -la\n"
            "codex\n"
            "Second thing done.\n"
            "tokens used\n"
            "500\n"
        )
        result, _ = extract_agent_text(raw)
        self.assertIn("First thing done.", result)
        self.assertIn("Second thing done.", result)

    def test_codex_with_odin_envelope(self):
        """Codex output with ODIN-STATUS envelope is properly parsed end-to-end."""
        raw = (
            "OpenAI Codex v0.101.0 (research preview)\n"
            "--------\n"
            "workdir: /tmp/test\n"
            "model: o4-mini\n"
            "--------\n"
            "user\n"
            "Write a file.\n"
            "codex\n"
            "I wrote the file.\n"
            "\n"
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            "File written successfully.\n"
            "tokens used\n"
            "800\n"
        )
        agent_text, _ = extract_agent_text(raw)
        clean, success, summary = parse_envelope(agent_text)
        self.assertTrue(success)
        self.assertEqual(summary, "File written successfully.")
        self.assertIn("I wrote the file.", clean)


# ── Unit tests: Codex JSONL output parsing ───────────────────────────


class TestExtractCodexJsonl(APITestCase):
    """Codex CLI with --json flag produces JSONL streaming events.

    extract_agent_text() should extract agent_message text from
    item.completed events and usage from turn.completed events.
    """

    def test_extracts_agent_message(self):
        """Agent messages are extracted from item.completed events."""
        lines = [
            '{"type":"thread.started","thread_id":"abc-123"}',
            '{"type":"turn.started"}',
            '{"type":"item.started","item":{"id":"item_0","type":"command_execution","command":"mkdir jokes"}}',
            '{"type":"item.completed","item":{"id":"item_0","type":"command_execution","command":"mkdir jokes","exit_code":0}}',
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"I created the jokes directory and wrote a joke."}}',
            '{"type":"turn.completed","usage":{"input_tokens":5000,"output_tokens":200,"cached_input_tokens":4500}}',
        ]
        result, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "I created the jokes directory and wrote a joke.")
        self.assertEqual(usage["input_tokens"], 5000)
        self.assertEqual(usage["output_tokens"], 200)
        self.assertEqual(usage["total_tokens"], 5200)

    def test_skips_non_agent_items(self):
        """command_execution and other item types are not extracted as text."""
        lines = [
            '{"type":"item.completed","item":{"id":"item_0","type":"command_execution","command":"ls","exit_code":0}}',
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Done."}}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertEqual(result, "Done.")
        self.assertNotIn("ls", result)

    def test_multiple_agent_messages(self):
        """Multiple agent_message items are joined."""
        lines = [
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Step 1 complete."}}',
            '{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"Step 2 complete."}}',
        ]
        result, _ = extract_agent_text("\n".join(lines))
        self.assertIn("Step 1 complete.", result)
        self.assertIn("Step 2 complete.", result)

    def test_codex_jsonl_with_envelope(self):
        """Codex JSONL with ODIN-STATUS envelope parses end-to-end."""
        lines = [
            '{"type":"thread.started","thread_id":"abc"}',
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Task done.\\n\\n-------ODIN-STATUS-------\\nSUCCESS\\n-------ODIN-SUMMARY-------\\nWrote the file."}}',
            '{"type":"turn.completed","usage":{"input_tokens":1000,"output_tokens":100}}',
        ]
        agent_text, usage = extract_agent_text("\n".join(lines))
        clean, success, summary = parse_envelope(agent_text)
        self.assertTrue(success)
        self.assertEqual(summary, "Wrote the file.")
        self.assertIn("Task done.", clean)


# ── Unit tests: Claude modelUsage token extraction ───────────────────


class TestExtractClaudeUsage(APITestCase):
    """Claude Code final line includes modelUsage with per-model token counts.

    Real format from Claude Code trace (comment 375):
    Last line is a JSON object with "modelUsage" containing camelCase keys
    nested under model name(s).
    """

    def test_single_model_usage(self):
        """Extract tokens from a single model in modelUsage."""
        lines = [
            '{"type":"text","part":{"text":"I completed the task."}}',
            '{"type":"step_finish","part":{"reason":"stop"}}',
            '{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":38,"outputTokens":925,"cacheReadInputTokens":296555,"cacheCreationInputTokens":0,"costUSD":0.268}}}',
        ]
        _, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(usage["input_tokens"], 38)
        self.assertEqual(usage["output_tokens"], 925)
        self.assertEqual(usage["total_tokens"], 963)
        self.assertEqual(usage["cache_read_input_tokens"], 296555)
        self.assertEqual(usage["cache_creation_input_tokens"], 0)

    def test_multi_model_usage(self):
        """When Claude uses subagents (haiku), tokens are summed across models."""
        lines = [
            '{"type":"text","part":{"text":"Done."}}',
            '{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":100,"outputTokens":500,"cacheReadInputTokens":1000,"cacheCreationInputTokens":200},"claude-haiku-3-5":{"inputTokens":50,"outputTokens":80,"cacheReadInputTokens":0,"cacheCreationInputTokens":0}}}',
        ]
        _, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(usage["input_tokens"], 150)   # 100 + 50
        self.assertEqual(usage["output_tokens"], 580)   # 500 + 80
        self.assertEqual(usage["total_tokens"], 730)     # 150 + 580
        self.assertEqual(usage["cache_read_input_tokens"], 1000)

    def test_text_still_extracted_with_usage(self):
        """Text extraction works alongside modelUsage extraction."""
        lines = [
            '{"type":"text","part":{"text":"Part 1"}}',
            '{"type":"text","part":{"text":"Part 2"}}',
            '{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":10,"outputTokens":20}}}',
        ]
        text, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(text, "Part 1\nPart 2")
        self.assertEqual(usage["total_tokens"], 30)


# ── Unit tests: MiniMax step_finish token extraction ─────────────────


class TestExtractMiniMaxUsage(APITestCase):
    """MiniMax (opencode/kilo harness) emits step_finish events with tokens.

    Real format from MiniMax trace (comment 355):
    Each step produces {"type":"step_finish","part":{"tokens":{"total":N,...}}}
    Tokens must be accumulated across all steps.
    """

    def test_single_step_tokens(self):
        """Extract tokens from a single step_finish event."""
        lines = [
            '{"type":"text","text":"Hello from MiniMax."}',
            '{"type":"step_finish","part":{"tokens":{"total":14926,"input":94,"output":108,"cache":{"read":14482}}}}',
        ]
        _, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(usage["input_tokens"], 94)
        self.assertEqual(usage["output_tokens"], 108)
        self.assertEqual(usage["total_tokens"], 14926)

    def test_multi_step_accumulation(self):
        """Tokens from multiple step_finish events are summed."""
        lines = [
            '{"type":"text","text":"Step 1 output."}',
            '{"type":"step_finish","part":{"tokens":{"total":5000,"input":100,"output":200,"cache":{"read":4700}}}}',
            '{"type":"text","text":"Step 2 output."}',
            '{"type":"step_finish","part":{"tokens":{"total":3000,"input":80,"output":150,"cache":{"read":2770}}}}',
            '{"type":"text","text":"Step 3 output."}',
            '{"type":"step_finish","part":{"tokens":{"total":2000,"input":60,"output":100,"cache":{"read":1840}}}}',
        ]
        text, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(text, "Step 1 output.\nStep 2 output.\nStep 3 output.")
        self.assertEqual(usage["input_tokens"], 240)   # 100 + 80 + 60
        self.assertEqual(usage["output_tokens"], 450)   # 200 + 150 + 100
        self.assertEqual(usage["total_tokens"], 10000)   # 5000 + 3000 + 2000

    def test_step_finish_without_tokens(self):
        """step_finish without tokens field is ignored for usage."""
        lines = [
            '{"type":"text","text":"Output."}',
            '{"type":"step_finish","part":{"reason":"stop"}}',
        ]
        text, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(text, "Output.")
        self.assertEqual(usage, {})


# ── Unit tests: GLM step_finish token extraction ─────────────────────


class TestExtractGLMUsage(APITestCase):
    """GLM (kilo harness) uses the same step_finish format as MiniMax.

    Real format from GLM trace (comment 359):
    Same {"type":"step_finish","part":{"tokens":{...}}} structure.
    """

    def test_glm_step_tokens(self):
        """GLM step_finish tokens are extracted identically to MiniMax."""
        lines = [
            '{"type":"text","text":"GLM completed the analysis."}',
            '{"type":"step_finish","part":{"tokens":{"total":8500,"input":200,"output":300,"cache":{"read":8000}}}}',
        ]
        _, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(usage["input_tokens"], 200)
        self.assertEqual(usage["output_tokens"], 300)
        self.assertEqual(usage["total_tokens"], 8500)

    def test_glm_multi_step(self):
        """GLM multi-step accumulation works correctly."""
        lines = [
            '{"type":"text","text":"Analyzing..."}',
            '{"type":"step_finish","part":{"tokens":{"total":4000,"input":150,"output":250,"cache":{"read":3600}}}}',
            '{"type":"text","text":"Writing solution..."}',
            '{"type":"step_finish","part":{"tokens":{"total":6000,"input":180,"output":320,"cache":{"read":5500}}}}',
        ]
        text, usage = extract_agent_text("\n".join(lines))
        self.assertEqual(text, "Analyzing...\nWriting solution...")
        self.assertEqual(usage["input_tokens"], 330)   # 150 + 180
        self.assertEqual(usage["output_tokens"], 570)   # 250 + 320
        self.assertEqual(usage["total_tokens"], 10000)   # 4000 + 6000
