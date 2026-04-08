"""Interactive planning session via tmux or direct subprocess.

Launches the agent CLI interactively so the user can chat naturally
about the plan.  The agent receives the unified plan prompt from
``_build_plan_prompt()`` and writes its plan JSON to the ``plan_path``
specified in the prompt.

Two modes:
- **tmux** (default): wraps the agent in a tmux session for
  detach/reattach from a terminal.
- **direct** (``--direct``): runs the agent as a plain subprocess,
  inheriting the current process's stdin/stdout/stderr.  Designed for
  web UI / PTY contexts where the caller already provides the terminal
  and session persistence — tmux would add an unnecessary layer
  (status bar, sizing mismatches).

Transcript is for debugging only — plan data is NOT extracted from it.
"""

import shlex
import subprocess
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


KICKOFF_SUFFIX = """

---
Please analyze the spec above and help me decompose it into sub-tasks. \
When we're satisfied with the plan, write the final JSON to the file path \
specified above."""


class InteractivePlanSession:
    """Launch agent CLI interactively in tmux or as a direct subprocess.

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
        direct: bool = False,
    ):
        self.harness = harness
        self.system_prompt = system_prompt
        self.context = context or {}
        self.log_dir = log_dir or ".odin/logs"
        self.direct = direct

    def run(self) -> Optional[str]:
        """Run interactive session.  Blocks until user exits.

        Plan data is on disk at plan_path (embedded in the system prompt).
        Returns the path to the transcript log file (for trace capture).
        """
        if self.direct:
            return self._run_direct()

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

        # Write system prompt to file (harness reads it via CLI flag)
        prompt_file = output_dir / f"system_prompt_{session_id}.txt"

        has_system_prompt = self.harness.supports_system_prompt_flag
        if has_system_prompt:
            # Claude: system prompt goes via --system-prompt flag,
            # initial message is a separate kickoff pasted via tmux.
            prompt_file.write_text(self.system_prompt)
            initial_msg_file = output_dir / f"initial_msg_{session_id}.txt"
            initial_msg_file.write_text(INITIAL_MESSAGE_TEMPLATE)
        else:
            # All other agents: no --system-prompt flag. The planning
            # prompt + kickoff are combined into the CLI flag
            # (--prompt-interactive, --prompt, or positional arg).
            # No tmux paste needed.
            prompt_file.write_text(self.system_prompt + KICKOFF_SUFFIX)
            initial_msg_file = None

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
            initial_message_file=str(initial_msg_file) if initial_msg_file else None,
        )

        console.print(
            f"\n[dim]Interactive session ended (exit code {exit_code}).[/dim]"
        )

        # Clean up temp files
        cleanup = [prompt_file]
        if initial_msg_file:
            cleanup.append(initial_msg_file)
        for f in cleanup:
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

        # Prefer tmux scrollback (properly rendered text with correct spacing)
        # over ANSI-stripped script(1) output (which collapses TUI layouts).
        scrollback_path = Path(output_file + ".scrollback")
        if scrollback_path.exists():
            content = scrollback_path.read_text(errors="replace").strip()
            if len(content) > 20:  # non-trivial content
                return str(scrollback_path)

        # Fallback to ANSI-stripped version
        if transcript_path.exists():
            return str(clean_path)
        return None


    def _run_direct(self) -> Optional[str]:
        """Run agent CLI as a direct subprocess without tmux.

        The agent inherits the current process's stdin/stdout/stderr,
        so whatever terminal is driving this process (e.g. a PTY from
        the web UI) becomes the agent's terminal.  No tmux session is
        created — no status bar, no sizing indirection.

        Returns None (no transcript capture in direct mode).
        """
        session_id = uuid.uuid4().hex[:12]
        output_dir = Path(self.log_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write system prompt to file (same as tmux path)
        prompt_file = output_dir / f"system_prompt_{session_id}.txt"

        if self.harness.supports_system_prompt_flag:
            prompt_file.write_text(self.system_prompt)
        else:
            prompt_file.write_text(self.system_prompt + KICKOFF_SUFFIX)

        # Build CLI command
        cmd = self.harness.build_interactive_command(
            str(prompt_file), self.context
        )
        if cmd is None:
            raise RuntimeError(
                f"Agent '{self.harness.name}' does not support interactive mode. "
                "Use --auto for one-shot planning."
            )

        working_dir = self.context.get("working_dir", str(Path.cwd()))

        # Expand __FILE__: markers via shell $(cat ...) — avoids hitting
        # the OS exec argument length limit for large system prompts.
        cmd_parts = []
        for arg in cmd:
            if arg.startswith("__FILE__:"):
                file_path = arg[len("__FILE__:"):]
                cmd_parts.append('"$(cat ' + shlex.quote(file_path) + ')"')
            else:
                cmd_parts.append(shlex.quote(arg))
        cmd_str = " ".join(cmd_parts)

        # Run directly — stdin/stdout/stderr inherited from the caller.
        # KeyboardInterrupt (Ctrl+C from the PTY) is caught so that the
        # orchestrator can still check for the plan file and create tasks.
        try:
            subprocess.run(["bash", "-c", cmd_str], cwd=working_dir)
        except KeyboardInterrupt:
            pass

        # Clean up temp files
        try:
            prompt_file.unlink()
        except OSError:
            pass

        return None


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    from odin.logging.logger_utils import ANSI_ESCAPE
    return ANSI_ESCAPE.sub("", text)
