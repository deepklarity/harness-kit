"""Utility for running CLI commands and capturing output."""

import asyncio
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CLIResult:
    """Result from running a CLI command."""
    returncode: int
    stdout: str
    stderr: str
    command: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def run_cli(
    command: List[str],
    timeout: float = 30,
    env: Optional[Dict[str, str]] = None,
) -> CLIResult:
    """Run a CLI command asynchronously and capture output.

    Args:
        command: Command and arguments as a list
        timeout: Timeout in seconds
        env: Optional environment variables to set
    """
    import os
    full_env = {**os.environ, **(env or {})}

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return CLIResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            command=" ".join(command),
        )
    except asyncio.TimeoutError:
        return CLIResult(
            returncode=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            command=" ".join(command),
        )
    except FileNotFoundError:
        return CLIResult(
            returncode=-1,
            stdout="",
            stderr=f"Command not found: {command[0]}",
            command=" ".join(command),
        )


def find_cli(name: str) -> Optional[str]:
    """Check if a CLI tool is available on PATH."""
    return shutil.which(name)
