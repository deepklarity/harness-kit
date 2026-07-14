"""Tests for the clarification gate stage in odin plan.

Tags: [mock] — no real LLM calls, no real HTTP.

Verifies that:
- The clarification prompt is built correctly with spec + output paths
- The gate runs before decomposition in auto/quiet mode
- The gate_callback receives the clarification data (incl. preview_path)
- Answers from the callback are passed to the decomposition prompt
- gate=False skips the clarification entirely
- Clarification files (JSON + HTML) are written to disk
- Interactive mode runs the clarification gate BEFORE the tmux session
- gate_callback=None produces files but proceeds without waiting
- gate_callback returning None aborts planning cleanly
- The CLI gate surfaces the preview file path for human review
- The CLI gate always requires a human nod, even when there are no questions
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from odin.models import AgentConfig, CostTier, ModelRoute, OdinConfig, TaskResult
from odin.orchestrator import Orchestrator


@pytest.fixture
def config_with_mock(odin_dirs):
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents={
            "mock": AgentConfig(
                cli_command="mock",
                capabilities=["coding", "planning"],
                cost_tier=CostTier.LOW,
                enabled=True,
            ),
        },
        model_routing=[
            ModelRoute(agent="mock", model="mock-model"),
        ],
    )


def _plan_path_for(config, spec_text):
    from odin.specs import generate_spec_id
    from odin.orchestrator import _extract_title
    title = _extract_title(spec_text)
    sid = generate_spec_id(title)
    plans_dir = Path(config.task_storage).parent / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir / f"plan_{sid}.json"


def _clarification_path_for(config, spec_text):
    from odin.specs import generate_spec_id
    from odin.orchestrator import _extract_title
    title = _extract_title(spec_text)
    sid = generate_spec_id(title)
    plans_dir = Path(config.task_storage).parent / "plans"
    return plans_dir / f"clarification_{sid}.json"


def _preview_path_for(config, spec_text):
    from odin.specs import generate_spec_id
    from odin.orchestrator import _extract_title
    title = _extract_title(spec_text)
    sid = generate_spec_id(title)
    plans_dir = Path(config.task_storage).parent / "plans"
    return plans_dir / f"preview_{sid}.html"


SIMPLE_PLAN = [{
    "id": "task_1",
    "title": "Do it",
    "description": "Do the thing",
    "required_capabilities": ["coding"],
    "suggested_agent": "mock",
    "complexity": "low",
    "depends_on": [],
}]


def _mock_result():
    return TaskResult(success=True, output="", duration_ms=100, agent="mock")


class TestClarificationPrompt:
    def test_prompt_includes_spec_and_paths(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        prompt = orch._build_clarification_prompt(
            spec="Build an RSS feed reader",
            clarification_path="/tmp/clarification.json",
            preview_path="/tmp/preview.html",
        )
        assert "RSS feed reader" in prompt
        assert "/tmp/clarification.json" in prompt
        assert "/tmp/preview.html" in prompt

    def test_prompt_asks_for_questions_and_summary(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        prompt = orch._build_clarification_prompt(
            spec="Test spec",
            clarification_path="/tmp/c.json",
            preview_path="/tmp/p.html",
        )
        assert "questions" in prompt.lower()
        assert "summary" in prompt.lower()

    def test_prompt_quick_instruction(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        prompt = orch._build_clarification_prompt(
            spec="Test",
            clarification_path="/tmp/c.json",
            preview_path="/tmp/p.html",
            quick=True,
        )
        assert "Do NOT explore" in prompt


class TestGateAutoMode:
    def test_gate_runs_before_decomposition(self, odin_dirs, config_with_mock):
        """Clarification dispatch runs before decomposition in auto mode."""
        orch = Orchestrator(config=config_with_mock)
        call_order = []

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = clar_path.with_suffix(".html")

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                call_order.append("clarification")
                clar_path.write_text(json.dumps({
                    "questions": ["What format?"],
                    "summary": "A tool.",
                }))
                preview_path.write_text("<html>preview</html>")
            else:
                call_order.append("decomposition")
                plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        def gate_callback(clarification):
            call_order.append("gate_callback")
            assert "questions" in clarification
            return "Use JSON"

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            sid, tasks = asyncio.run(orch.plan(
                spec_text, quick=True, gate=True, gate_callback=gate_callback
            ))

        assert call_order == ["clarification", "gate_callback", "decomposition"]
        assert len(tasks) == 1

    def test_gate_disabled_skips_clarification(self, odin_dirs, config_with_mock):
        """gate=False skips the clarification step entirely."""
        orch = Orchestrator(config=config_with_mock)
        call_order = []

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                call_order.append("clarification")
            else:
                call_order.append("decomposition")
                plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            sid, tasks = asyncio.run(orch.plan(spec_text, quick=True, gate=False))

        assert call_order == ["decomposition"]
        assert not clar_path.exists()

    def test_answers_appended_to_decomposition_prompt(self, odin_dirs, config_with_mock):
        """Answers from gate_callback are passed to the decomposition prompt."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = clar_path.with_suffix(".html")

        captured_prompts = []

        async def mock_decompose(prompt, wd, **kwargs):
            captured_prompts.append(prompt)
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                clar_path.write_text(json.dumps({
                    "questions": ["Color?"],
                    "summary": "A tool.",
                }))
                preview_path.write_text("<html></html>")
            else:
                plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        def gate_callback(clar):
            return "Use blue"

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            asyncio.run(orch.plan(
                spec_text, quick=True, gate=True, gate_callback=gate_callback
            ))

        decomposition_prompt = captured_prompts[1]
        assert "Use blue" in decomposition_prompt

    def test_clarification_files_written(self, odin_dirs, config_with_mock):
        """JSON and HTML files exist after gate runs."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build something"
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = clar_path.with_suffix(".html")

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                Path(out_path).write_text(json.dumps({
                    "questions": ["Q1"],
                    "summary": "Summary.",
                }))
                Path(out_path.replace(".json", ".html")).write_text(
                    "<html><body>Preview</body></html>"
                )
            else:
                Path(out_path).write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            asyncio.run(orch.plan(spec_text, quick=True, gate=True, gate_callback=lambda c: "ok"))

        assert clar_path.exists()
        clar_data = json.loads(clar_path.read_text())
        assert "questions" in clar_data
        assert "summary" in clar_data
        assert preview_path.exists()
        assert "html" in preview_path.read_text().lower()

    def test_gate_callback_none_proceeds_without_answers(self, odin_dirs, config_with_mock):
        """When gate_callback is None, files are produced but no input is waited for."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = clar_path.with_suffix(".html")

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                clar_path.write_text(json.dumps({
                    "questions": ["Q1"],
                    "summary": "S.",
                }))
                preview_path.write_text("<html></html>")
            else:
                plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            sid, tasks = asyncio.run(orch.plan(spec_text, quick=True, gate=True))

        assert len(tasks) == 1
        assert clar_path.exists()

    def test_gate_callback_returns_none_aborts(self, odin_dirs, config_with_mock):
        """If gate_callback returns None, planning aborts cleanly."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = _clarification_path_for(config_with_mock, spec_text).with_suffix(".html")

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                clar_path.write_text(json.dumps({
                    "questions": ["Q1"],
                    "summary": "S.",
                }))
                preview_path.write_text("<html></html>")
            else:
                plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with pytest.raises(RuntimeError, match="aborted"):
                asyncio.run(orch.plan(
                    spec_text, quick=True, gate=True,
                    gate_callback=lambda c: None,
                ))

        assert not plan_path.exists()

    def test_orchestrator_injects_preview_path(self, odin_dirs, config_with_mock):
        """The clarification dict passed to gate_callback includes preview_path."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build something"
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = _preview_path_for(config_with_mock, spec_text)
        plan_path = _plan_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                clar_path.write_text(json.dumps({
                    "questions": ["Q1"],
                    "summary": "S.",
                }))
                preview_path.write_text("<html></html>")
            else:
                plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return _mock_result()

        received = {}

        def gate_callback(clarification):
            received.update(clarification)
            return "ok"

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            asyncio.run(orch.plan(
                spec_text, quick=True, gate=True, gate_callback=gate_callback,
            ))

        assert "preview_path" in received
        assert str(preview_path) == received["preview_path"]
        assert received["preview_exists"] is True


