"""Tests for the quickstart sample loader's provider-selection logic.

Tags: [pure] — these functions are pure (no I/O). The loader script lives at
``odin/sample_specs/quickstart/run.py`` and is loaded here via importlib because
``sample_specs/`` is a specs directory, not an importable package.

The loader's job: read ``odin doctor --json``, find which providers are
available, and pick the best one so the sample spec can run with
``--base-agent <picked>`` (the default ``base_agent`` is ``claude`` — without
this override a user whose only provider is, say, ``glm`` cannot plan at all).

Covers:
- ``available_providers``: parse doctor JSON, filter available, preserve order
- ``pick_best``: cheapest-capable wins; any single provider works; fallback
- ``derive_preference``: data-driven from odin's DEFAULT_MODEL_ROUTING, deduped
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RUN_PY = Path(__file__).resolve().parents[2] / "sample_specs" / "quickstart" / "run.py"


@pytest.fixture(scope="module")
def loader():
    """Load run.py as a module (sample_specs is not a package)."""
    spec = importlib.util.spec_from_file_location("quickstart_loader", _RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── available_providers ───────────────────────────────────────────────────


def test_available_providers_filters_and_preserves_order(loader):
    doctor = {
        "agents": [
            {"name": "claude", "available": False, "installed": True, "authenticated": False},
            {"name": "codex", "available": True, "installed": True, "authenticated": True},
            {"name": "glm", "available": True, "installed": True, "authenticated": True},
        ],
        "exit_code": 0,
    }
    assert loader.available_providers(doctor) == ["codex", "glm"]


def test_available_providers_empty_when_none_available(loader):
    doctor = {
        "agents": [
            {"name": "claude", "available": False},
            {"name": "glm", "available": False},
        ],
        "exit_code": 1,
    }
    assert loader.available_providers(doctor) == []


def test_available_providers_handles_missing_agents_key(loader):
    assert loader.available_providers({"exit_code": 1}) == []
    assert loader.available_providers({}) == []


def test_extract_json_clean(loader):
    assert loader._extract_json('{"agents": [], "exit_code": 1}') == {"agents": [], "exit_code": 1}


def test_extract_json_tolerates_leading_log_line(loader):
    # Console logs now route to stderr, so doctor --json stdout is clean JSON.
    # _extract_json still tolerates a leading line as defense-in-depth (e.g. a
    # caller that pipes merged stdout+stderr).
    noisy = (
        '[2026-07-08 04:40:23] INFO [cli.py:263] - CLI initialized: config=x\n'
        '{"agents": [{"name": "glm", "available": true}], "exit_code": 0}'
    )
    assert loader._extract_json(noisy) == {
        "agents": [{"name": "glm", "available": True}],
        "exit_code": 0,
    }


def test_extract_json_tolerates_trailing_noise(loader):
    assert loader._extract_json('{"exit_code": 0}\n[later log line]') == {"exit_code": 0}


def test_extract_json_raises_when_no_object(loader):
    with pytest.raises(ValueError):
        loader._extract_json("not json at all")


# ── pick_best ─────────────────────────────────────────────────────────────


def test_pick_best_none_when_empty(loader):
    assert loader.pick_best([], ["gemini", "glm", "claude"]) is None


def test_pick_best_single_available_in_preference(loader):
    assert loader.pick_best(["glm"], ["gemini", "glm", "claude"]) == "glm"


def test_pick_best_single_available_not_in_preference(loader):
    # An unknown / future provider must still be usable — no hardcoded assumptions.
    assert loader.pick_best(["agy"], ["gemini", "glm", "claude"]) == "agy"
    assert loader.pick_best(["some-new-provider"], ["gemini", "glm", "claude"]) == "some-new-provider"


def test_pick_best_cheapest_wins_when_multiple(loader):
    # preference is cheapest-first; the first available match in preference wins.
    pref = ["gemini", "glm", "minimax", "claude", "codex"]
    assert loader.pick_best(["claude", "glm", "codex"], pref) == "glm"
    assert loader.pick_best(["claude", "codex"], pref) == "claude"


def test_pick_best_falls_back_to_first_available_when_none_in_preference(loader):
    # When no available provider is in the preference list, use the first available
    # (doctor's reported order) rather than failing.
    assert loader.pick_best(["agy", "future-x"], ["gemini", "glm", "claude"]) == "agy"


def test_pick_best_empty_preference_uses_first_available(loader):
    assert loader.pick_best(["codex", "glm"], []) == "codex"


def test_pick_best_ignores_duplicates_in_available(loader):
    assert loader.pick_best(["glm", "glm", "claude"], ["gemini", "glm", "claude"]) == "glm"


# ── plan_command ──────────────────────────────────────────────────────────


def test_plan_command_includes_base_agent_override(loader):
    cmd = loader.plan_command("glm", "odin/sample_specs/quickstart/quickstart_spec.md")
    assert cmd[0] == "odin"
    assert cmd[1] == "plan"
    assert "--auto" in cmd
    # --base-agent is the loader's reason to exist: without it the kit's default
    # `claude` decomposition agent is used and fails for non-claude users.
    assert "--base-agent" in cmd
    assert cmd[cmd.index("--base-agent") + 1] == "glm"
    assert "odin/sample_specs/quickstart/quickstart_spec.md" in cmd


def test_plan_command_reflects_picked_provider(loader):
    a = loader.plan_command("gemini", "spec.md")
    b = loader.plan_command("agy", "spec.md")
    assert a[a.index("--base-agent") + 1] == "gemini"
    assert b[b.index("--base-agent") + 1] == "agy"


# ── _guidance_for_no_provider ─────────────────────────────────────────────


def test_no_provider_guidance_names_each_login_command(loader):
    """A first-timer with no provider authenticated gets concrete next steps.

    Each line must name the actual command they run, matching what doctor's
    own per-provider probe says (e.g. ``codex login``, not a vague "log in to
    populate ..."). A stranger should be able to copy-paste the command.
    """
    text = loader._guidance_for_no_provider()
    # codex line must name the executable command, not just describe the file.
    assert "codex login" in text, (
        "guidance must tell the user to run `codex login` (doctor's probe says "
        "the same) — not merely 'log in to populate ~/.codex/auth.json'"
    )


# ── derive_preference ─────────────────────────────────────────────────────


def test_derive_preference_is_deduped_list(loader):
    pref = loader.derive_preference()
    assert isinstance(pref, list)
    assert len(pref) == len(set(pref)), "preference list must not contain duplicates"


def test_derive_preference_cheapest_before_expensive(loader):
    # The kit's own routing ranks low-cost providers (gemini/glm/minimax) ahead of
    # claude/codex. The loader inherits that ordering rather than inventing its own.
    pref = loader.derive_preference()
    cheap = {"gemini", "glm", "minimax"}
    expensive = {"claude", "codex"}
    cheap_idx = [pref.index(p) for p in cheap if p in pref]
    expensive_idx = [pref.index(p) for p in expensive if p in pref]
    if cheap_idx and expensive_idx:
        assert max(cheap_idx) < min(expensive_idx), (
            f"cheap providers {cheap} must rank before expensive {expensive}, got {pref}"
        )


def test_derive_preference_falls_back_when_odin_unimportable(loader, monkeypatch):
    # pipx install: `odin` is on PATH but not importable by this process.
    # derive_preference must degrade to [] (pick_best then uses first-available),
    # not crash — so the loader works no matter how odin was installed.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("odin"):
            raise ImportError("simulated pipx isolation")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert loader.derive_preference() == []
