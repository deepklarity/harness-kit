"""End-to-end tests for MCP harness integration.

Verifies the full pipeline:
  1. Orchestrator generates MCP config with correct env vars
  2. taskit-mcp starts as a subprocess and responds to MCP protocol
  3. Agent CLI command includes --mcp-config flag
  4. Comments posted via MCP appear in TaskIt

Requires:
  - TaskIt backend running at TASKIT_URL (default: http://localhost:8000)
  - taskit-mcp on PATH (pip install -e ".[mcp]")
  - If auth enabled: ODIN_ADMIN_USER, ODIN_ADMIN_PASSWORD env vars

Run from odin/:
  python -m pytest tests/integration/test_mcp_harness_e2e.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from dotenv import load_dotenv

# Load .env from temp_test_dir
_env_file = Path(__file__).resolve().parents[2] / "temp_test_dir" / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

TASKIT_URL = os.environ.get("TASKIT_URL", "http://localhost:8000")


def _health_ok() -> bool:
    try:
        return httpx.get(f"{TASKIT_URL}/health/", timeout=5).status_code == 200
    except httpx.ConnectError:
        return False


def _mcp_on_path() -> bool:
    """Check if taskit-mcp is available."""
    from shutil import which
    return which("taskit-mcp") is not None


pytestmark = [
    pytest.mark.skipif(not _health_ok(), reason=f"TaskIt not reachable at {TASKIT_URL}"),
    pytest.mark.skipif(not _mcp_on_path(), reason="taskit-mcp not on PATH"),
]


def _get_auth():
    email = os.environ.get("ODIN_ADMIN_USER", "")
    password = os.environ.get("ODIN_ADMIN_PASSWORD", "")
    if not email or not password:
        return None
    from odin.backends.taskit import TaskItAuth
    return TaskItAuth(f"{TASKIT_URL}/auth/login/", email, password)


def _get_token(auth) -> str:
    if auth is None:
        return ""
    return auth.get_token()


def _api(path, auth=None, **kwargs):
    return httpx.request(url=f"{TASKIT_URL}{path}", auth=auth, timeout=10, **kwargs)


@pytest.fixture(scope="module")
def auth():
    return _get_auth()


@pytest.fixture(scope="module")
def board(auth):
    resp = _api("/boards/", auth=auth, method="POST", json={"name": "MCP E2E Test"})
    assert resp.status_code == 201
    data = resp.json()
    yield data
    _api(f"/boards/{data['id']}/clear/", auth=auth, method="POST")
    _api(f"/boards/{data['id']}/", auth=auth, method="DELETE")


@pytest.fixture
def task(board, auth):
    resp = _api(
        "/tasks/", auth=auth, method="POST",
        json={
            "board_id": board["id"],
            "title": "MCP E2E test task",
            "created_by": "e2e@test.com",
        },
    )
    assert resp.status_code == 201
    return resp.json()


# ── Test 1: Config generation ──────────────────────────────────


class TestMcpConfigGeneration:
    """Verify the orchestrator generates a correct MCP config file."""

    def test_config_round_trip(self, task, auth, tmp_path):
        """Generate config, parse it, verify env vars match live state."""
        from odin.orchestrator import Orchestrator
        from odin.models import AgentConfig, OdinConfig, TaskItConfig

        token = _get_token(auth)
        task_id = str(task["id"])

        # Build an Orchestrator with real taskit config
        config = OdinConfig(
            agents={"claude": AgentConfig(cli_command="claude")},
            taskit=TaskItConfig(base_url=TASKIT_URL),
            log_dir=str(tmp_path / "logs"),
        )
        orch = Orchestrator.__new__(Orchestrator)
        orch.config = config
        orch._log = MagicMock()

        # Mock the backend with a real auth token
        mock_auth = MagicMock()
        mock_auth.get_token.return_value = token
        mock_client = MagicMock()
        mock_client.auth = mock_auth
        mock_backend = MagicMock()
        mock_backend._client = mock_client
        orch._backend = mock_backend

        # Generate config
        config_path = orch._generate_mcp_config(task_id, "claude", tmp_path / "logs")

        assert config_path is not None
        data = json.loads(Path(config_path).read_text())

        # Verify structure
        server = data["mcpServers"]["taskit"]
        assert server["command"] == "taskit-mcp"
        assert server["env"]["TASKIT_URL"] == TASKIT_URL
        assert server["env"]["TASKIT_TASK_ID"] == task_id
        assert server["env"]["TASKIT_AUTHOR_EMAIL"] == "claude@odin.agent"
        # Token should be non-empty if auth is configured
        if auth:
            assert server["env"]["TASKIT_AUTH_TOKEN"] != ""


# ── Test 2: MCP server subprocess ──────────────────────────────


class TestMcpServerSubprocess:
    """Verify taskit-mcp starts as a subprocess and responds to MCP init."""

    def test_server_starts_and_responds(self, task, auth):
        """Start taskit-mcp, send initialize, get a valid response."""
        token = _get_token(auth)
        env = {
            **os.environ,
            "TASKIT_URL": TASKIT_URL,
            "TASKIT_AUTH_TOKEN": token,
            "TASKIT_TASK_ID": str(task["id"]),
            "TASKIT_AUTHOR_EMAIL": "e2e@test.com",
        }

        proc = subprocess.Popen(
            ["taskit-mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        try:
            # Send MCP initialize request (JSON-RPC)
            init_msg = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            })
            proc.stdin.write(init_msg.encode() + b"\n")
            proc.stdin.flush()

            # Read response (with timeout)
            import select
            ready, _, _ = select.select([proc.stdout], [], [], 10)
            assert ready, "taskit-mcp did not respond within 10s"

            line = proc.stdout.readline()
            assert line, "Empty response from taskit-mcp"

            resp = json.loads(line)
            assert resp.get("jsonrpc") == "2.0"
            assert resp.get("id") == 1
            assert "result" in resp
            # Server should report its capabilities
            result = resp["result"]
            assert "serverInfo" in result or "capabilities" in result
        finally:
            proc.terminate()
            proc.wait(timeout=5)


# ── Test 3: Harness command includes --mcp-config ──────────────


class TestHarnessCommandIntegration:
    """Verify harness build_execute_command includes MCP flag."""

    def test_claude_command_has_mcp_config(self, task, auth, tmp_path):
        """Build a Claude command with MCP config and verify the flag."""
        from odin.harnesses.claude import ClaudeHarness
        from odin.models import AgentConfig

        harness = ClaudeHarness(AgentConfig(cli_command="claude"))
        config_file = str(tmp_path / "mcp_test.json")
        Path(config_file).write_text("{}")

        context = {"mcp_config": config_file}
        cmd = harness.build_execute_command("test prompt", context)

        assert "--mcp-config" in cmd
        idx = cmd.index("--mcp-config")
        assert cmd[idx + 1] == config_file

    def test_gemini_command_ignores_mcp_config(self, task, auth, tmp_path):
        """Gemini has no --mcp-config flag — it auto-discovers from .gemini/settings.json."""
        from odin.harnesses.gemini import GeminiHarness
        from odin.models import AgentConfig

        harness = GeminiHarness(AgentConfig(cli_command="gemini"))
        config_file = str(tmp_path / "mcp_test.json")
        Path(config_file).write_text("{}")

        context = {"mcp_config": config_file}
        cmd = harness.build_execute_command("test prompt", context)

        assert "--mcp-config" not in cmd


# ── Test 4: Full round-trip via MCP ──────────────────────────


class TestMcpRoundTrip:
    """Post a comment via MCP tool functions and verify in TaskIt."""

    def test_comment_via_mcp_appears_in_taskit(self, task, auth):
        """Call the MCP tool function directly, verify via HTTP API."""
        from odin.mcps.taskit_mcp.server import taskit_add_comment

        token = _get_token(auth)
        task_id = str(task["id"])

        # Set env for the MCP tool
        old_env = {}
        env_vars = {
            "TASKIT_URL": TASKIT_URL,
            "TASKIT_AUTH_TOKEN": token,
            "TASKIT_AUTHOR_EMAIL": "e2e-harness@odin.agent",
            "TASKIT_AUTHOR_LABEL": "e2e",
        }
        for k, v in env_vars.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        try:
            result = taskit_add_comment(
                task_id=task_id,
                content="E2E harness integration test comment",
            )
            assert "comment_id" in result

            # Verify via TaskIt API
            resp = _api(f"/tasks/{task['id']}/comments/", auth=auth, method="GET")
            comments = resp.json()["results"]
            matching = [
                c for c in comments
                if c["content"] == "E2E harness integration test comment"
            ]
            assert len(matching) == 1
            assert matching[0]["author_email"] == "e2e-harness@odin.agent"
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# ── Test 5: odin mcp_config CLI command ──────────────────────


class TestMcpConfigCLI:
    """Verify the odin mcp_config CLI command generates valid config."""

    def test_generates_mcp_json(self, tmp_path):
        """Run odin mcp_config and verify the output file."""
        output = tmp_path / ".mcp.json"
        temp_test_dir = Path(__file__).resolve().parents[2] / "temp_test_dir"

        if not temp_test_dir.exists():
            pytest.skip("temp_test_dir not found")

        from shutil import which
        odin_bin = which("odin")
        if not odin_bin:
            pytest.skip("odin not on PATH")

        result = subprocess.run(
            [odin_bin, "mcp_config", "--output", str(output)],
            capture_output=True,
            text=True,
            cwd=str(temp_test_dir),
            timeout=30,
        )

        assert result.returncode == 0, f"odin mcp_config failed: {result.stderr}"
        assert output.exists()
        data = json.loads(output.read_text())
        assert "mcpServers" in data
        assert "taskit" in data["mcpServers"]
        assert data["mcpServers"]["taskit"]["command"] == "taskit-mcp"
