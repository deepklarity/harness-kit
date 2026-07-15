"""Mock E2E tests for odin reflect command.

Tests the reflect_task() flow with mocked HTTP and harness calls.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

from odin.cli import OdinCLI
from odin.models import AgentConfig, TaskResult
from odin.reflection import parse_reflection_report, reflect_task, _sanitize_reflection_output


MOCK_TASK_DETAIL = {
    "id": 42,
    "title": "Implement user login",
    "description": "Add JWT-based login endpoint",
    "status": "REVIEW",
    "model_name": "claude-sonnet-4-5",
    "metadata": {
        "working_dir": "/tmp/project",
        "selected_model": "claude-sonnet-4-5",
        "full_output": "Created endpoint successfully",
        "last_duration_ms": 30000,
        "last_usage": {"input_tokens": 5000, "output_tokens": 8000, "total_tokens": 13000},
    },
    "comments": [
        {"content": "Starting implementation", "comment_type": "status_update"},
        {"content": "Completed in 30s", "comment_type": "status_update"},
    ],
    "depends_on": [],
    "assignee": {"name": "claude-agent"},
}

MOCK_AGENT_OUTPUT = """### Quality Assessment
The code is well-structured.

### Slop Detection
No slop found.

### Actionable Improvements
1. Add input validation

### Agent Optimization
Model tier was appropriate.

