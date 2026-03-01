"""Helpers for invoking Odin CLI commands from Taskit."""

from __future__ import annotations

import re
import shutil
import subprocess
import os
from typing import Any, Dict, List, Optional

from django.conf import settings

from tasks.utils.logger import logger


DEFAULT_TIMEOUT_SECONDS = 30
ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
SUMMARY_RE = re.compile(r"(\d+)\s+(done|failed|executing|in_progress|review|testing|todo|backlog)")
TOTAL_RE = re.compile(r"\((\d+)\s+total\)")


def _resolve_cwd() -> Optional[str]:
    configured = getattr(settings, "ODIN_WORKING_DIR", None)
    if configured:
        return configured
    return None


def _run_command(
    cmd: List[str], timeout_s: int = DEFAULT_TIMEOUT_SECONDS, env_overrides: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    binary = cmd[0]
    if not shutil.which(binary):
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"Command not found on PATH: {binary}",
        }

    cwd = _resolve_cwd()
    env = None
    if env_overrides:
        env = dict(os.environ)
        env.update(env_overrides)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        return {
            "ok": proc.returncode == 0,
            "command": cmd,
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "error": "" if proc.returncode == 0 else f"Command exited with code {proc.returncode}",
        }
    except subprocess.TimeoutExpired as exc:
        logger.warning("Command timed out: %s", cmd, exc_info=True)
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"Command timed out after {timeout_s}s",
        }
    except Exception as exc:
        logger.exception("Failed to execute command: %s", cmd)
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": str(exc),
        }


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def parse_odin_status_output(raw_stdout: str) -> Dict[str, Any]:
    """Best-effort parser for plain `odin status` rich-table output."""
    clean = _strip_ansi(raw_stdout)
    lines = [ln.rstrip("\n") for ln in clean.splitlines()]
    if any("No tasks found." in ln for ln in lines):
        return {
            "rows": [],
            "summary": {},
            "total": 0,
            "parse_ok": True,
            "parse_warnings": [],
        }

    rows: List[Dict[str, Any]] = []
    parse_warnings: List[str] = []
    for ln in lines:
        if "│" not in ln:
            continue
        parts = [p.strip() for p in ln.split("│")]
        if len(parts) < 11:
            continue
        if parts[1] in ("ID", ""):
            continue
        row_parts = parts[1:10]
        row = {
            "id": row_parts[0],
            "title": row_parts[1],
            "status": row_parts[2],
            "agent": row_parts[3],
            "spec": row_parts[4],
            "model": row_parts[5],
            "deps": row_parts[6],
            "elapsed": row_parts[7],
            "updated_hms": row_parts[8],
        }
        if row["id"]:
            rows.append(row)

    summary: Dict[str, int] = {}
    total: Optional[int] = None
    for ln in lines:
        for match in SUMMARY_RE.finditer(ln):
            summary[match.group(2)] = int(match.group(1))
        total_match = TOTAL_RE.search(ln)
        if total_match:
            total = int(total_match.group(1))

    if total is None:
        total = len(rows)
        if rows or summary:
            parse_warnings.append("Summary total not found in output; using parsed row count.")
    if not rows and summary:
        parse_warnings.append("Parsed summary but no table rows.")

    return {
        "rows": rows,
        "summary": summary,
        "total": total,
        "parse_ok": len(rows) > 0 or len(summary) > 0 or ("No tasks found." in clean),
        "parse_warnings": parse_warnings,
    }


def run_odin_status(spec: Optional[str] = None, agent: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
    """Run plain `odin status` and parse output for UI."""
    odin_cli = getattr(settings, "ODIN_CLI_PATH", "odin")
    cmd = [odin_cli, "status"]
    if spec:
        cmd += ["--spec", str(spec)]
    if agent:
        cmd += ["--agent", str(agent)]
    if status:
        cmd += ["--status", str(status)]

    res = _run_command(
        cmd,
        env_overrides={
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COLUMNS": "200",
        },
    )
    parsed = parse_odin_status_output(res.get("stdout", ""))
    return {
        **res,
        "rows": parsed["rows"],
        "summary": parsed["summary"],
        "total": parsed["total"],
        "parse_ok": parsed["parse_ok"],
        "parse_warnings": parsed["parse_warnings"],
    }


def fetch_odin_status() -> Dict[str, Any]:
    """Backward-compatible wrapper for existing process-monitor endpoint."""
    result = run_odin_status()
    return {
        "ok": result.get("ok", False),
        "tasks": result.get("rows", []),
        "summary": result.get("summary", {}),
        "warning": "; ".join(result.get("parse_warnings", [])),
        "error": result.get("error", ""),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


def stop_with_odin(task_id: int, force: bool = False) -> Dict[str, Any]:
    """Run `odin stop <task_id>`."""
    odin_cli = getattr(settings, "ODIN_CLI_PATH", "odin")
    cmd = [odin_cli, "stop", str(task_id)]
    if force:
        cmd.append("--force")
    res = _run_command(cmd)
    return {
        **res,
        "engine": "odin_cli",
    }