class TestGateInteractiveMode:
    """Interactive mode must run the clarification gate BEFORE the tmux session.

    The gate is a separate one-shot dispatch (same as auto/quiet).  Only after
    the human nods does the interactive planning session open for task
    breakdown.  This guarantees the human reviews questions + preview +
    summary regardless of mode.
    """

    def test_gate_runs_before_interactive_session(self, odin_dirs, config_with_mock):
        """Clarification dispatch + gate_callback fire before the interactive session."""
        orch = Orchestrator(config=config_with_mock)
        call_order = []

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = _preview_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                call_order.append("clarification")
                clar_path.write_text(json.dumps({
                    "questions": ["What format?"],
                    "summary": "A tool.",
                }))
                preview_path.write_text("<html>preview</html>")
            return _mock_result()

        def mock_interactive(prompt, working_dir, **kwargs):
            call_order.append("interactive")
            plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return None

        def gate_callback(clarification):
            call_order.append("gate_callback")
            assert "questions" in clarification
            return "Use JSON"

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_run_interactive_plan", side_effect=mock_interactive):
                sid, tasks = asyncio.run(orch.plan(
                    spec_text, quick=True, mode="interactive",
                    gate=True, gate_callback=gate_callback,
                ))

        assert call_order == ["clarification", "gate_callback", "interactive"]
        assert len(tasks) == 1

    def test_interactive_gate_aborts_on_none(self, odin_dirs, config_with_mock):
        """If gate_callback returns None in interactive mode, no session opens."""
        orch = Orchestrator(config=config_with_mock)
        call_order = []

        spec_text = "Build something"
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = _preview_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                call_order.append("clarification")
                clar_path.write_text(json.dumps({
                    "questions": ["Q1"],
                    "summary": "S.",
                }))
                preview_path.write_text("<html></html>")
            return _mock_result()

        def mock_interactive(prompt, working_dir, **kwargs):
            call_order.append("interactive")
            return None

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_run_interactive_plan", side_effect=mock_interactive):
                with pytest.raises(RuntimeError, match="aborted"):
                    asyncio.run(orch.plan(
                        spec_text, quick=True, mode="interactive",
                        gate=True, gate_callback=lambda c: None,
                    ))

        assert "interactive" not in call_order

    def test_interactive_no_gate_skips_clarification(self, odin_dirs, config_with_mock):
        """gate=False in interactive mode skips clarification entirely."""
        orch = Orchestrator(config=config_with_mock)
        call_order = []

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            call_order.append("decompose")
            return _mock_result()

        def mock_interactive(prompt, working_dir, **kwargs):
            call_order.append("interactive")
            plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return None

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_run_interactive_plan", side_effect=mock_interactive):
                sid, tasks = asyncio.run(orch.plan(
                    spec_text, quick=True, mode="interactive", gate=False,
                ))

        assert call_order == ["interactive"]
        assert not clar_path.exists()
        assert len(tasks) == 1

    def test_interactive_answers_injected_into_session_prompt(self, odin_dirs, config_with_mock):
        """Answers from the gate are visible in the prompt passed to the session."""
        orch = Orchestrator(config=config_with_mock)

        spec_text = "Build something"
        plan_path = _plan_path_for(config_with_mock, spec_text)
        clar_path = _clarification_path_for(config_with_mock, spec_text)
        preview_path = _preview_path_for(config_with_mock, spec_text)

        async def mock_decompose(prompt, wd, **kwargs):
            out_path = kwargs.get("plan_path", "")
            if "clarification_" in Path(out_path).name:
                clar_path.write_text(json.dumps({
                    "questions": ["Color?"],
                    "summary": "A tool.",
                }))
                preview_path.write_text("<html></html>")
            return _mock_result()

        captured_prompt = {}

        def mock_interactive(prompt, working_dir, **kwargs):
            captured_prompt["prompt"] = prompt
            plan_path.write_text(json.dumps(SIMPLE_PLAN))
            return None

        with patch.object(orch, "_decompose", side_effect=mock_decompose):
            with patch.object(orch, "_run_interactive_plan", side_effect=mock_interactive):
                asyncio.run(orch.plan(
                    spec_text, quick=True, mode="interactive",
                    gate=True, gate_callback=lambda c: "Use blue",
                ))

        assert "Use blue" in captured_prompt["prompt"]