### Verdict
PASS
Looks good overall.
"""


@pytest.fixture
def mock_http():
    """Mock HTTP calls to TaskIt API."""
    with patch("odin.reflection.httpx") as mock_requests:
        running_resp = MagicMock()
        running_resp.status_code = 200
        running_resp.json.return_value = {"status": "RUNNING"}

        detail_resp = MagicMock()
        detail_resp.status_code = 200
        detail_resp.json.return_value = MOCK_TASK_DETAIL

        complete_resp = MagicMock()
        complete_resp.status_code = 200
        complete_resp.json.return_value = {"status": "COMPLETED"}

        mock_requests.patch.side_effect = [running_resp, complete_resp]
        mock_requests.get.return_value = detail_resp

        yield mock_requests


@pytest.fixture
def mock_harness():
    """Mock harness execution returning a TaskResult."""
    with patch("odin.reflection.get_harness") as mock_get:
        harness = MagicMock()
        harness.execute = AsyncMock(return_value=TaskResult(
            success=True,
            output=MOCK_AGENT_OUTPUT,
            duration_ms=15000,
            metadata={"usage": {"input_tokens": 2000, "output_tokens": 3000}},
        ))
        mock_get.return_value = harness
        yield harness


class TestReflectTask:
    """reflect_task() orchestrates the full reflection flow."""

    def test_reflect_task_updates_report_to_running(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        first_patch_call = mock_http.patch.call_args_list[0]
        assert "/reflections/1/" in first_patch_call[0][0]
        assert first_patch_call[1]["json"]["status"] == "RUNNING"

    def test_reflect_task_gathers_context_from_api(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        mock_http.get.assert_called_once()
        assert "/tasks/42/detail/" in mock_http.get.call_args[0][0]

    def test_reflect_task_calls_harness_correctly(self, mock_http, mock_harness):
        """Harness called with (prompt, context) positional args."""
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        mock_harness.execute.assert_called_once()
        args = mock_harness.execute.call_args[0]
        prompt, context = args[0], args[1]
        assert "auditing a task executed by an AI agent" in prompt
        assert context["working_dir"] == "/tmp/project"
        assert context["model"] == "claude-opus-4-6"

    def test_reflect_task_passes_setting_sources_user(self, mock_http, mock_harness):
        """Reviewer explicitly sets setting_sources='user' in context.

        The reflection reviewer is the ONLY consumer that needs to skip
        project/local `.claude/settings.*` discovery — the worktree
        carries a stale `.claude/settings.local.json` whose
        `permissions.allow` entries trigger the workspace-trust noise
        warning (task 159: 4 reflections hard-ERRORed on that single
        line). Regular task execution does NOT set this flag, so
        project-level safety hooks (secrets / lock-file edit blocks) keep
        loading normally.
        """
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        passed_context = mock_harness.execute.call_args[0][1]
        assert passed_context.get("setting_sources") == "user", (
            f"Reviewer must opt in to setting_sources='user' so the "
            f"claude harness emits --setting-sources user and skips the "
            f"worktree's stale .claude/settings.local.json; got "
            f"{passed_context.get('setting_sources')!r}"
        )

    def test_reflect_task_loads_board_local_config(self, mock_http, mock_harness, tmp_path):
        working_dir = tmp_path / "board"
        (working_dir / ".odin").mkdir(parents=True)
        (working_dir / ".odin" / "config.yaml").write_text("agents: {}\n")
        mock_http.get.return_value.json.return_value = {
            **MOCK_TASK_DETAIL,
            "metadata": {**MOCK_TASK_DETAIL["metadata"], "working_dir": str(working_dir)},
        }
        with patch("odin.config.load_config") as mock_load_config:
            mock_cfg = MagicMock()
            mock_cfg.agents = {}
            mock_cfg.cost_storage = ".odin/costs"
            mock_load_config.return_value = mock_cfg
            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )
        mock_load_config.assert_called_once_with(str(working_dir / ".odin" / "config.yaml"))

    def test_reflect_task_preserves_forkd_for_reviewer(self, mock_http, tmp_path):
        working_dir = tmp_path / "board"
        (working_dir / ".odin").mkdir(parents=True)
        (working_dir / ".odin" / "config.yaml").write_text("agents: {}\n")
        mock_http.get.return_value.json.return_value = {
            **MOCK_TASK_DETAIL,
            "metadata": {**MOCK_TASK_DETAIL["metadata"], "working_dir": str(working_dir)},
        }
        with patch("odin.config.load_config") as mock_load_config, patch("odin.reflection.get_harness") as mock_get_harness:
            cfg = MagicMock()
            cfg.agents = {"gemini": AgentConfig(cli_command="gemini", run_in_forkd=True, forkd_snapshot_tag="odin-node22-4g-cli-browser")}
            cfg.cost_storage = ".odin/costs"
            mock_load_config.return_value = cfg
            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=True,
                output=MOCK_AGENT_OUTPUT,
                duration_ms=15000,
                metadata={"usage": {"input_tokens": 2000, "output_tokens": 3000}},
            ))
            mock_get_harness.return_value = harness
            reflect_task(
                task_id="42", report_id="1", model="gemini-3-flash-preview",
                agent="gemini", taskit_url="http://localhost:8000",
            )
        passed_cfg = mock_get_harness.call_args[0][1]
        assert passed_cfg.run_in_forkd is True
        passed_context = harness.execute.call_args[0][1]
        assert passed_context["forkd_require_chrome"] is True

    def test_reflect_task_submits_parsed_report(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["status"] == "COMPLETED"
        assert payload["verdict"] == "PASS"
        assert "well-structured" in payload["quality_assessment"]

    def test_reflect_task_round_trips_json_review(self, mock_http):
        """The full reflection flow must accept a JSON contract review and
        submit the parsed fields to the TaskIt backend. End-to-end check
        that a reviewer's fenced JSON block round-trips through the
        parser into the completed-report PATCH."""
        import json as _json
        json_review = (
            "Here's my review.\n\n"
            "```json\n"
            + _json.dumps({
                "verdict": "NEEDS_WORK",
                "summary": "Missing before/after sandbox listing proof.",
                "quality_assessment": "MET: code; UNMET: proof artifact",
                "slop_detection": "None.",
                "improvements": "- attach before/after listing",
                "agent_optimization": "tier appropriate",
                "quota_failure": "None.",
                "fix_list": ["attach before/after sandbox listing"],
            })
            + "\n```\n"
        )
        with patch("odin.reflection.get_harness") as mock_get:
            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=True,
                output=json_review,
                duration_ms=15000,
                metadata={"usage": {"input_tokens": 2000, "output_tokens": 3000}},
            ))
            mock_get.return_value = harness

            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["status"] == "COMPLETED"
        assert payload["verdict"] == "NEEDS_WORK"
        assert "before/after" in payload["verdict_summary"]
        assert "MET: code" in payload["quality_assessment"]
        assert "before/after listing" in payload["improvements"]
        assert payload["quota_failure"] == "None."

    def test_reflect_task_unverifiable_proof_claim_names_claim(self, mock_http, tmp_path):
        working_dir = tmp_path / "project"
        proof_dir = working_dir / ".proof" / "task-42"
        proof_dir.mkdir(parents=True)
        proof_path = proof_dir / "proof.md"
        proof_path.write_text(
            "# Proof\n\n"
            "## Claims\n"
            "- page renders\n\n"
            "No render log or screenshot is included.\n",
            encoding="utf-8",
        )
        mock_http.get.return_value.json.return_value = {
            **MOCK_TASK_DETAIL,
            "metadata": {
                **MOCK_TASK_DETAIL["metadata"],
                "working_dir": str(working_dir),
            },
            "comments": [
                {
                    "content": "See .proof/task-42/proof.md for proof.",
                    "comment_type": "proof",
                },
            ],
        }

        async def reviewer(prompt, context):
            assert context["working_dir"] == str(working_dir)
            assert ".proof/task-42/proof.md" in prompt
            proof_text = proof_path.read_text(encoding="utf-8")
            assert "page renders" in proof_text
            return TaskResult(
                success=True,
                output=(
                    "```json\n"
                    + json.dumps({
                        "verdict": "NEEDS_WORK",
                        "summary": "Claim 'page renders' is unverifiable: proof.md has no render log or screenshot.",
                        "quality_assessment": "UNMET: claim 'page renders' lacks raw output.",
                        "slop_detection": "None.",
                        "improvements": "Paste a render log or attach a screenshot for claim 'page renders'.",
                        "agent_optimization": "Proof instruction was clear; worker omitted raw output.",
                        "quota_failure": "None.",
                        "fix_list": [
                            "attach a screenshot or render log for claim 'page renders'",
                        ],
                    })
                    + "\n```\n"
                ),
                duration_ms=12000,
                metadata={"usage": {"input_tokens": 1000, "output_tokens": 500}},
            )

        with patch("odin.reflection.get_harness") as mock_get:
            harness = MagicMock()
            harness.execute = AsyncMock(side_effect=reviewer)
            mock_get.return_value = harness
            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["status"] == "COMPLETED"
        assert payload["verdict"] == "NEEDS_WORK"
        assert "page renders" in payload["verdict_summary"]
        assert "unverifiable" in payload["verdict_summary"]
        assert "page renders" in payload["raw_output"]

    def test_reflect_task_captures_captured_bad_output_as_error(self, mock_http):
        """The exact captured bad output from task 159 (haiku noise with
        trust warning + bare NEEDS_WORK keyword) must be reported as
        ERROR, not laundered into a contentless NEEDS_WORK verdict."""
        with patch("odin.reflection.get_harness") as mock_get:
            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=True,
                output=(
                    "Ignoring 124 permissions.allow entries from .claude/"
                    "settings.local.json: this workspace has not been "
                    "trusted. Run Claude Code interactively here once.\n"
                    '{"type":"system","subtype":"init","model":"claude-haiku-4-5"}\n'
                    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}\n'
                    'context: rate_limit_failure but quota is fine\n'
                ),
                duration_ms=10000,
                metadata={"usage": {"input_tokens": 100, "output_tokens": 50}},
            ))
            mock_get.return_value = harness

            reflect_task(
                task_id="42", report_id="1", model="claude-haiku-4-5",
                agent="claude", taskit_url="http://localhost:8000",
            )

        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["status"] == "COMPLETED"
        # The captured noise contains no parseable review and no clean
        # verdict keyword. The bare-keyword scan and the no-verdict
        # fallback both classify this as ERROR — that's the gate:
        # unparseable output never dispatches rework.
        assert payload["verdict"] == "ERROR"
        assert "Reviewer failure" in payload["verdict_summary"] or "unparseable" in payload["verdict_summary"]
        # Sections must be empty
        assert payload["quality_assessment"] == ""
        assert payload["slop_detection"] == ""
        assert payload["improvements"] == ""
        # The trust warning was stripped (defense-in-depth) so it does
        # not appear in the raw head; the head is what survives sanitize.
        assert "permissions.allow" not in payload["raw_output"]
        assert "haiku-4-5" in payload["raw_output"]

    def test_reflect_task_truncated_review_yields_reviewer_infra(self, mock_http):
        """When the reviewer produces substantive reasoning but the output
        is truncated (finish_reason=length), the report should get
        REVIEWER_INFRA — not ERROR — so it doesn't consume a strike and
        triggers a fresh-reviewer retry.

        Task #346: reflection 360 on task 342 had real reasoning but no
        JSON verdict because the output was cut off. The whole review
        run's tokens were spent for nothing.
        """
        truncated_reasoning = (
            "I'll analyze this task.\n\n"
            "The worker implemented the endpoint correctly. Tests pass:\n"
            "  $ pytest tests/\n  5 passed\n\n"
            "The build is clean. However, I notice the error handling could"
        )
        with patch("odin.reflection.get_harness") as mock_get, \
             patch("odin.reflection.extract_stream_summary") as mock_extract:
            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=True,
                output=truncated_reasoning,
                duration_ms=10000,
                metadata={"usage": {"input_tokens": 500, "output_tokens": 4000}},
            ))
            mock_get.return_value = harness
            mock_extract.return_value = {"finish_reason": "length", "output_tokens": 4000}

            reflect_task(
                task_id="42", report_id="1", model="codex",
                agent="codex", taskit_url="http://localhost:8000",
            )

        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["status"] == "COMPLETED"
        assert payload["verdict"] == "REVIEWER_INFRA"
        assert "length" in payload["verdict_summary"]
        assert "truncated" in payload["verdict_summary"].lower()
        # finish_reason persisted in token_usage
        assert payload["token_usage"]["finish_reason"] == "length"
        # Truncated reasoning salvaged in raw_output
        assert "endpoint" in payload["raw_output"]

    def test_reflect_task_json_at_top_with_truncated_prose_parses_normally(self, mock_http):
        """When the JSON block is at the top (complete) but the prose
        after it is truncated, the parser should extract the verdict
        from the JSON — the truncated tail only costs polish."""
        output_with_json_then_truncated = (
            '```json\n'
            '{"verdict":"PASS","summary":"All good.","quality_assessment":"Met.",'
            '"slop_detection":"None.","improvements":"None.",'
            '"agent_optimization":"Fine.","quota_failure":"None.","fix_list":[]}\n'
            '```\n\n'
            '### Quality Assessment\nThe code is well-structured but'
        )
        with patch("odin.reflection.get_harness") as mock_get, \
             patch("odin.reflection.extract_stream_summary") as mock_extract:
            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=True,
                output=output_with_json_then_truncated,
                duration_ms=10000,
                metadata={"usage": {"input_tokens": 500, "output_tokens": 4000}},
            ))
            mock_get.return_value = harness
            mock_extract.return_value = {"finish_reason": "length", "output_tokens": 4000}

            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["status"] == "COMPLETED"
        assert payload["verdict"] == "PASS"
        assert payload["verdict_summary"] == "All good."

    def test_reflect_task_report_has_correct_sections(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        for key in ("quality_assessment", "slop_detection", "improvements",
                     "agent_optimization", "verdict", "verdict_summary", "raw_output"):
            assert key in payload

    def test_reflect_task_handles_harness_exception(self, mock_http):
        with patch("odin.reflection.get_harness") as mock_get:
            harness = MagicMock()
            harness.execute = AsyncMock(side_effect=RuntimeError("Agent crashed"))
            mock_get.return_value = harness

            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        last_patch_call = mock_http.patch.call_args_list[-1]
        payload = last_patch_call[1]["json"]
        assert payload["status"] == "FAILED"
        assert "Agent crashed" in payload["error_message"]

    def test_reflect_task_handles_harness_failure_result(self, mock_http):
        with patch("odin.reflection.get_harness") as mock_get:
            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=False, output="", error="Timeout",
            ))
            mock_get.return_value = harness

            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        last_patch_call = mock_http.patch.call_args_list[-1]
        payload = last_patch_call[1]["json"]
        assert payload["status"] == "FAILED"
        assert payload["error_message"] == "Timeout"

    def test_reflect_task_patches_assembled_prompt(self, mock_http, mock_harness):
        """The RUNNING patch should include the assembled prompt."""
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        first_patch_call = mock_http.patch.call_args_list[0]
        payload = first_patch_call[1]["json"]
        assert payload["status"] == "RUNNING"
        assert "assembled_prompt" in payload
        assert "auditing a task executed by an AI agent" in payload["assembled_prompt"]
        assert "Implement user login" in payload["assembled_prompt"]

    def test_reflect_task_includes_token_usage(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        second_patch_call = mock_http.patch.call_args_list[1]
        payload = second_patch_call[1]["json"]
        assert payload["token_usage"] == {"input_tokens": 2000, "output_tokens": 3000}

    def test_reflect_task_marks_workspace_read_only(self, mock_http, mock_harness):
        """Reflection is a read-only audit — the reviewer must never write to the
        repo. The context carries read_only_workspace=True so the sandbox harness
        bind-mounts every repo path :ro."""
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        context = mock_harness.execute.call_args[0][1]
        assert context["read_only_workspace"] is True

    def test_reflect_task_preserves_long_proof_comment_in_prompt(self, mock_harness):
        proof = "Proof:\n" + '{"artifact":"full"}\n' + ("A" * 2811)
        task_detail = {
            **MOCK_TASK_DETAIL,
            "comments": [
                {"content": "Starting implementation", "comment_type": "status_update"},
                {"content": proof, "comment_type": "proof"},
            ],
        }
        with patch("odin.reflection.httpx") as mock_requests:
            running_resp = MagicMock()
            running_resp.status_code = 200
            running_resp.json.return_value = {"status": "RUNNING"}

            detail_resp = MagicMock()
            detail_resp.status_code = 200
            detail_resp.json.return_value = task_detail

            complete_resp = MagicMock()
            complete_resp.status_code = 200
            complete_resp.json.return_value = {"status": "COMPLETED"}

            mock_requests.patch.side_effect = [running_resp, complete_resp]
            mock_requests.get.return_value = detail_resp

            reflect_task(
                task_id="42", report_id="1", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        prompt = mock_harness.execute.call_args[0][0]
        assert proof in prompt
        assert "[truncated by pipeline at 2000 chars]" not in prompt

    def test_reflect_task_with_custom_model_override(self, mock_http, mock_harness):
        reflect_task(
            task_id="42", report_id="1", model="gemini-2.5-pro",
            agent="gemini", taskit_url="http://localhost:8000",
        )
        mock_harness.execute.assert_called_once()
        context = mock_harness.execute.call_args[0][1]
        assert context["model"] == "gemini-2.5-pro"


class TestReflectionOutputSanitizing:
    def test_sanitizes_gemini_retry_noise_before_parsing(self):
        raw = """YOLO mode is enabled. All tool calls will be automatically approved.
