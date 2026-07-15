"""Tests for the analytics quota-status endpoint.

Root cause: the dashboard provider cards showed silent "No data" or
misleading "0%" because the backend buried the error reason inside
``raw`` and never surfaced it as a first-class field.  The frontend had
no way to show *why* a provider couldn't report, nor *when* the number
was last fetched.

These tests pin the two contract additions:

1. ``error`` — a first-class string extracted from ``raw["error"]`` so
   the card can say WHY (e.g. "token expired") instead of a silent
   "No data".
2. ``last_fetched`` — an ISO timestamp so the card can show "as of
   HH:MM" and make silent staleness visible.
"""

from unittest.mock import patch, AsyncMock

from datetime import datetime

from .base import APITestCase


def _fake_usage(provider_name, *, error=None, usage_pct=None):
    """Build a minimal UsageInfo-like object for testing."""
    from harness_usage_status.models import UsageInfo

    raw = {"error": error} if error else {}
    return UsageInfo(
        provider=provider_name,
        usage_pct=usage_pct,
        raw=raw or None,
    )


def _fake_status(provider_name):
    from harness_usage_status.models import StatusInfo

    return StatusInfo(provider=provider_name)


class TestQuotaStatusContract(APITestCase):
    """The quota-status endpoint must surface errors and fetch time."""

    def _patch_providers(self, providers):
        """Patch get_all_providers + load_config so no real network calls fire."""
        provider_objs = {}
        for name, info in providers.items():
            mock_prov = AsyncMock()
            mock_prov.get_usage = AsyncMock(return_value=info["usage"])
            mock_prov.get_status = AsyncMock(return_value=info["status"])
            provider_objs[name] = mock_prov

        patcher_config = patch(
            "harness_usage_status.config.load_config",
            return_value=type("C", (), {"get_provider_configs": lambda self: {}})(),
        )
        patcher_providers = patch(
            "harness_usage_status.providers.registry.get_all_providers",
            return_value=provider_objs,
        )
        patcher_config.start()
        patcher_providers.start()
        self.addCleanup(patcher_config.stop)
        self.addCleanup(patcher_providers.stop)

    def test_error_surfaces_as_first_class_field(self):
        """When raw["error"] exists, the response must include a top-level
        ``error`` string so the card can show WHY, not silent 'No data'."""
        self._patch_providers({
            "claude_code": {
                "usage": _fake_usage("Claude Code", error="No OAuth token found"),
                "status": _fake_status("Claude Code"),
            },
        })
        resp = self.client.get("/api/analytics/quota-status/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        entry = resp.data[0]
        self.assertEqual(entry["error"], "No OAuth token found")
        self.assertIsNone(entry["usage_pct"])

    def test_error_null_when_provider_reports_usage(self):
        """A healthy provider has error=null alongside a real usage_pct."""
        self._patch_providers({
            "codex": {
                "usage": _fake_usage("Codex (OpenAI)", usage_pct=1.0),
                "status": _fake_status("Codex (OpenAI)"),
            },
        })
        resp = self.client.get("/api/analytics/quota-status/")
        self.assertEqual(resp.status_code, 200)
        entry = resp.data[0]
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["usage_pct"], 1.0)

    def test_last_fetched_timestamp_present(self):
        """Every entry must carry a ``last_fetched`` ISO timestamp so the
        card can show 'as of HH:MM' — making silent staleness visible."""
        self._patch_providers({
            "claude_code": {
                "usage": _fake_usage("Claude Code", error="No OAuth token found"),
                "status": _fake_status("Claude Code"),
            },
        })
        resp = self.client.get("/api/analytics/quota-status/")
        self.assertEqual(resp.status_code, 200)
        entry = resp.data[0]
        self.assertIsNotNone(entry["last_fetched"])
        # Must be parseable as ISO datetime.
        parsed = datetime.fromisoformat(entry["last_fetched"])
        self.assertIsNotNone(parsed)

    def test_mixed_providers_all_carry_error_and_timestamp(self):
        """A batch with one healthy + one broken provider: both get error
        and last_fetched fields."""
        self._patch_providers({
            "codex": {
                "usage": _fake_usage("Codex (OpenAI)", usage_pct=1.0),
                "status": _fake_status("Codex (OpenAI)"),
            },
            "glm": {
                "usage": _fake_usage("GLM (Zhipu AI)", error="GLM not configured"),
                "status": _fake_status("GLM (Zhipu AI)"),
            },
        })
        resp = self.client.get("/api/analytics/quota-status/")
        self.assertEqual(resp.status_code, 200)
        by_provider = {e["provider"]: e for e in resp.data}
        self.assertIsNone(by_provider["Codex (OpenAI)"]["error"])
        self.assertIsNotNone(by_provider["Codex (OpenAI)"]["last_fetched"])
        self.assertEqual(by_provider["GLM (Zhipu AI)"]["error"], "GLM not configured")
        self.assertIsNotNone(by_provider["GLM (Zhipu AI)"]["last_fetched"])
