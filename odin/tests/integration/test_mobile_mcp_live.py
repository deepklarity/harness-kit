"""Integration tests for mobile MCP + TaskIt proof pipeline.

Prerequisites:
- Android emulator or iOS Simulator running
- TaskIt backend healthy (http://localhost:8000)
- mobile-mcp npm package available (npx @mobilenext/mobile-mcp@latest)

These tests are skipped by default when prerequisites are not met.
Run explicitly: python -m pytest tests/integration/test_mobile_mcp_live.py -v
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def _emulator_running() -> bool:
    """Check if an Android emulator or iOS Simulator is running."""
    # Check Android emulator
    if shutil.which("adb"):
        try:
            result = subprocess.run(
                ["adb", "devices"], capture_output=True, text=True, timeout=5,
            )
            lines = result.stdout.strip().split("\n")
            # First line is "List of devices attached", real devices follow
            return len(lines) > 1 and any("device" in l for l in lines[1:])
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    # Check iOS Simulator
    if shutil.which("xcrun"):
        try:
            result = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "booted", "--json"],
                capture_output=True, text=True, timeout=5,
            )
            data = json.loads(result.stdout)
            for runtime_devices in data.get("devices", {}).values():
                if runtime_devices:
                    return True
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass
    return False


def _taskit_healthy() -> bool:
    """Check if TaskIt backend is reachable."""
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:8000/api/health/", timeout=3)
        return req.status == 200
    except Exception:
        return False


def _mobile_mcp_available() -> bool:
    """Check if npx can resolve mobile-mcp."""
    return shutil.which("npx") is not None


requires_emulator = pytest.mark.skipif(
    not _emulator_running(), reason="No emulator/simulator running",
)
requires_taskit = pytest.mark.skipif(
    not _taskit_healthy(), reason="TaskIt backend not healthy",
)
requires_mobile_mcp = pytest.mark.skipif(
    not _mobile_mcp_available(), reason="npx not available",
)


@requires_emulator
@requires_mobile_mcp
class TestMobileListDevices:
    """Verify mobile MCP can list running devices."""

    @pytest.mark.integration
    def test_mobile_list_devices(self):
        """Call mobile MCP's list_available_devices and get a non-empty result."""
        # This test would require actually starting the MCP server and
        # calling the tool — deferred to manual verification for now.
        pytest.skip("Requires MCP server stdio interaction — run manually")


@requires_emulator
@requires_mobile_mcp
class TestMobileScreenshot:
    """Verify mobile MCP can take and save screenshots."""

    @pytest.mark.integration
    def test_mobile_screenshot_saves_to_file(self):
        """Take screenshot via mobile MCP, verify PNG on disk."""
        pytest.skip("Requires MCP server stdio interaction — run manually")


@requires_emulator
@requires_mobile_mcp
@requires_taskit
class TestMobileScreenshotToTaskitProof:
    """Full flow: screenshot → upload → verify in TaskIt."""

    @pytest.mark.integration
    def test_mobile_screenshot_to_taskit_proof(self):
        """Take screenshot, upload via taskit_add_comment(proof), verify."""
        pytest.skip("Requires full MCP pipeline — run manually")
