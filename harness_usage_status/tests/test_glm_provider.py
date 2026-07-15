"""GLM provider regression: missing 'percentage' must yield null, not 0.

Root cause: ``glm.py`` used ``primary.get("percentage", 0)`` which
defaults to ``0`` when the key is absent from the API response.  This
made the dashboard card show a misleading "0% used" when GLM's quota
limit endpoint returned a TOKENS_LIMIT entry *without* a percentage
field — i.e. data was genuinely unavailable but the card reported a
fake zero instead of 'No data'.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness_usage_status.providers.glm import GLMProvider


def _provider(api_key="sk-glm-test"):
    return GLMProvider(config={"api_key": api_key})


def _mock_response(json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b'{"data":1}'
    resp.json.return_value = json_data
    return resp


async def _mock_get(url, **kwargs):
    """Return canned responses keyed by URL path."""
    if "quota/limit" in str(url):
        return _mock_response(_provider_quota_response)
    if "model-usage" in str(url):
        return _mock_response({"data": []})
    return _mock_response({})


_provider_quota_response = {}


@pytest.mark.asyncio
async def test_missing_percentage_yields_null_not_zero():
    """A TOKENS_LIMIT entry with no 'percentage' key must produce
    usage_pct=None (no data), not a misleading 0."""
    global _provider_quota_response
    _provider_quota_response = {
        "data": {
            "level": "tier1",
            "limits": [
                {"type": "TOKENS_LIMIT", "unit": 3},
            ],
        },
    }
    client = MagicMock()
    client.get = _mock_get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("harness_usage_status.providers.glm.httpx.AsyncClient", return_value=client):
        usage = await _provider().get_usage()
    assert usage.usage_pct is None, (
        "Missing 'percentage' must yield None (no data), not 0"
    )


@pytest.mark.asyncio
async def test_present_percentage_is_reported_correctly():
    """When the API does return a percentage, it is passed through."""
    global _provider_quota_response
    _provider_quota_response = {
        "data": {
            "level": "tier1",
            "limits": [
                {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 37.5},
            ],
        },
    }
    client = MagicMock()
    client.get = _mock_get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("harness_usage_status.providers.glm.httpx.AsyncClient", return_value=client):
        usage = await _provider().get_usage()
    assert usage.usage_pct == 37.5


@pytest.mark.asyncio
async def test_zero_percentage_is_a_real_zero():
    """When the API explicitly returns percentage=0, that's a genuine
    0% (nothing used) — not 'no data'.  Only a *missing* key is null."""
    global _provider_quota_response
    _provider_quota_response = {
        "data": {
            "level": "tier1",
            "limits": [
                {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 0},
            ],
        },
    }
    client = MagicMock()
    client.get = _mock_get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("harness_usage_status.providers.glm.httpx.AsyncClient", return_value=client):
        usage = await _provider().get_usage()
    assert usage.usage_pct == 0
