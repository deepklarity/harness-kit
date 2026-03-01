"""Interactive planning session via tmux.

Launches the agent CLI interactively in a tmux session so the user can
chat naturally about the plan.  The agent receives the unified plan
prompt from ``_build_plan_prompt()`` and writes its plan JSON to the
``plan_path`` specified in the prompt.

Transcript is for debugging only — plan data is NOT extracted from it.
"""

import uuid
from pathlib import Path
from typing import Optional

from rich.console import Console

from odin.harnesses.base import BaseHarness
from odin.tmux import is_available as tmux_available, launch_and_attach

console = Console()


INITIAL_MESSAGE_TEMPLATE = """\
I need help planning this task. The full specification and instructions \
are in the system prompt.

Please analyze the spec and help me decompose it into sub-tasks. When \
we're satisfied with the plan, write the final JSON to the file path \
specified in the system prompt."""


class InteractivePlanSession:
    """Launch agent CLI interactively in tmux.

    The system prompt is the unified plan prompt from
    ``Orchestrator._build_plan_prompt()`` — the same prompt used by
    auto and quiet modes.  The agent writes its plan JSON to the
    ``plan_path`` embedded in the prompt.

    After the session ends, plan data is on disk at ``plan_path``
    (or absent — the caller handles the clean error).
    """

    def __init__(
        self,
        harness: BaseHarness,
        system_prompt: str,
        context: Optional[dict] = None,
        log_dir: Optional[str] = None,
    ):
        self.harness = harness
        self.system_prompt = system_prompt
        self.context = context or {}
        self.log_dir = log_dir or ".odin/logs"

    def run(self) -> Optional[str]:
        """Run interactive session.  Blocks until user exits tmux.

        Plan data is on disk at plan_path (embedded in the system prompt).
        Returns the path to the transcript log file (for trace capture).
        """
        if not tmux_available():
            raise RuntimeError(
                "tmux is required for interactive planning. "
                "Install it with: brew install tmux (macOS) or apt install tmux (Linux)"
            )

        # Set up output directory
        session_id = uuid.uuid4().hex[:12]
        output_dir = Path(self.log_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = str(output_dir / f"interactive_plan_{session_id}.log")

        # Write system prompt to file (harness reads it via -s flag or equivalent)
        prompt_file = output_dir / f"system_prompt_{session_id}.txt"
        prompt_file.write_text(self.system_prompt)

        # Write initial message to a file (sent as first user message)
        initial_msg_file = output_dir / f"initial_msg_{session_id}.txt"
        initial_msg_file.write_text(INITIAL_MESSAGE_TEMPLATE)

        # Get interactive command from harness
        cmd = self.harness.build_interactive_command(
            str(prompt_file), self.context
        )
        if cmd is None:
            raise RuntimeError(
                f"Agent '{self.harness.name}' does not support interactive mode. "
                "Use --auto for one-shot planning."
            )

        working_dir = self.context.get("working_dir", str(Path.cwd()))

        console.print(
            "\n[bold cyan]Interactive Plan Mode[/bold cyan]\n"
            f"[dim]Launching {self.harness.name} in tmux session...[/dim]\n"
            "[dim]Chat with the agent about your plan. "
            "When you're done, ask it to write the final plan, then exit.[/dim]\n"
        )

        # Launch in tmux and block until user exits
        exit_code = launch_and_attach(
            cmd=cmd,
            working_dir=working_dir,
            session_id=session_id,
            output_file=output_file,
            initial_message_file=str(initial_msg_file),
        )

        console.print(
            f"\n[dim]Interactive session ended (exit code {exit_code}).[/dim]"
        )

        # Clean up temp files
        for f in (prompt_file, initial_msg_file):
            try:
                f.unlink()
            except OSError:
                pass

        # Transcript is for debugging only — plan data is on disk at plan_path
        transcript_path = Path(output_file)
        if transcript_path.exists():
            raw_transcript = transcript_path.read_text(errors="replace")
            clean_path = transcript_path.with_suffix(".clean.log")
            clean_path.write_text(_strip_ansi(raw_transcript))
            return str(clean_path)
        return None


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    from odin.logging.logger_utils import ANSI_ESCAPE
    return ANSI_ESCAPE.sub("", text)