class TestGateInteraction:
    """Tests for the CLI's _gate_interaction function — the human-facing
    gate that surfaces the preview, questions, and always requires a nod."""

    def _console(self):
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        return Console(file=buf, force_terminal=False), buf

    def test_surfaces_preview_path(self):
        """The preview file path is shown to the human."""
        from odin.cli import _gate_interaction
        console, buf = self._console()
        clarification = {
            "questions": [],
            "summary": "A tool.",
            "preview_path": "/tmp/preview_xyz.html",
            "preview_exists": True,
        }
        with patch("builtins.input", side_effect=["y"]):
            result = _gate_interaction(clarification, console)
        output = buf.getvalue()
        assert "/tmp/preview_xyz.html" in output
        assert result is not None

    def test_requires_nod_even_with_no_questions(self):
        """Even with no questions, the human must confirm before proceeding."""
        from odin.cli import _gate_interaction
        console, _ = self._console()
        clarification = {
            "questions": [],
            "summary": "Clear spec.",
            "preview_path": "/tmp/preview.html",
            "preview_exists": True,
        }
        with patch("builtins.input", side_effect=["n"]):
            result = _gate_interaction(clarification, console)
        assert result is None

    def test_proceeds_on_yes_with_no_questions(self):
        """No questions + 'y' confirmation proceeds with empty answers."""
        from odin.cli import _gate_interaction
        console, _ = self._console()
        clarification = {
            "questions": [],
            "summary": "Clear.",
            "preview_path": "/tmp/p.html",
            "preview_exists": True,
        }
        with patch("builtins.input", side_effect=["y"]):
            result = _gate_interaction(clarification, console)
        assert result == ""

    def test_requires_nod_after_answering_questions(self):
        """After answering questions, still asks for final confirmation."""
        from odin.cli import _gate_interaction
        console, _ = self._console()
        clarification = {
            "questions": ["What format?"],
            "summary": "A tool.",
            "preview_path": "/tmp/p.html",
            "preview_exists": True,
        }
        with patch("builtins.input", side_effect=["JSON", "n"]):
            result = _gate_interaction(clarification, console)
        assert result is None

    def test_proceeds_with_answers_on_yes(self):
        """Questions answered + 'y' confirmation returns the answers."""
        from odin.cli import _gate_interaction
        console, _ = self._console()
        clarification = {
            "questions": ["What format?"],
            "summary": "A tool.",
            "preview_path": "/tmp/p.html",
            "preview_exists": True,
        }
        with patch("builtins.input", side_effect=["JSON", "y"]):
            result = _gate_interaction(clarification, console)
        assert result is not None
        assert "JSON" in result

    def test_eof_on_confirm_aborts(self):
        """EOFError (non-interactive) on the confirmation prompt aborts."""
        from odin.cli import _gate_interaction
        console, _ = self._console()
        clarification = {
            "questions": [],
            "summary": "Clear.",
            "preview_path": "/tmp/p.html",
            "preview_exists": True,
        }
        with patch("builtins.input", side_effect=EOFError):
            result = _gate_interaction(clarification, console)
        assert result is None

    def test_no_preview_path_does_not_crash(self):
        """Missing preview_path key is handled gracefully."""
        from odin.cli import _gate_interaction
        console, buf = self._console()
        clarification = {
            "questions": [],
            "summary": "Clear.",
        }
        with patch("builtins.input", side_effect=["y"]):
            result = _gate_interaction(clarification, console)
        assert result is not None