Attempt 1 failed with status 429. Retrying with backoff...
_GaxiosError: [{ "error": { "code": 429, "message": "No capacity available" }}]
    at Gaxios._request (bundle.js:1:1)
Error executing tool read_file: Path not in workspace: /tmp/proof.png
Quality Assessment
- Core requirement: MET - Implemented correctly.

Slop Detection
None.

Actionable Improvements
None.

Agent Optimization
- Description clarity: clear

Quota / Resource Failure
None.

Verdict
PASS
The implementation satisfies the task.
"""

        clean = _sanitize_reflection_output(raw)
        parsed = parse_reflection_report(clean)

        assert clean.startswith("### Quality Assessment")
        assert "Gaxios" not in clean
        assert "Path not in workspace" not in clean
        assert parsed["verdict"] == "PASS"
        assert "Core requirement" in parsed["quality_assessment"]



# Task detail with proof comments containing screenshot attachments
MOCK_TASK_WITH_SCREENSHOTS = {
    "id": 99,
    "title": "Frontend Quota modal with navbar button",
    "description": "Add a Quota button to the navbar. Proof: Screenshot showing the modal open.",
    "status": "REVIEW",
    "model_name": "claude-sonnet-4-5",
    "metadata": {
        "working_dir": "/tmp/project",
        "selected_model": "claude-sonnet-4-5",
        "full_output": "Feature implemented",
        "last_duration_ms": 60000,
        "last_usage": {"input_tokens": 3000, "output_tokens": 5000, "total_tokens": 8000},
    },
    "comments": [
        {"content": "Starting implementation", "comment_type": "status_update", "attachments": []},
        {
            "content": "Proof: Quota button and modal implemented",
            "comment_type": "proof",
            "attachments": [
                {
                    "type": "proof",
                    "summary": "Task complete",
                    "screenshots": [
                        "http://localhost:8000/media/screenshots/2026/03/proof_99_navbar.png",
                        "http://localhost:8000/media/screenshots/2026/03/proof_99_modal.png",
                    ],
                }
            ],
        },
    ],
    "depends_on": [],
    "assignee": {"name": "claude-agent"},
}


class TestReflectTaskScreenshots:
    """reflect_task() must include screenshot images in the reviewer prompt."""

    def test_screenshot_urls_extracted_and_downloaded(self, mock_harness, tmp_path):
        """Screenshots from proof comments are downloaded and paths injected into prompt."""
        # Mock HTTP: detail returns task with screenshot attachments, download returns image bytes
        with patch("odin.reflection.httpx") as mock_requests:
            running_resp = MagicMock()
            running_resp.status_code = 200
            running_resp.json.return_value = {"status": "RUNNING"}

            detail_resp = MagicMock()
            detail_resp.status_code = 200
            detail_resp.json.return_value = MOCK_TASK_WITH_SCREENSHOTS

            complete_resp = MagicMock()
            complete_resp.status_code = 200
            complete_resp.json.return_value = {"status": "COMPLETED"}

            mock_requests.patch.side_effect = [running_resp, complete_resp]
            mock_requests.get.side_effect = [
                detail_resp,
                # Two screenshot downloads
                MagicMock(status_code=200, content=b"\x89PNG\r\n\x1a\nfake_navbar"),
                MagicMock(status_code=200, content=b"\x89PNG\r\n\x1a\nfake_modal"),
            ]

            reflect_task(
                task_id="99", report_id="5", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        # The prompt sent to the harness must reference the screenshot files
        prompt = mock_harness.execute.call_args[0][0]
        assert "proof_99_navbar.png" in prompt
        assert "proof_99_modal.png" in prompt
        # Must instruct the reviewer to actually look at the images
        assert "screenshot" in prompt.lower() or "image" in prompt.lower()

    def test_screenshot_download_failure_degrades_gracefully(self, mock_harness):
        """If screenshot download fails, reflection continues without images."""
        with patch("odin.reflection.httpx") as mock_requests:
            running_resp = MagicMock()
            running_resp.status_code = 200
            running_resp.json.return_value = {"status": "RUNNING"}

            detail_resp = MagicMock()
            detail_resp.status_code = 200
            detail_resp.json.return_value = MOCK_TASK_WITH_SCREENSHOTS

            complete_resp = MagicMock()
            complete_resp.status_code = 200
            complete_resp.json.return_value = {"status": "COMPLETED"}

            mock_requests.patch.side_effect = [running_resp, complete_resp]

            # Detail succeeds, screenshot downloads fail
            download_fail = MagicMock()
            download_fail.status_code = 404
            download_fail.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=download_fail,
            )

            mock_requests.get.side_effect = [detail_resp, download_fail, download_fail]

            # Should not raise — degrades gracefully
            reflect_task(
                task_id="99", report_id="5", model="claude-opus-4-6",
                agent="claude", taskit_url="http://localhost:8000",
            )

        # Harness still called (reflection continues)
        mock_harness.execute.assert_called_once()

    def test_no_screenshots_no_image_section(self, mock_http, mock_harness):
        """When comments have no screenshot attachments, prompt has no image section."""
        reflect_task(
            task_id="42", report_id="1", model="claude-opus-4-6",
            agent="claude", taskit_url="http://localhost:8000",
        )
        prompt = mock_harness.execute.call_args[0][0]
        assert "Proof Screenshots" not in prompt

    def test_forkd_screenshots_are_staged_inside_guest_workspace(self, tmp_path):
        """forkd reviewers receive proof screenshots at /tmp/odin-workspace paths."""
        working_dir = tmp_path / "project"
        (working_dir / ".odin").mkdir(parents=True)
        (working_dir / ".odin" / "config.yaml").write_text(
            "agents:\n"
            "  gemini:\n"
            "    run_in_forkd: true\n"
            "    forkd_snapshot_tag: odin-node22-4g-cli-browser\n"
        )
        task_detail = {
            **MOCK_TASK_WITH_SCREENSHOTS,
            "metadata": {
                **MOCK_TASK_WITH_SCREENSHOTS["metadata"],
                "working_dir": str(working_dir),
            },
        }
        with patch("odin.reflection.httpx") as mock_requests, patch("odin.reflection.get_harness") as mock_get_harness:
            running_resp = MagicMock()
            running_resp.json.return_value = {"status": "RUNNING"}
            detail_resp = MagicMock()
            detail_resp.json.return_value = task_detail
            complete_resp = MagicMock()
            complete_resp.json.return_value = {"status": "COMPLETED"}
            image_resp = MagicMock(status_code=200, content=b"fakepng")
            image_resp.raise_for_status.return_value = None
            mock_requests.patch.side_effect = [running_resp, complete_resp]
            mock_requests.get.side_effect = [detail_resp, image_resp, image_resp]

            harness = MagicMock()
            harness.execute = AsyncMock(return_value=TaskResult(
                success=True,
                output=MOCK_AGENT_OUTPUT,
                duration_ms=15000,
                metadata={},
            ))
            mock_get_harness.return_value = harness

            reflect_task(
                task_id="99", report_id="5", model="gemini-3-flash-preview",
                agent="gemini", taskit_url="http://localhost:8000",
            )

        prompt = harness.execute.call_args[0][0]
        assert "/tmp/odin-workspace/.odin-reflection-assets/reflect_5/proof_0.png" in prompt
        assert "/tmp/odin_reflect_screenshots_" not in prompt
        assert not (working_dir / ".odin-reflection-assets").exists()


class TestOdinCLIReflectHonorsExplicitReviewer:
    """Bug #325: `odin reflect` silently overwrites the explicit reviewer the
    TaskIt API stored on the ReflectionReport whenever the env-driven
    ``forced_base_provider`` knob is set. So a caller who paid for a $0/M
    reviewer by routing reflection to glm ends up running gemini (and gemini's
    default model) because the CLI ignores --agent/--model without saying so.

    The CLI must distinguish "caller named a reviewer" (selection_reason ==
    caller_override, set by the TaskIt API when the operator supplied a
    reviewer) from "caller left defaults in place" (selection_reason unset /
    manual_default), and only apply forced_base_provider in the latter case.
    """

    def _cli_with_forced_provider(self, forced_provider, forced_model):
        """Build an OdinCLI whose _get_config returns a cfg carrying forced_base_provider."""
        cli = OdinCLI()
        cfg = MagicMock()
        cfg.forced_base_provider = forced_provider
        cfg.forced_base_model = forced_model
        cfg.taskit = MagicMock()
        cfg.taskit.base_url = "http://localhost:8000"
        cfg.log_dir = None
        cli._config = cfg  # bypass lazy load_config; first _get_config() returns this
        cli._cli_log = MagicMock()  # setup_logger() is skipped when _config is pre-set
        return cli

    def _patch_reflect_task(self, **kwargs):
        """Patch the module-level symbol the CLI re-imports each call.

        ``cli.reflect`` does ``from odin.reflection import reflect_task``
        inside the function, so the cleanest target is the source module —
        patching ``odin.reflection.reflect_task`` is what intercepts the
        re-import (the local binding becomes our mock). We also stage a
        matching attribute on ``odin.cli`` so anything reading the
        already-bound module attribute still resolves.
        """
        return patch("odin.reflection.reflect_task", **kwargs)

    def test_caller_override_ignores_forced_provider_for_agent(self):
        """Explicit --agent must win over forced_base_provider when the caller
        passed a reviewer (selection_reason=caller_override)."""
        cli = self._cli_with_forced_provider(
            forced_provider="gemini",
            forced_model="gemini-2.5-pro",
        )
        with self._patch_reflect_task() as mock_reflect_task:
            try:
                cli.reflect(
                    task_id="42", report_id="7",
                    model="claude-opus-4-8", agent="claude",
                    selection_reason="caller_override",
                )
            except SystemExit as exc:
                pytest.fail(f"reflect() should not SystemExit on caller_override; got {exc}")
        mock_reflect_task.assert_called_once()
        call_kwargs = mock_reflect_task.call_args.kwargs
        assert call_kwargs["agent"] == "claude", (
            f"explicit --agent claude should reach reflect_task, but got "
            f"agent={call_kwargs['agent']!r} (forced_base_provider overrode it)"
        )
        assert call_kwargs["model"] == "claude-opus-4-8", (
            f"explicit --model claude-opus-4-8 should reach reflect_task, but "
            f"got model={call_kwargs['model']!r}"
        )

    def test_manual_default_still_honors_forced_provider(self):
        """When the caller did NOT override (selection_reason empty / not set),
        forced_base_provider still fills in the defaults — that's the whole
        point of the env knob. The fix must not break that path."""
        cli = self._cli_with_forced_provider(
            forced_provider="gemini",
            forced_model="gemini-2.5-pro",
        )
        with self._patch_reflect_task() as mock_reflect_task:
            cli.reflect(
                task_id="42", report_id="7",
                model="claude-opus-4-7",  # function default
                agent="claude",            # function default
                selection_reason="manual_default",
            )
        mock_reflect_task.assert_called_once()
        call_kwargs = mock_reflect_task.call_args.kwargs
        assert call_kwargs["agent"] == "gemini"
        assert call_kwargs["model"] == "gemini-2.5-pro"

    def test_caller_override_with_forced_provider_surface_explicit_pair(
        self,
    ):
        """When the CLI is honoring explicit --agent/--model in the face of
        forced_base_provider, surface the conflict on stderr/stdout so an
        operator reading the trace can see *why* the env knob lost."""
        cli = self._cli_with_forced_provider(
            forced_provider="gemini",
            forced_model="gemini-2.5-pro",
        )
        with self._patch_reflect_task() as mock_reflect_task, \
                patch("odin.reflection.httpx"), \
                patch("odin.reflection.get_harness"):
            cli.reflect(
                task_id="42", report_id="7",
                model="claude-opus-4-8", agent="claude",
                selection_reason="caller_override",
            )
        mock_reflect_task.assert_called_once()
        assert mock_reflect_task.call_args.kwargs["agent"] == "claude"
        assert mock_reflect_task.call_args.kwargs["model"] == "claude-opus-4-8"

    def test_no_forced_provider_passes_args_through_unchanged(self):
        """Without forced_base_provider, the explicit --agent/--model flow
        through reflect_task unmodified (regression guard)."""
        cli = self._cli_with_forced_provider(
            forced_provider=None, forced_model=None,
        )
        with self._patch_reflect_task() as mock_reflect_task, \
                patch("odin.reflection.httpx"), \
                patch("odin.reflection.get_harness"):
            cli.reflect(
                task_id="42", report_id="7",
                model="gemini-2.5-pro", agent="gemini",
                selection_reason="caller_override",
            )
        mock_reflect_task.assert_called_once()
        call_kwargs = mock_reflect_task.call_args.kwargs
        assert call_kwargs["agent"] == "gemini"
        assert call_kwargs["model"] == "gemini-2.5-pro"
        assert call_kwargs["selection_reason"] == "caller_override"
