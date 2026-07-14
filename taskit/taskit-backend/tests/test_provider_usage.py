"""Provider usage endpoint tests (task #169).

Origin: the /providers page rendered "package not installed:
harness_usage_status" because the in-repo `harness_usage_status` package (a
src-layout sibling project) was not on the backend's import path, so the
`runtime_provider_usage` view hit its ImportError branch. Worse, the generic
exception branch returned HTTP 500. The fix has two parts:

  1. `config/settings.py` puts `harness_usage_status/src` on sys.path so the
     package imports when the backend runs from the repo (all transitive deps
     are already installed).
  2. The view delegates to `_load_provider_usage()`, a module-level seam that
     mirrors odin's graceful-degradation convention: it never raises, returning
     ``(entries, error)`` so the endpoint always answers HTTP 200 with an
     honest payload.

These tests pin both availability states and the never-500 contract. The
network boundary is `_load_provider_usage`; provider HTTP is mocked.

Non-visual — no browser/screenshots needed.
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from tests.base import APITestCase
from tasks.views import _load_provider_usage

ENDPOINT = "/api/runtime/provider-usage/"


class _FakeProvider:
    """Duck-typed provider feeding real UsageInfo/StatusInfo models."""

    def __init__(self, name):
        self.name = name

    async def get_usage(self):
        from harness_usage_status.models import UsageInfo
        return UsageInfo(
            provider=self.name,
            plan="pro",
            quota_limit=100,
            used=40,
            remaining=60,
            usage_pct=40.0,
            unit="requests",
        )

    async def get_status(self):
        from harness_usage_status.models import ProviderState, StatusInfo
        return StatusInfo(
            provider=self.name,
            state=ProviderState.ONLINE,
            latency_ms=12.5,
            message="ok",
        )


def _available_config():
    """Real load_config + get_all_providers, but with a single fake provider."""
    fake_cfg = SimpleNamespace(get_provider_configs=lambda: {})
    return fake_cfg, {"claude_code": _FakeProvider("claude_code")}


class ProviderUsageEndpointTest(APITestCase):
    # ── available state ────────────────────────────────────────────────

    def test_endpoint_returns_providers_when_available(self):
        """Happy path: package importable + providers resolve → 200 + data."""
        cfg, providers = _available_config()
        with patch("harness_usage_status.config.load_config", return_value=cfg), \
                patch("harness_usage_status.providers.registry.get_all_providers",
                      return_value=providers):
            resp = self.client.get(ENDPOINT)

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["error"])
        self.assertEqual(len(resp.data["providers"]), 1)

        entry = resp.data["providers"][0]
        self.assertEqual(entry["name"], "claude_code")
        self.assertEqual(entry["state"], "online")
        self.assertEqual(entry["usage_pct"], 40.0)
        self.assertEqual(entry["plan"], "pro")
        self.assertEqual(entry["latency_ms"], 12.5)
        self.assertIn("fetched_at", resp.data)

    def test_loader_assembles_per_provider_failure_honestly(self):
        """A provider that raises is recorded as 'unknown', not a 500."""
        boom = _FakeProvider("claude_code")

        async def fail_usage(self):
            raise RuntimeError("upstream timeout")

        boom.get_usage = lambda: fail_usage(boom)
        providers = {"claude_code": boom}
        cfg = SimpleNamespace(get_provider_configs=lambda: {})
        with patch("harness_usage_status.config.load_config", return_value=cfg), \
                patch("harness_usage_status.providers.registry.get_all_providers",
                      return_value=providers):
            entries, error = _load_provider_usage()

        self.assertIsNone(error)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["state"], "online")  # status still resolved
        self.assertEqual(entries[0]["unit"], "unknown")  # usage failed → unknown
        self.assertIsNone(entries[0]["usage_pct"])

    # ── degraded state ─────────────────────────────────────────────────

    def test_endpoint_honest_degraded_when_package_missing(self):
        """Package absent → HTTP 200 (not 500), empty providers, honest error."""
        poisoned = {
            "harness_usage_status": None,
            "harness_usage_status.config": None,
            "harness_usage_status.providers.registry": None,
        }
        with patch.dict(sys.modules, poisoned):
            resp = self.client.get(ENDPOINT)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["providers"], [])
        self.assertIn("not installed", resp.data["error"])
        self.assertIn("fetched_at", resp.data)

    def test_endpoint_never_500_on_internal_error(self):
        """Config/fetch blow-up degrades to 200 (was HTTP 500 before the fix)."""
        with patch("harness_usage_status.config.load_config",
                   side_effect=RuntimeError("config parse boom")):
            resp = self.client.get(ENDPOINT)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["providers"], [])
        self.assertIn("config parse boom", resp.data["error"])

    def test_loader_degrades_when_package_import_fails(self):
        """Unit-level: ImportError → ([], '... not installed'), never raises."""
        poisoned = {
            "harness_usage_status": None,
            "harness_usage_status.config": None,
            "harness_usage_status.providers.registry": None,
        }
        with patch.dict(sys.modules, poisoned):
            entries, error = _load_provider_usage()

        self.assertEqual(entries, [])
        self.assertIn("not installed", error)
