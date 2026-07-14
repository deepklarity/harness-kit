"""MiniMaxProvider.get_usage() field-mapping regression test.

Root cause anchor: operator's real screenshot showed MiniMax at 64/100
used in the 5h window (see taskit-backend tests/test_quota_ground_truth.py,
task #159). This test pins that ground truth and guards the "0/0" bug
report: an out-of-plan model (current_interval_total_count == 0) must
never be summed into the aggregate quota.

Field semantics, confirmed against the MiniMax-Coding-Plan-MCP /
CodexBar reference parsers: despite its name, current_interval_usage_count
is the REMAINING count, not the used count.
"""
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from harness_usage_status.providers.minimax import MiniMaxProvider


def _provider(api_key="sk-cp-test"):
    return MiniMaxProvider(config={"api_key": api_key})


@pytest.mark.asyncio
@respx.mock
async def test_get_usage_matches_ground_truth_64_used_of_100():
    """Real plan model: total=100, usage_count(remaining)=36 -> 64 used, 64%."""
    respx.get("https://api.minimax.io/v1/api/openplatform/coding_plan/remains").mock(
        return_value=Response(200, json={
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "model_remains": [
                {
                    "model_name": "MiniMax-M2",
                    "current_interval_total_count": 100,
                    "current_interval_usage_count": 36,
                },
            ],
        })
    )
    usage = await _provider().get_usage()
    usage.compute_pct()

    assert usage.used == 64
    assert usage.remaining == 36
    assert usage.quota_limit == 100
    assert usage.usage_pct == pytest.approx(64.0)


@pytest.mark.asyncio
@respx.mock
async def test_out_of_plan_models_are_excluded_not_rendered_as_zero_zero():
    """Models with total_count 0 (not on the account's plan) must be
    dropped from the aggregate entirely, not summed in as 0/0."""
    respx.get("https://api.minimax.io/v1/api/openplatform/coding_plan/remains").mock(
        return_value=Response(200, json={
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "model_remains": [
                {
                    "model_name": "MiniMax-Text-01",
                    "current_interval_total_count": 0,
                    "current_interval_usage_count": 0,
                },
                {
                    "model_name": "MiniMax-M2",
                    "current_interval_total_count": 100,
                    "current_interval_usage_count": 36,
                },
            ],
        })
    )
    usage = await _provider().get_usage()
    usage.compute_pct()

    # Aggregate reflects only the real plan model — not diluted by the
    # zero-limit model, and never surfaces as an overall 0/0.
    assert usage.quota_limit == 100
    assert usage.used == 64
    assert usage.usage_pct == pytest.approx(64.0)


@pytest.mark.asyncio
@respx.mock
async def test_all_models_out_of_plan_yields_no_data_not_zero_zero():
    """This is the exact reported bug's root shape — confirmed live: the
    real MiniMax account's model_remains entries ("general", "video") both
    report current_interval_total_count == 0. Must render as 'no data',
    never as a literal 0/0."""
    respx.get("https://api.minimax.io/v1/api/openplatform/coding_plan/remains").mock(
        return_value=Response(200, json={
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "model_remains": [
                {
                    "model_name": "MiniMax-Text-01",
                    "current_interval_total_count": 0,
                    "current_interval_usage_count": 0,
                },
            ],
        })
    )
    usage = await _provider().get_usage()
    assert usage.quota_limit is None
    assert usage.used is None
    assert usage.remaining is None


@pytest.mark.asyncio
async def test_missing_api_key_returns_error_raw_no_crash():
    # Must not fall back to a real local opencode auth token in CI/dev
    # environments — isolate both the config key and the file fallback.
    with patch(
        "harness_usage_status.providers.minimax._read_opencode_auth_token",
        return_value=None,
    ):
        usage = await _provider(api_key=None).get_usage()
    assert usage.used is None
    assert "error" in usage.raw
