"""Backend client tests for the new /boards/{id}/agent-stats/ REST client.

Defensive coverage — confirms the wired backend endpoint is invoked
with the right URL + params, and that exceptions degrade gracefully
to empty rows (Default First: never break plan).
"""

from unittest.mock import MagicMock

from odin.backends.taskit import TaskItBackend


def _make_backend(client):
    """Build a TaskItBackend with an injected fake client; no auth."""
    backend = TaskItBackend.__new__(TaskItBackend)
    backend._base_url = "http://test"
    backend._board_id = 7
    backend._created_by = "odin@harness.kit"
    backend._client = client
    backend._agent_user_cache = {}
    backend._spec_pk_cache = {}
    backend._trial_board_id = None
    return backend


def test_fetch_agent_stats_parses_rows():
    fake_client = MagicMock()
    fake_client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "agents": [
                {"name": "gemini", "sample_count": 5, "success_count": 5,
                 "success_rate": 1.0, "median_tokens": 4000},
            ],
        },
    )

    backend = _make_backend(fake_client)
    out = backend.fetch_agent_stats()
    assert out == {
        "agents": [
            {"name": "gemini", "sample_count": 5, "success_count": 5,
             "success_rate": 1.0, "median_tokens": 4000},
        ],
    }
    args, kwargs = fake_client.get.call_args
    assert "/boards/7/agent-stats/" in args[0]
    # No spec_id passed → no params query string.
    assert not kwargs.get("params")


def test_fetch_agent_stats_forwards_spec_id_param():
    fake_client = MagicMock()
    fake_client.get.return_value = MagicMock(
        status_code=200, json=lambda: {"agents": []},
    )

    backend = _make_backend(fake_client)
    backend.fetch_agent_stats(spec_id="42")
    args, kwargs = fake_client.get.call_args
    assert kwargs.get("params") == {"spec": "42"}


def test_fetch_agent_stats_exception_returns_empty():
    """Default First: any backend blip → empty stats. Routing falls
    back to static rather than failing the plan."""
    fake_client = MagicMock()
    fake_client.get.side_effect = RuntimeError("connection refused")

    backend = _make_backend(fake_client)
    out = backend.fetch_agent_stats()
    assert out == {"agents": []}


def test_fetch_agent_stats_non_dict_payload_returns_empty():
    """A 200 with garbage JSON should not poison routing."""
    fake_client = MagicMock()
    fake_client.get.return_value = MagicMock(
        status_code=200, json=lambda: "not a dict",
    )

    backend = _make_backend(fake_client)
    out = backend.fetch_agent_stats()
    assert out == {"agents": []}

