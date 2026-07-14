"""Tests for `odin doctor` — environment sanity checks.

Tags: [pure] + [mock] — probe logic and matrix derivation are pure; I/O probes
are mocked. No real services, no real CLIs, no network.

Covers:
- service probes (backend/frontend port, celery default + merges queue)
- agent CLI probes (installed vs authenticated, per-provider auth detection)
- sandbox runtime probes (msb binary, image, throwaway VM boot, --fast skip)
- host probes (free memory vs VM budget, disk headroom)
- partial-provider feature matrix (which kit features work with the CLIs found)
- output formats (human table, --json), exit-code semantics
- simulated missing provider degrades honestly instead of erroring
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from odin import doctor
from odin.doctor import (
    AgentProbe,
    Check,
    DoctorReport,
    FAIL,
    PASS,
    SKIP,
    WARN,
    derive_feature_matrix,
    format_json,
    format_text,
    run_doctor,
)
from odin.models import AgentConfig, OdinConfig


# ── fixtures ──────────────────────────────────────────────────────────────


def _cfg(**overrides) -> OdinConfig:
    """Minimal OdinConfig with the five fable agents present."""
    agents = {
        name: AgentConfig(cli_command=cli, enabled=True)
        for name, cli in (
            ("claude", "claude"),
            ("codex", "codex"),
            ("glm", "opencode"),
            ("minimax", "opencode"),
            ("agy", "agy"),
        )
    }
    base = dict(
        base_agent="claude",
        board_backend="local",
        agents=agents,
    )
    base.update(overrides)
    return OdinConfig(**base)


@pytest.fixture
def cfg():
    return _cfg()


# ── service probes ────────────────────────────────────────────────────────


class TestServiceProbes:
    def test_backend_port_open_is_pass(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True):
            report = run_doctor(cfg, backend_port=9100, frontend_port=9200)
        backend = next(c for c in report.checks if c.name == "backend :9100")
        assert backend.status == PASS

    def test_backend_port_closed_is_warn_with_fix(self, cfg):
        with patch.object(doctor, "_port_open", return_value=False), \
             patch.object(doctor, "_celery_running", return_value=True):
            report = run_doctor(cfg, backend_port=9100, frontend_port=9200)
        backend = next(c for c in report.checks if c.name == "backend :9100")
        assert backend.status == WARN
        assert backend.fix, "closed backend must include a fix command"

    def test_frontend_port_closed_is_warn(self, cfg):
        with patch.object(doctor, "_port_open", side_effect=[True, False]), \
             patch.object(doctor, "_celery_running", return_value=True):
            report = run_doctor(cfg, backend_port=9100, frontend_port=9200)
        frontend = next(c for c in report.checks if c.name == "frontend :9200")
        assert frontend.status == WARN

    def test_celery_default_running_is_pass(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True):
            report = run_doctor(cfg)
        celery = next(c for c in report.checks if c.name == "celery worker (default)")
        assert celery.status == PASS

    def test_celery_default_down_is_warn(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=False):
            report = run_doctor(cfg)
        celery = next(c for c in report.checks if c.name == "celery worker (default)")
        assert celery.status == WARN

    def test_merges_queue_check_only_when_configured(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True):
            # No merge queue name → no merges check emitted.
            report = run_doctor(cfg)
        assert not any("merges" in c.name for c in report.checks)

    def test_merges_queue_check_when_configured(self, cfg):
        import odin.doctor as d

        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(d, "_celery_running", side_effect=[True, True]):
            report = run_doctor(cfg, merge_queue_name="merges")
        merges = [c for c in report.checks if "merges" in c.name]
        assert merges, "merges queue check emitted when merge_queue_name set"
        assert merges[0].status == PASS


# ── agent CLI probes ──────────────────────────────────────────────────────


class TestAgentProbes:
    def test_all_agents_available(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value="/usr/bin/x"), \
             patch.object(doctor, "_agent_authenticated", return_value=(True, "ok")):
            report = run_doctor(cfg)
        assert len(report.agents) == 5
        assert all(a.available for a in report.agents)
        assert all(a.status == PASS for a in report.agents)

    def test_agent_installed_not_authenticated_is_warn(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value="/usr/bin/claude"), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "no token")):
            report = run_doctor(cfg)
        claude = next(a for a in report.agents if a.name == "claude")
        assert claude.installed is True
        assert claude.authenticated is False
        assert claude.status == WARN
        assert "no token" in claude.detail

    def test_agent_not_installed_is_warn(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")):
            report = run_doctor(cfg)
        codex = next(a for a in report.agents if a.name == "codex")
        assert codex.installed is False
        assert codex.status == WARN


# ── sandbox probes ────────────────────────────────────────────────────────


class TestSandboxProbes:
    def test_msb_missing_is_warn_with_fix(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value=None):
            report = run_doctor(cfg)
        msb = next(c for c in report.checks if c.name == "microsandbox runtime")
        assert msb.status == WARN
        assert msb.fix, "missing msb must include a fix command"

    def test_msb_present_image_missing_is_warn(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value="/usr/local/bin/msb"), \
             patch.object(doctor, "_msb_image_exists", return_value=False):
            report = run_doctor(cfg)
        img = next(c for c in report.checks if c.name == "sandbox image")
        assert img.status == WARN

    def test_fast_skips_vm_boot(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value="/usr/local/bin/msb"), \
             patch.object(doctor, "_msb_image_exists", return_value=True) as img_mock, \
             patch.object(doctor, "_msb_boot_test") as boot_mock:
            report = run_doctor(cfg, fast=True)
        boot = next(c for c in report.checks if c.name == "throwaway VM boot")
        assert boot.status == SKIP
        boot_mock.assert_not_called()
        img_mock.assert_called()

    def test_vm_boot_ok_is_pass(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value="/usr/local/bin/msb"), \
             patch.object(doctor, "_msb_image_exists", return_value=True), \
             patch.object(doctor, "_msb_boot_test", return_value=(True, "booted in 0.3s")):
            report = run_doctor(cfg, fast=False)
        boot = next(c for c in report.checks if c.name == "throwaway VM boot")
        assert boot.status == PASS

    def test_vm_boot_fail_is_fail(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value="/usr/local/bin/msb"), \
             patch.object(doctor, "_msb_image_exists", return_value=True), \
             patch.object(doctor, "_msb_boot_test", return_value=(False, "timeout")):
            report = run_doctor(cfg, fast=False)
        boot = next(c for c in report.checks if c.name == "throwaway VM boot")
        assert boot.status == FAIL


# ── host probes ───────────────────────────────────────────────────────────


class TestHostProbes:
    def test_memory_above_budget_is_pass(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value=None), \
             patch.object(doctor, "_host_memory_mib", return_value=(16384, 8000)), \
             patch.object(doctor, "_disk_free_mib", return_value=50000):
            report = run_doctor(cfg, vm_mem_budget_mib=4096)
        mem = next(c for c in report.checks if c.name == "free memory")
        assert mem.status == PASS

    def test_memory_below_budget_is_warn(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value=None), \
             patch.object(doctor, "_host_memory_mib", return_value=(8192, 2048)), \
             patch.object(doctor, "_disk_free_mib", return_value=50000):
            report = run_doctor(cfg, vm_mem_budget_mib=4096)
        mem = next(c for c in report.checks if c.name == "free memory")
        assert mem.status == WARN

    def test_disk_low_is_warn(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value=None), \
             patch.object(doctor, "_host_memory_mib", return_value=(16384, 8000)), \
             patch.object(doctor, "_disk_free_mib", return_value=512):
            report = run_doctor(cfg, vm_mem_budget_mib=4096)
        disk = next(c for c in report.checks if "disk" in c.name)
        assert disk.status == WARN


# ── feature matrix ────────────────────────────────────────────────────────


class TestFeatureMatrix:
    def test_all_available_all_features_pass(self, cfg):
        avail = {"claude", "codex", "glm", "minimax", "agy"}
        rows = derive_feature_matrix(cfg, avail)
        assert all(r.status == PASS for r in rows), [r.feature for r in rows]

    def test_only_codex_execution_passes(self, cfg):
        rows = derive_feature_matrix(cfg, {"codex"})
        by_feat = {r.feature: r for r in rows}
        assert by_feat["execution"].status == PASS
        assert by_feat["planning"].status != PASS, "planning default claude missing → not green"

    def test_no_agents_execution_fails(self, cfg):
        rows = derive_feature_matrix(cfg, set())
        by_feat = {r.feature: r for r in rows}
        assert by_feat["execution"].status == FAIL

    def test_planning_follows_base_agent_override(self):
        cfg = _cfg(base_agent="codex")
        rows = derive_feature_matrix(cfg, {"codex"})
        planning = {r.feature: r for r in rows}["planning"]
        assert planning.status == PASS
        assert planning.serving_agent == "codex"

    def test_reflection_needs_default_reviewer(self, cfg):
        rows = derive_feature_matrix(cfg, {"codex"})
        reflection = {r.feature: r for r in rows}["reflection"]
        assert reflection.status != PASS, "reflection default claude missing → not green"

    def test_each_feature_names_default_agent(self, cfg):
        rows = derive_feature_matrix(cfg, set())
        for r in rows:
            assert r.default_agent, f"{r.feature} must name its default agent"


# ── exit code ─────────────────────────────────────────────────────────────


class TestExitCode:
    def _common_patches(self, agents_available: bool):
        authed = (True, "ok") if agents_available else (False, "")
        cli = "/usr/bin/x" if agents_available else None
        return (
            patch.object(doctor, "_port_open", return_value=True),
            patch.object(doctor, "_celery_running", return_value=True),
            patch.object(doctor, "_cli_on_path", return_value=cli),
            patch.object(doctor, "_agent_authenticated", return_value=authed),
            patch.object(doctor, "_msb_bin", return_value=None),
        )

    def test_zero_agents_exits_nonzero(self, cfg):
        patches = self._common_patches(agents_available=False)
        for p in patches:
            p.start()
        try:
            report = run_doctor(cfg)
        finally:
            for p in patches:
                p.stop()
        assert report.exit_code == 1, "zero agents blocks a basic run"

    def test_one_agent_exits_zero(self, cfg):
        patches = self._common_patches(agents_available=True)
        for p in patches:
            p.start()
        try:
            report = run_doctor(cfg)
        finally:
            for p in patches:
                p.stop()
        assert report.exit_code == 0


# ── output formats ────────────────────────────────────────────────────────


class TestOutputFormats:
    def test_json_is_well_formed_and_complete(self, cfg):
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value="/usr/bin/x"), \
             patch.object(doctor, "_agent_authenticated", return_value=(True, "ok")), \
             patch.object(doctor, "_msb_bin", return_value=None):
            report = run_doctor(cfg)
        blob = format_json(report)
        data = json.loads(blob)
        assert {"checks", "agents", "features", "exit_code"} <= set(data)
        assert isinstance(data["checks"], list)
        assert len(data["agents"]) == 5

    def test_text_has_status_markers_and_fix(self, cfg):
        with patch.object(doctor, "_port_open", return_value=False), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", return_value=None), \
             patch.object(doctor, "_agent_authenticated", return_value=(False, "")), \
             patch.object(doctor, "_msb_bin", return_value=None):
            report = run_doctor(cfg)
        text = format_text(report)
        assert "PASS" in text or "WARN" in text or "FAIL" in text
        # A failing check must surface its fix command.
        assert "fix" in text.lower() or "sh " in text or "start_services" in text


# ── simulated missing provider (degrades honestly) ───────────────────────


class TestMissingProviderDegrades:
    def test_codex_missing_does_not_error(self, cfg):
        """PATH without codex → codex probe WARN, matrix degrades, no exception."""

        def fake_cli(cli: str):
            return None if cli == "codex" else f"/usr/bin/{cli}"

        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", side_effect=fake_cli), \
             patch.object(doctor, "_agent_authenticated", return_value=(True, "ok")), \
             patch.object(doctor, "_msb_bin", return_value=None):
            report = run_doctor(cfg)
        codex = next(a for a in report.agents if a.name == "codex")
        assert codex.installed is False
        # No exception raised, report still produced.
        assert report.exit_code == 0


# ── .claude-token staleness (W6.11 / task #232) ──────────────────────────
#
# The harness prefers the live keychain token but still resolves
# ``.claude-token`` files as a fallback (see
# ``harnesses/microsandbox.py::_claude_token``). When a stale file exists
# and the live source becomes momentarily unreadable, the harness will pick
# the stale value — and a doctor diagnosis will be led astray. These tests
# pin the WARN behavior so the trap is named.


class TestClaudeTokenStaleness:
    @pytest.fixture
    def patched_microsandbox(self, monkeypatch):
        """Inject a fake ``claude_token_candidate_paths`` so the test never
        walks the host filesystem. Imports the real symbol lazily — the test
        fails with a useful ImportError if the harness stops exposing it."""

        import odin.harnesses.microsandbox as msb

        def _set(paths):
            monkeypatch.setattr(
                msb, "claude_token_candidate_paths",
                lambda working_dir=None, explicit_file=None: list(paths),
                raising=False,
            )

        return _set

    @pytest.fixture
    def no_io(self):
        """Suppress the unrelated I/O probes that ``run_doctor`` runs."""
        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_msb_bin", return_value=None):
            yield

    def _report(self, cfg, **mocks):
        defaults = {
            "_port_open": True,
            "_celery_running": True,
            "_cli_on_path": None,
            "_agent_authenticated": (False, ""),
            "_msb_bin": None,
        }
        defaults.update(mocks)
        with patch.object(doctor, "_port_open", return_value=defaults["_port_open"]), \
             patch.object(doctor, "_celery_running", return_value=defaults["_celery_running"]), \
             patch.object(doctor, "_cli_on_path", return_value=defaults["_cli_on_path"]), \
             patch.object(doctor, "_agent_authenticated", return_value=defaults["_agent_authenticated"]), \
             patch.object(doctor, "_msb_bin", return_value=defaults["_msb_bin"]):
            return run_doctor(cfg)

    def _stale_checks(self, report) -> list:
        return [c for c in report.checks if c.name.startswith("stale .claude-token:")]

    def test_stale_file_with_env_live_token_warns(
        self, cfg, tmp_path, monkeypatch, patched_microsandbox, no_io,
    ):
        stale_file = tmp_path / ".claude-token"
        stale_file.write_text("stale-token")
        patched_microsandbox([stale_file])
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "live-token")
        # Force keychain probe to no-op so the env-var path is exercised.
        monkeypatch.setattr(doctor, "_claude_keychain_access_token", lambda: None)

        report = self._report(cfg)
        stale = self._stale_checks(report)
        assert len(stale) == 1
        chk = stale[0]
        assert chk.status == WARN
        assert str(stale_file) in chk.name
        assert chk.fix, "stale token must include the fix command"
        # The fix should mention how to refresh or delete the file.
        fix = chk.fix.lower()
        assert "setup-token" in fix or "rm " in fix
        # Token secrecy: never echo the stale or live token in the detail.
        assert "stale-token" not in chk.detail
        assert "live-token" not in chk.detail

    def test_matching_file_does_not_warn(
        self, cfg, tmp_path, monkeypatch, patched_microsandbox, no_io,
    ):
        same = tmp_path / ".claude-token"
        same.write_text("same-token")
        patched_microsandbox([same])
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "same-token")
        monkeypatch.setattr(doctor, "_claude_keychain_access_token", lambda: None)

        report = self._report(cfg)
        assert self._stale_checks(report) == [], "matching token → no WARN"

    def test_no_live_source_does_not_warn(
        self, cfg, tmp_path, monkeypatch, patched_microsandbox, no_io,
    ):
        """Without an env var or Keychain token we have nothing to compare
        against — emitting a WARN would be a false positive."""
        stale = tmp_path / ".claude-token"
        stale.write_text("some-old-value")
        patched_microsandbox([stale])
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr(doctor, "_claude_keychain_access_token", lambda: None)

        report = self._report(cfg)
        assert self._stale_checks(report) == [], "no live source → no WARN"

    def test_each_existing_stale_file_emits_one_warn(
        self, cfg, tmp_path, monkeypatch, patched_microsandbox, no_io,
    ):
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        f1 = a / ".claude-token"; f1.write_text("stale-1")
        f2 = b / ".claude-token"; f2.write_text("stale-2")
        patched_microsandbox([f1, f2])
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "live")
        monkeypatch.setattr(doctor, "_claude_keychain_access_token", lambda: None)

        report = self._report(cfg)
        stale = self._stale_checks(report)
        paths = {c.name for c in stale}
        assert len(stale) == 2
        assert any(str(f1) in n for n in paths)
        assert any(str(f2) in n for n in paths)

    def test_dedup_when_path_list_duplicates_resolve_same_file(
        self, cfg, tmp_path, monkeypatch, patched_microsandbox, no_io,
    ):
        stale = tmp_path / ".claude-token"
        stale.write_text("dup")
        # Both entries must resolve to the same file — WARN only once.
        patched_microsandbox([stale, stale])
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "live")
        monkeypatch.setattr(doctor, "_claude_keychain_access_token", lambda: None)

        report = self._report(cfg)
        stale_checks = self._stale_checks(report)
        assert len(stale_checks) == 1

    def test_missing_file_does_not_warn(
        self, cfg, tmp_path, monkeypatch, patched_microsandbox, no_io,
    ):
        patched_microsandbox([tmp_path / "nope" / ".claude-token"])
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "live")
        monkeypatch.setattr(doctor, "_claude_keychain_access_token", lambda: None)

        report = self._report(cfg)
        assert self._stale_checks(report) == []

    def test_empty_file_is_skipped(
        self, cfg, tmp_path, monkeypatch, patched_microsandbox, no_io,
    ):
        empty = tmp_path / ".claude-token"
        empty.write_text("")
        patched_microsandbox([empty])
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "live")
        monkeypatch.setattr(doctor, "_claude_keychain_access_token", lambda: None)

        report = self._report(cfg)
        assert self._stale_checks(report) == [], "blank file is not a candidate"

    def test_harness_exposes_candidate_path_helper(self):
        """The harness MUST publish a ``claude_token_candidate_paths`` symbol
        so doctor can call it without re-deriving the precedence list. The
        signature is plain: (working_dir, explicit_file) → list of Path."""
        import odin.harnesses.microsandbox as msb
        assert hasattr(msb, "claude_token_candidate_paths"), (
            "MicrosandboxHarness must expose claude_token_candidate_paths() "
            "for doctor to probe every precedence-list path."
        )


# ── base_agent CLI probe (task #252) ──────────────────────────────────────
#
# A codex-only user has base_agent="claude" by default — doctor must surface
# the mismatch as a named check (not just a feature-matrix row) so the fix is
# obvious before any run crashes deep in a subprocess.


class TestBaseAgentProbe:
    """Doctor emits a named check when the configured base_agent's CLI is
    unavailable — the most common getting-started trap for a single-provider
    user."""

    def _run(self, cfg, *, base_cli_found, other_cli_found=True):
        """Run doctor with selective CLI availability."""
        def fake_cli(cli):
            if cli == "claude":
                return "/usr/bin/claude" if base_cli_found else None
            return "/usr/bin/x" if other_cli_found else None

        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", side_effect=fake_cli), \
             patch.object(doctor, "_agent_authenticated", return_value=(True, "ok")), \
             patch.object(doctor, "_msb_bin", return_value=None):
            return run_doctor(cfg)

    def _base_agent_checks(self, report):
        return [c for c in report.checks if "base_agent" in c.name]

    def test_no_check_when_base_agent_cli_available(self, cfg):
        report = self._run(cfg, base_cli_found=True)
        checks = self._base_agent_checks(report)
        assert all(c.status == PASS for c in checks), \
            "base_agent CLI available → no warning"

    def test_warns_when_base_agent_cli_missing(self, cfg):
        """base_agent=claude but claude not on PATH → WARN naming the fix."""
        report = self._run(cfg, base_cli_found=False, other_cli_found=True)
        checks = self._base_agent_checks(report)
        assert len(checks) >= 1
        chk = checks[0]
        assert chk.status == WARN
        assert chk.fix, "must include a fix command"
        assert "base_agent" in (chk.fix or "").lower() or "config" in (chk.fix or "").lower()

    def test_fix_names_available_alternative(self, cfg):
        """A codex-only user sees codex named as the alternative."""
        def fake_cli(cli):
            if cli == "codex":
                return "/usr/bin/codex"
            return None

        with patch.object(doctor, "_port_open", return_value=True), \
             patch.object(doctor, "_celery_running", return_value=True), \
             patch.object(doctor, "_cli_on_path", side_effect=fake_cli), \
             patch.object(doctor, "_agent_authenticated", return_value=(True, "ok")), \
             patch.object(doctor, "_msb_bin", return_value=None):
            report = run_doctor(cfg)
        checks = self._base_agent_checks(report)
        assert len(checks) >= 1
        chk = checks[0]
        assert "codex" in (chk.fix or "").lower(), \
            "fix should name an available alternative agent"
