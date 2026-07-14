"""Unit tests for the microsandbox sandbox harness (mocked — no real VM boot).

MicrosandboxHarness is a decorator harness (peer to ForkdHarness) that wraps an
inner CLI harness and runs its command inside a libkrun microVM via `msb run`.
These tests pin the decorator contract, the `msb` command construction, and the
TaskResult mapping. A real-VM smoke lives in tests/integration (msb required).
"""

import asyncio
import json
import subprocess
import sys
import time

import pytest

from odin.harnesses.microsandbox import MicrosandboxHarness
# forkd lives on a sibling integration line and may be absent here; the forkd
# backcompat test below skips when it is. Microsandbox itself never imports forkd.
try:
    from odin.harnesses.forkd import ForkdHarness
except ImportError:  # pragma: no cover - forkd module not present on this line
    ForkdHarness = None
from odin.harnesses.registry import get_harness
from odin.models import AgentConfig


def _cfg(**kw):
    base = dict(cli_command="codex", sandbox_mode="microsandbox")
    base.update(kw)
    return AgentConfig(**base)


# --- config model -----------------------------------------------------------

def test_agentconfig_has_microsandbox_fields():
    c = AgentConfig(
        sandbox_mode="microsandbox",
        microsandbox_image="odin-sbx:1",
        microsandbox_mem_size_mib=256,
        microsandbox_timeout_secs=120,
    )
    assert c.sandbox_mode == "microsandbox"
    assert c.microsandbox_image == "odin-sbx:1"
    assert c.microsandbox_mem_size_mib == 256
    assert c.microsandbox_timeout_secs == 120


# --- registry dispatch ------------------------------------------------------

def test_registry_wraps_when_sandbox_mode_microsandbox():
    h = get_harness("codex", _cfg())
    assert isinstance(h, MicrosandboxHarness)
    # decorator forces the execute() path; inner still yields a real CLI command
    assert h.build_execute_command("prompt", {}) is None
    assert h.inner.build_execute_command("prompt", {}) is not None


def test_name_has_microsandbox_suffix():
    h = get_harness("codex", _cfg())
    assert h.name.endswith("(microsandbox)")


def test_run_in_forkd_backcompat_still_wraps_forkd():
    # Existing configs using run_in_forkd must keep working unchanged.
    if ForkdHarness is None:
        pytest.skip("forkd module not present on this branch")
    h = get_harness("codex", AgentConfig(cli_command="codex", run_in_forkd=True))
    assert isinstance(h, ForkdHarness)


def test_sandbox_mode_none_returns_bare_harness():
    h = get_harness("codex", AgentConfig(cli_command="codex"))
    bare = not isinstance(h, MicrosandboxHarness)
    if ForkdHarness is not None:
        bare = bare and not isinstance(h, ForkdHarness)
    assert bare


def test_interactive_command_delegates_to_inner():
    # Planning runs on the host; only execution runs in the VM.
    h = get_harness("codex", _cfg())
    inner_cmd = h.inner.build_interactive_command("/tmp/sys.txt", {})
    assert h.build_interactive_command("/tmp/sys.txt", {}) == inner_cmd


def test_supports_system_prompt_flag_delegates_to_inner():
    # interactive.py reads this during host-side planning; the wrapper must
    # answer for its inner harness instead of raising AttributeError.
    h = get_harness("codex", _cfg())
    assert h.supports_system_prompt_flag == h.inner.supports_system_prompt_flag


# --- msb command construction ----------------------------------------------

def test_build_msb_command_structure():
    h = get_harness("codex", _cfg(
        microsandbox_timeout_secs=120,
        microsandbox_mem_size_mib=512,
        microsandbox_image="node:22-slim",
    ))
    cmd = h._build_msb_command(["claude", "-p", "hi"], "/host/wt")
    assert cmd[0].endswith("msb")
    assert cmd[1] == "run"
    # execution runs mount the workspace writable (:rw) so agents can commit
    assert "-v" in cmd and "/host/wt:/workspace:rw" in " ".join(cmd)
    assert "--workdir" in cmd and "/workspace" in cmd
    assert "--timeout" in cmd and "120s" in cmd
    assert "--memory" in cmd and "512M" in cmd
    # the inner command runs inside `sh -lc '<cmd> </dev/null'` (stdin closed in-guest),
    # after the image and the `--` separator
    dd = cmd.index("--")
    assert cmd[dd - 1] == "node:22-slim"
    assert cmd[dd + 1] == "sh" and cmd[dd + 2] == "-lc"
    assert cmd[dd + 3] == "claude -p hi </dev/null"


def test_build_msb_command_applies_net_policy():
    h = get_harness("codex", _cfg(
        microsandbox_net_default="deny",
        microsandbox_net_rules=["allow@public", "allow@api.anthropic.com:tcp:443"],
    ))
    cmd = h._build_msb_command(["echo", "hi"], "/wt")
    assert "--net-default" in cmd and "deny" in cmd
    assert cmd.count("--net-rule") == 2


# --- execute() → TaskResult mapping ----------------------------------------

_SUCCESS_STREAM = (
    '{"type":"result","result":"did the work\\n'
    '-------ODIN-STATUS-------\\nSUCCESS"}\n'
)


def test_execute_success_writes_trace_and_metadata(tmp_path, monkeypatch):
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(h, "_execute_sync", lambda prompt, context: _SUCCESS_STREAM)
    out = tmp_path / "t.out"
    trace = tmp_path / "t.trace.jsonl"
    result = asyncio.run(h.execute("p", {
        "output_file": str(out),
        "trace_file": str(trace),
    }))
    assert result.success is True
    assert result.metadata["sandbox"] == "microsandbox"
    assert result.agent.endswith("(microsandbox)")
    assert trace.read_text() == _SUCCESS_STREAM      # raw stream to trace
    assert out.read_text() == result.output          # extracted text to out


def test_execute_missing_status_fails(monkeypatch):
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(h, "_execute_sync", lambda prompt, context: "did stuff, no status")
    result = asyncio.run(h.execute("p", {}))
    assert result.success is False
    assert "ODIN-STATUS" in (result.error or "")


def test_execute_validate_status_false_succeeds(monkeypatch):
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(h, "_execute_sync", lambda prompt, context: "anything at all")
    result = asyncio.run(h.execute("p", {"validate_status": False}))
    assert result.success is True


def test_execute_wraps_exception_as_failure(monkeypatch):
    h = get_harness("codex", _cfg())

    def boom(prompt, context):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(h, "_execute_sync", boom)
    result = asyncio.run(h.execute("p", {}))
    assert result.success is False
    assert "kaboom" in (result.error or "")
    assert result.metadata["sandbox"] == "microsandbox"


# --- _execute_sync boundary -------------------------------------------------

def test_execute_sync_raises_when_inner_has_no_command(monkeypatch):
    # An API-only inner harness returns None → cannot be sandboxed this way.
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(h.inner, "build_execute_command", lambda prompt, context: None)
    with pytest.raises(RuntimeError):
        h._execute_sync("p", {})


def test_execute_sync_invokes_msb_with_worktree_mount(monkeypatch):
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(h.inner, "build_execute_command", lambda prompt, context: ["echo", "hi"])
    captured = []

    def fake_run(argv, **kw):
        captured.append({"argv": argv, "stdin": kw.get("stdin")})

        class R:
            stdout = "ok\n-------ODIN-STATUS-------\nSUCCESS"
            stderr = ""
            returncode = 0

        return R()

    monkeypatch.setattr("odin.harnesses.microsandbox.subprocess.run", fake_run)
    out = h._execute_sync("p", {"working_dir": "/host/wt"})
    assert "SUCCESS" in out
    # The first subprocess call is the `msb run` invocation (the run itself);
    # subsequent calls are cleanup. Inspect only the run argv below.
    run_call = next(c for c in captured if c["argv"][1] == "run")
    argv = run_call["argv"]
    assert "/host/wt:/workspace:rw" in " ".join(argv)
    # inner command runs inside `sh -lc`; codex gets its bypass flag injected
    dd = argv.index("--")
    assert argv[dd + 1] == "sh" and argv[dd + 2] == "-lc"
    guest_cmd = argv[dd + 3]
    assert "echo hi --dangerously-bypass-approvals-and-sandbox" in guest_cmd
    assert guest_cmd.endswith("</dev/null")  # stdin closed in-guest
    # host msb process also gets DEVNULL stdin
    assert run_call["stdin"] is subprocess.DEVNULL


# --- is_available -----------------------------------------------------------

def test_is_available_true_when_bin_and_inner(monkeypatch):
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(h, "_microsandbox_bin", lambda: "/usr/bin/msb")
    assert asyncio.run(h.is_available()) is True


def test_is_available_false_when_no_bin(monkeypatch):
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(h, "_microsandbox_bin", lambda: None)
    assert asyncio.run(h.is_available()) is False


# --- staging (snapshot / memory / bypass / creds / net) ---------------------

def test_snapshot_used_when_configured():
    h = get_harness("codex", _cfg(microsandbox_snapshot="odin-agents"))
    cmd = h._build_msb_command(["echo", "hi"], "/wt")
    assert "--snapshot" in cmd and "odin-agents" in cmd
    assert "node:22-slim" not in cmd  # snapshot replaces the positional image


def test_default_memory_is_4096():
    # opencode (bun) OOMs below ~4G — the default must be 4096, not 512.
    h = get_harness("codex", AgentConfig(cli_command="codex", sandbox_mode="microsandbox"))
    cmd = h._build_msb_command(["echo", "hi"], "/wt")
    assert "--memory" in cmd and "4096M" in cmd


@pytest.mark.parametrize("agent,cli,flag", [
    ("codex", "codex", "--dangerously-bypass-approvals-and-sandbox"),
    ("claude", "claude", "--dangerously-skip-permissions"),
    ("agy", "agy", "--dangerously-skip-permissions"),
])
def test_bypass_flags_injected_per_agent(agent, cli, flag):
    h = get_harness(agent, AgentConfig(cli_command=cli, sandbox_mode="microsandbox"))
    out = h._inject_bypass_flags(["run", "task"])
    assert flag in out
    # idempotent — not doubled if already present
    assert h._inject_bypass_flags(out).count(flag) == 1


@pytest.mark.parametrize("agent", ["glm", "minimax"])
def test_opencode_agents_get_no_bypass_flag(agent):
    # opencode `run` is non-interactive and rejects unknown flags → no injection.
    h = get_harness(agent, AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    assert h._inject_bypass_flags(["run", "task"]) == ["run", "task"]


def test_credential_mounts_glm_are_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config/opencode").mkdir(parents=True)
    (tmp_path / ".config/opencode/opencode.jsonc").write_text("{}")
    (tmp_path / ".local/share/opencode").mkdir(parents=True)
    (tmp_path / ".local/share/opencode/auth.json").write_text("{}")
    h = get_harness("glm", AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    joined = " ".join(h._credential_mounts())
    assert "/root/.config/opencode/opencode.jsonc:ro" in joined
    assert "/root/.local/share/opencode/auth.json:ro" in joined
    assert "account.json" not in joined  # absent on host → skipped


def test_credential_mounts_agy_never_mounts_host_files(tmp_path, monkeypatch):
    # agy's OAuth token is NOT in a file — it lives in the OS credential store
    # (macOS Keychain / Linux Secret Service). The guest reads it from a per-run
    # gnome-keyring the harness seeds via AGY_SECRET (see _extra_env + the guest
    # bootstrap), so agy mounts NOTHING: read-only mounts of ~/.gemini would also
    # block agy writing its own state dir in-guest. (#125 root cause: the old
    # file-mount design never supplied a token → "not logged into Antigravity".)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gemini").mkdir(parents=True)
    (tmp_path / ".antigravity").mkdir(parents=True)
    h = get_harness("agy", AgentConfig(cli_command="agy", sandbox_mode="microsandbox"))
    assert h._credential_mounts() == []


# --- agy Secret Service token staging ---------------------------------------

_AGY_JSON = '{"token":{"access_token":"ya29.TESTONLY","refresh_token":"1//rt"},"auth_method":"consumer"}'


def test_agy_token_env_override(monkeypatch):
    monkeypatch.setenv("AGY_SECRET", _AGY_JSON)
    h = get_harness("agy", AgentConfig(cli_command="agy", sandbox_mode="microsandbox"))
    assert h._agy_token() == _AGY_JSON


def test_agy_token_decodes_macos_keychain_go_keyring_base64(monkeypatch):
    # macOS go-keyring wraps the secret as `go-keyring-base64:<b64(JSON)>`. The
    # Linux guest's go-keyring stores the RAW value, so the harness must strip the
    # prefix and base64-decode before seeding — else agy reads the literal prefix.
    import base64
    monkeypatch.delenv("AGY_SECRET", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    wrapped = "go-keyring-base64:" + base64.b64encode(_AGY_JSON.encode()).decode()

    def fake_run(cmd, **kw):
        assert cmd[:2] == ["security", "find-generic-password"]
        assert "gemini" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=wrapped + "\n", stderr="")

    monkeypatch.setattr("odin.harnesses.microsandbox.subprocess.run", fake_run)
    h = get_harness("agy", AgentConfig(cli_command="agy", sandbox_mode="microsandbox"))
    assert h._agy_token() == _AGY_JSON


def test_agy_token_none_when_keychain_absent(monkeypatch):
    monkeypatch.delenv("AGY_SECRET", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    monkeypatch.setattr(
        "odin.harnesses.microsandbox.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found"),
    )
    h = get_harness("agy", AgentConfig(cli_command="agy", sandbox_mode="microsandbox"))
    assert h._agy_token() is None


def test_agy_extra_env_injects_secret(monkeypatch):
    monkeypatch.setenv("AGY_SECRET", _AGY_JSON)
    h = get_harness("agy", AgentConfig(cli_command="agy", sandbox_mode="microsandbox"))
    env = h._extra_env()
    assert f"AGY_SECRET={_AGY_JSON}" in env


def test_agy_guest_command_seeds_keyring_under_dbus(monkeypatch):
    # The agy guest command must (a) run under dbus-run-session so a private
    # session bus exists, (b) seed the token into gnome-keyring via secret-tool
    # BEFORE agy runs, and (c) still invoke agy with stdin closed.
    monkeypatch.setenv("AGY_SECRET", _AGY_JSON)
    h = get_harness("agy", AgentConfig(cli_command="agy", sandbox_mode="microsandbox"))
    cmd = h._build_msb_command(["agy", "-p", "hi", "--dangerously-skip-permissions"], "/wt")
    dd = cmd.index("--")
    assert cmd[dd + 1] == "dbus-run-session" and cmd[dd + 2] == "--"
    body = cmd[-1]
    assert "secret-tool store" in body and "service gemini username antigravity" in body
    assert "gnome-keyring-daemon" in body
    assert "agy -p hi --dangerously-skip-permissions </dev/null" in body
    # the secret itself is passed by env, never inlined into the command
    assert _AGY_JSON not in body


def test_non_agy_guest_command_has_no_keyring_bootstrap():
    h = get_harness("codex", _cfg())
    cmd = h._build_msb_command(["codex", "-p", "hi"], "/wt")
    dd = cmd.index("--")
    assert cmd[dd + 1] == "sh"  # plain shell, no dbus-run-session
    assert "secret-tool" not in cmd[-1]


def test_claude_macos_uses_env_token_not_file_mounts(monkeypatch):
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")
    h = get_harness("claude", AgentConfig(cli_command="claude", sandbox_mode="microsandbox"))
    assert h._credential_mounts() == []  # keychain on macOS → no file mounts
    env = h._extra_env()
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test" in env
    assert "IS_SANDBOX=1" in env  # allow --dangerously-skip-permissions as root in-VM


def _claude_cfg(**kw):
    return AgentConfig(cli_command="claude", sandbox_mode="microsandbox", **kw)


def test_claude_token_read_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    tf = tmp_path / ".claude-token"
    tf.write_text("sk-ant-oat-from-file\n")  # trailing newline stripped
    h = get_harness("claude", _claude_cfg(microsandbox_claude_token_file=str(tf)))
    assert h._claude_token() == "sk-ant-oat-from-file"
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-from-file" in h._extra_env()


def test_claude_token_env_wins_over_file(tmp_path, monkeypatch):
    (tmp_path / ".claude-token").write_text("from-file")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-env")
    h = get_harness("claude", _claude_cfg(microsandbox_claude_token_file=str(tmp_path / ".claude-token")))
    assert h._claude_token() == "from-env"


def test_claude_token_read_from_working_dir(tmp_path, monkeypatch):
    # The TaskIt UI writes .claude-token into board.working_dir == context["working_dir"].
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    monkeypatch.setattr(MicrosandboxHarness, "_claude_keychain_token", lambda self: None)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude-token").write_text("tok-from-workdir")
    h = get_harness("claude", _claude_cfg())
    assert h._claude_token({"working_dir": str(tmp_path)}) == "tok-from-workdir"


def test_claude_token_found_from_worktree_up_to_git_root(tmp_path, monkeypatch):
    # A worktree under the project root still finds the project's .claude-token.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    monkeypatch.setattr(MicrosandboxHarness, "_claude_keychain_token", lambda self: None)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude-token").write_text("root-tok")
    wt = tmp_path / "sub" / "wt"
    wt.mkdir(parents=True)
    h = get_harness("claude", _claude_cfg())
    assert h._claude_token({"working_dir": str(wt)}) == "root-tok"


def test_claude_no_token_yields_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    monkeypatch.setattr(MicrosandboxHarness, "_claude_keychain_token", lambda self: None)
    monkeypatch.chdir(tmp_path)          # no ./.claude-token
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.claude-token or ~/.odin/.claude-token
    h = get_harness("claude", _claude_cfg(microsandbox_claude_token_file=str(tmp_path / "absent")))
    assert h._claude_token() is None
    env = h._extra_env()
    assert not any("CLAUDE_CODE_OAUTH_TOKEN" in e for e in env)  # no token injected
    assert "IS_SANDBOX=1" in env  # still marks the VM as a sandbox


# --- live trace streaming (guest tee → bind-mounted trace file) ---------------
# msb buffers guest stdout and DISCARDS it on timeout-kill (proven live 2026-07-04),
# so the canonical trace file is bind-mounted into the guest and tee'd from inside;
# the host mirrors extracted text into .out for `odin logs -f`.

def _fake_msb_run(monkeypatch, side_effect=None, rc=0, stdout=""):
    """Patch subprocess.run, capturing argv; side_effect(cmd) simulates the guest
    writing through the bind mount before msb exits."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if side_effect:
            side_effect(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")

    monkeypatch.setattr("odin.harnesses.microsandbox.subprocess.run", fake_run)
    return calls


def test_live_trace_mounted_and_teed(tmp_path, monkeypatch):
    trace = tmp_path / "t.trace.jsonl"
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"trace_file": str(trace), "working_dir": str(tmp_path)})
    cmd = calls[0]
    assert f"{trace}:/odin-trace.jsonl" in " ".join(cmd)  # rw bind mount, canonical file
    assert "bash" in cmd  # pipefail needs bash, not dash
    guest_cmd = cmd[-1]
    assert "set -o pipefail" in guest_cmd
    assert "| tee /odin-trace.jsonl" in guest_cmd
    assert "</dev/null" in guest_cmd  # stdin still closed in-guest


def test_no_trace_file_keeps_plain_sh_path(tmp_path, monkeypatch):
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    cmd = calls[0]
    assert "sh" in cmd and "tee" not in cmd[-1]


def test_live_trace_file_is_the_output_source(tmp_path, monkeypatch):
    # stdout arrives empty (msb buffered/dropped it) — the mounted file is truth.
    trace = tmp_path / "t.trace.jsonl"

    def guest_writes(cmd):
        trace.write_text("HELLO FROM GUEST\n")

    _fake_msb_run(monkeypatch, side_effect=guest_writes, stdout="")
    h = get_harness("codex", _cfg())
    raw = h._execute_sync("p", {"trace_file": str(trace), "working_dir": str(tmp_path)})
    assert "HELLO FROM GUEST" in raw


def test_live_out_mirrors_extracted_text(tmp_path, monkeypatch):
    trace = tmp_path / "t.trace.jsonl"
    out = tmp_path / "t.out"

    def guest_writes(cmd):
        trace.write_text("LIVE LINE\n")

    _fake_msb_run(monkeypatch, side_effect=guest_writes)
    h = get_harness("codex", _cfg())
    h._execute_sync(
        "p",
        {"trace_file": str(trace), "output_file": str(out), "working_dir": str(tmp_path)},
    )
    assert "LIVE LINE" in out.read_text()


def test_partial_trace_survives_host_timeout(tmp_path, monkeypatch):
    # The whole point: a killed VM must not cost us the agent's transcript.
    trace = tmp_path / "t.trace.jsonl"

    def guest_writes_then_hangs(cmd):
        trace.write_text("PARTIAL WORK BEFORE KILL\n")
        raise subprocess.TimeoutExpired(cmd, 1)

    _fake_msb_run(monkeypatch, side_effect=guest_writes_then_hangs)
    h = get_harness("codex", _cfg())
    result = asyncio.run(
        h.execute("p", {"trace_file": str(trace), "working_dir": str(tmp_path)})
    )
    assert result.success is False
    assert "PARTIAL WORK BEFORE KILL" in result.output  # nothing lost
    assert "host timeout" in result.output  # marker explains the kill


def _opencode_inner_cmd(prompt, context):
    return ["opencode", "run", "--format", "json", "-m", "zai-coding-plan/glm-4.7", "p"]


def test_live_trace_injects_print_logs_for_opencode(tmp_path, monkeypatch):
    # opencode's `run --format json` prints only a final blob — inject --print-logs
    # (proven live: INFO logs arrive within seconds) so the tee channel shows progress.
    trace = tmp_path / "t.trace.jsonl"
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("glm", AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    monkeypatch.setattr(h.inner, "build_execute_command", _opencode_inner_cmd)
    h._execute_sync("p", {"trace_file": str(trace), "working_dir": str(tmp_path)})
    guest_cmd = calls[0][-1]
    assert "--print-logs" in guest_cmd
    assert guest_cmd.index("run") < guest_cmd.index("--print-logs")


def test_no_print_logs_without_live_trace(tmp_path, monkeypatch):
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("glm", AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    monkeypatch.setattr(h.inner, "build_execute_command", _opencode_inner_cmd)
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    assert "--print-logs" not in calls[0][-1]


def test_no_print_logs_for_non_opencode_agents(tmp_path, monkeypatch):
    trace = tmp_path / "t.trace.jsonl"
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"trace_file": str(trace), "working_dir": str(tmp_path)})
    assert "--print-logs" not in calls[0][-1]


# --- linked git worktrees (odin task workspaces) ------------------------------
# A linked worktree's .git is a FILE pointing into the main repo's .git — with
# only the worktree mounted, git is dead in-guest ("fatal: not a git repository",
# proven live: glm spun for 30 min twice on task #102). Fix: mount the worktree
# AND the main repo's .git at their identical host paths; skip path rewriting.

def _make_linked_worktree(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git" / "worktrees" / "42").mkdir(parents=True)
    wt = tmp_path / "wt42"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {repo}/.git/worktrees/42\n")
    return repo, wt


def test_linked_worktree_mounts_git_at_host_paths(tmp_path, monkeypatch):
    repo, wt = _make_linked_worktree(tmp_path)
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(wt)})
    cmd = calls[0]
    joined = " ".join(cmd)
    assert f"{wt}:{wt}" in joined                      # worktree at its host path
    assert f"{repo}/.git:{repo}/.git" in joined        # main .git resolves gitdir
    wd = cmd[cmd.index("--workdir") + 1]
    assert wd == str(wt)                               # workdir = host path
    assert "/workspace" not in joined                  # no synthetic mount


def test_linked_worktree_skips_path_rewrite(tmp_path, monkeypatch):
    repo, wt = _make_linked_worktree(tmp_path)
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    monkeypatch.setattr(
        h.inner, "build_execute_command",
        lambda p, c: ["codex", "exec", f"--cd={wt}", "prompt"],
    )
    h._execute_sync("p", {"working_dir": str(wt)})
    assert f"--cd={wt}" in calls[0][-1]                # host path preserved in-guest


def test_plain_dir_still_uses_workspace_mount(tmp_path, monkeypatch):
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    joined = " ".join(calls[0])
    assert f"{tmp_path}:/workspace" in joined
    assert calls[0][calls[0].index("--workdir") + 1] == "/workspace"


# --- claude keychain resolution (macOS) — the live CLI credential ------------

def _kc_payload(token="sk-ant-oat01-kc-live", ttl_ms=3_600_000):
    return json.dumps({"claudeAiOauth": {
        "accessToken": token,
        "refreshToken": "sk-ant-ort01-x",
        "expiresAt": int(time.time() * 1000) + ttl_ms,
    }})


def _mock_keychain(monkeypatch, payload="", rc=0):
    """Patch the `security find-generic-password` call; returns the call log."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        assert cmd[0] == "security"
        return subprocess.CompletedProcess(cmd, rc, stdout=payload, stderr="")

    monkeypatch.setattr("odin.harnesses.microsandbox.subprocess.run", fake_run)
    return calls


def test_claude_token_from_keychain(tmp_path, monkeypatch):
    # macOS default: no env, no files → the logged-in CLI's live keychain token.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    _mock_keychain(monkeypatch, _kc_payload())
    h = get_harness("claude", _claude_cfg())
    assert h._claude_token() == "sk-ant-oat01-kc-live"
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-kc-live" in h._extra_env()


def test_claude_keychain_wins_over_discovered_file(tmp_path, monkeypatch):
    # A hand-pasted .claude-token can go stale (401s) while the keychain stays
    # live — the keychain outranks *discovered* files (not the explicit config).
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude-token").write_text("stale-pasted-token")
    _mock_keychain(monkeypatch, _kc_payload())
    h = get_harness("claude", _claude_cfg())
    assert h._claude_token({"working_dir": str(tmp_path)}) == "sk-ant-oat01-kc-live"


def test_claude_explicit_config_file_wins_over_keychain(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    tf = tmp_path / ".claude-token"
    tf.write_text("explicitly-configured")
    _mock_keychain(monkeypatch, _kc_payload())
    h = get_harness("claude", _claude_cfg(microsandbox_claude_token_file=str(tf)))
    assert h._claude_token() == "explicitly-configured"


def test_claude_expired_keychain_falls_back_to_file(tmp_path, monkeypatch):
    # expiresAt inside the 60s buffer → treated as expired → discovered file used.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude-token").write_text("file-fallback")
    _mock_keychain(monkeypatch, _kc_payload(ttl_ms=30_000))
    h = get_harness("claude", _claude_cfg())
    assert h._claude_token({"working_dir": str(tmp_path)}) == "file-fallback"


@pytest.mark.parametrize("payload,rc", [("", 1), ("not-json", 0)])
def test_claude_keychain_unreadable_falls_back_to_file(tmp_path, monkeypatch, payload, rc):
    # Locked keychain / headless daemon / garbage payload → fall through silently.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "darwin")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude-token").write_text("file-fallback")
    _mock_keychain(monkeypatch, payload, rc)
    h = get_harness("claude", _claude_cfg())
    assert h._claude_token({"working_dir": str(tmp_path)}) == "file-fallback"


def test_claude_keychain_not_consulted_off_macos(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("odin.harnesses.microsandbox.sys.platform", "linux")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude-token").write_text("linux-file-token")
    calls = _mock_keychain(monkeypatch, _kc_payload())
    h = get_harness("claude", _claude_cfg())
    assert h._claude_token({"working_dir": str(tmp_path)}) == "linux-file-token"
    assert calls == []  # `security` is macOS-only — never invoked


def test_net_opens_egress_when_mcp_present(tmp_path, monkeypatch):
    # F31: any --net-rule kills provider DNS (domain-gated forwarder), so host
    # access is granted by opening default egress, not by an IP allowlist.
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"taskit": {"url": "http://127.0.0.1:9100"}}')
    h = get_harness("glm", AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    flags = h._net_flags({"mcp_config": str(mcp)})
    assert flags == ["--net-default-egress", "allow"]
    assert "--net-rule" not in flags  # rules would break provider DNS


def test_net_opens_egress_when_taskit_base_url_present(monkeypatch):
    # The orchestrator sets context["taskit_base_url"] for microsandbox+taskit
    # tasks (opencode-family configs are workspace-discovered, so there is no
    # mcp_config path in context) — that alone must open host egress.
    h = get_harness("glm", AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    flags = h._net_flags({"taskit_base_url": "http://127.0.0.1:9100"})
    assert flags == ["--net-default-egress", "allow"]


def test_mcp_config_staged_outside_guest_tmpfs(tmp_path):
    # The guest's /tmp is a tmpfs that shadows any bind-mount under it, so an MCP
    # config staged to /tmp/... is invisible to the agent (claude aborts with
    # "MCP config file not found"). The staged guest path + its mount must live
    # outside /tmp, and the CLI flag must be rewritten to that same path.
    host_cfg = tmp_path / "mcp_42.json"
    host_cfg.write_text('{"mcpServers": {"taskit": {"url": "http://127.0.0.1:9100"}}}')
    h = get_harness("claude", AgentConfig(cli_command="claude", sandbox_mode="microsandbox"))
    cmd_in = ["claude", "-p", "hi", "--mcp-config", str(host_cfg)]
    cmd_out, mounts = h._stage_mcp_config({"mcp_config": str(host_cfg)}, cmd_in, str(tmp_path))
    guest_path = cmd_out[cmd_out.index("--mcp-config") + 1]
    assert not guest_path.startswith("/tmp/"), f"MCP staged under guest tmpfs: {guest_path}"
    assert str(host_cfg) not in cmd_out  # host path rewritten to the guest path
    mount_spec = " ".join(mounts)
    assert guest_path in mount_spec and ":ro" in mount_spec  # mounted read-only at that path
    assert "/tmp/" not in mount_spec


def test_user_configured_net_rules_still_win(monkeypatch):
    h = get_harness("glm", AgentConfig(
        cli_command="opencode", sandbox_mode="microsandbox",
        microsandbox_net_rules=["allow@api.z.ai", "allow@192.168.0.5"],
    ))
    flags = h._net_flags({"taskit_base_url": "http://127.0.0.1:9100"})
    assert flags == ["--net-rule", "allow@api.z.ai", "--net-rule", "allow@192.168.0.5"]


def test_guest_reachable_url_rewrites_loopback():
    from odin.harnesses.microsandbox import guest_reachable_url

    assert guest_reachable_url("http://127.0.0.1:9100", "10.0.0.7") == "http://10.0.0.7:9100"
    assert guest_reachable_url("http://localhost:9100", "10.0.0.7") == "http://10.0.0.7:9100"
    # already-reachable URLs pass through untouched
    assert guest_reachable_url("http://192.168.0.9:9100", "10.0.0.7") == "http://192.168.0.9:9100"


def test_guest_reachable_url_no_ip_leaves_url(monkeypatch):
    import odin.harnesses.microsandbox as msb

    monkeypatch.setattr(msb, "_detect_host_ip", lambda: None)
    assert msb.guest_reachable_url("http://127.0.0.1:9100") == "http://127.0.0.1:9100"


def test_no_net_rules_without_mcp():
    # Default (no MCP) leaves egress at msb's default (public allowed) — no rules added.
    h = get_harness("glm", AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    assert h._net_flags({}) == []


def test_relative_trace_file_is_absolutized_for_mount(tmp_path, monkeypatch):
    # Reflection passes a RELATIVE trace path; msb parses a relative -v source
    # as a NAMED VOLUME and refuses to boot ("invalid config: volume name…" —
    # killed every sandboxed reflection). The harness must mount absolute paths.
    monkeypatch.chdir(tmp_path)
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"trace_file": ".odin/logs/reflect_9.trace.jsonl",
                          "working_dir": str(tmp_path)})
    joined = " ".join(calls[0])
    assert f"{tmp_path}/.odin/logs/reflect_9.trace.jsonl:/odin-trace.jsonl" in joined
    assert " .odin/logs" not in joined  # no relative mount source anywhere


# --- reflection read-only mounts (reviewers must never write to the repo) ------
# A reflection/reviewer run sets context["read_only_workspace"] = True so every
# repo/workspace mount is bind-mounted :ro — the reviewer can READ the worktree,
# trace, and diff but cannot mutate the repo. Execution runs (default) stay :rw
# so agents can still commit in their worktree.

def test_workspace_mounts_read_only_plain_dir(tmp_path):
    h = get_harness("codex", _cfg())
    mounts, guest = h._workspace_mounts(str(tmp_path), read_only=True)
    joined = " ".join(mounts)
    assert f"{tmp_path}:/workspace:ro" in joined
    assert guest == "/workspace"


def test_workspace_mounts_read_only_linked_worktree(tmp_path):
    repo, wt = _make_linked_worktree(tmp_path)
    h = get_harness("codex", _cfg())
    mounts, guest = h._workspace_mounts(str(wt), read_only=True)
    joined = " ".join(mounts)
    assert f"{wt}:{wt}:ro" in joined
    assert f"{repo}/.git:{repo}/.git:ro" in joined
    assert guest == str(wt)


def test_workspace_mounts_default_is_writable_plain_dir(tmp_path):
    # Execution runs (no read_only) keep the worktree writable so agents commit.
    h = get_harness("codex", _cfg())
    mounts, _ = h._workspace_mounts(str(tmp_path))
    joined = " ".join(mounts)
    assert f"{tmp_path}:/workspace:rw" in joined
    assert ":ro" not in joined


def test_workspace_mounts_default_is_writable_linked_worktree(tmp_path):
    repo, wt = _make_linked_worktree(tmp_path)
    h = get_harness("codex", _cfg())
    mounts, _ = h._workspace_mounts(str(wt))
    joined = " ".join(mounts)
    assert f"{wt}:{wt}:rw" in joined
    assert f"{repo}/.git:{repo}/.git:rw" in joined


def test_build_msb_command_read_only_from_context(tmp_path):
    h = get_harness("codex", _cfg())
    cmd = h._build_msb_command(
        ["echo", "hi"], str(tmp_path),
        context={"read_only_workspace": True},
    )
    joined = " ".join(cmd)
    assert f"{tmp_path}:/workspace:ro" in joined


def test_build_msb_command_default_writable_without_flag(tmp_path):
    h = get_harness("codex", _cfg())
    cmd = h._build_msb_command(["echo", "hi"], str(tmp_path))
    joined = " ".join(cmd)
    assert f"{tmp_path}:/workspace:rw" in joined


def test_reflection_run_mounts_every_repo_path_read_only(tmp_path, monkeypatch):
    # GUARD: if any repo/workspace mount in a reflection run is ever writable
    # again, this test fails. Uses the full _execute_sync path (the same one
    # reflect_task drives) so it pins the real mount flags, not a hand-built
    # subset. Both the worktree and its backing main-repo .git must be :ro.
    repo, wt = _make_linked_worktree(tmp_path)
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {
        "working_dir": str(wt),
        "read_only_workspace": True,  # reflection context
    })
    cmd = calls[0]
    repo_paths = [str(wt), f"{repo}/.git"]
    repo_mounts = []
    for i, token in enumerate(cmd):
        if token == "-v":
            mount = cmd[i + 1]
            src = mount.split(":")[0]
            if src in repo_paths:
                repo_mounts.append(mount)
    assert repo_mounts, "expected at least one repo mount in the msb command"
    for mount in repo_mounts:
        assert mount.endswith(":ro"), f"reflection repo mount is writable: {mount}"


def test_execution_run_keeps_worktree_writable(tmp_path, monkeypatch):
    # Execution runs (no read_only flag) must stay :rw so agents can commit.
    repo, wt = _make_linked_worktree(tmp_path)
    calls = _fake_msb_run(monkeypatch)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(wt)})
    joined = " ".join(calls[0])
    assert f"{wt}:{wt}:rw" in joined
    assert f"{repo}/.git:{repo}/.git:rw" in joined


# --- per-task XDG isolation for opencode-family agents ----------------------


@pytest.mark.parametrize("agent", ["glm", "minimax"])
def test_opencode_family_gets_isolated_xdg_env(agent, tmp_path, monkeypatch):
    """opencode-family agents (glm/minimax) must receive per-task XDG_DATA_HOME
    and XDG_CONFIG_HOME pointing into the task's own run directory so concurrent
    dispatches don't race on the shared SQLite databases."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config/opencode").mkdir(parents=True)
    (tmp_path / ".config/opencode/opencode.jsonc").write_text("{}")
    (tmp_path / ".local/share/opencode").mkdir(parents=True)
    (tmp_path / ".local/share/opencode/auth.json").write_text("{}")
    h = get_harness(agent, AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    calls = _fake_msb_run(monkeypatch)
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    joined = " ".join(calls[0])
    assert "XDG_DATA_HOME=" in joined
    assert "XDG_CONFIG_HOME=" in joined


@pytest.mark.parametrize("agent", ["glm", "minimax"])
def test_opencode_xdg_paths_are_per_task_unique(agent, tmp_path, monkeypatch):
    """Two opencode-family tasks dispatched in the same tick must get different
    XDG host directories — each _execute_sync call creates its own tmpdir, so
    the bind-mount sources (and therefore the guest writable XDG dirs) are distinct."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config/opencode").mkdir(parents=True)
    (tmp_path / ".config/opencode/opencode.jsonc").write_text("{}")
    (tmp_path / ".local/share/opencode").mkdir(parents=True)
    (tmp_path / ".local/share/opencode/auth.json").write_text("{}")
    h = get_harness(agent, AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    calls = _fake_msb_run(monkeypatch)
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    # Task 154's run-scoped cleanup interleaves extra msb invocations (sandbox
    # remove / orphan sweep) between runs — count only actual `run` commands.
    run_calls = [c for c in calls if "run" in c[:3]]
    assert len(run_calls) >= 2, f"expected 2 run invocations, saw {len(run_calls)}"
    cmd_a, cmd_b = run_calls[0], run_calls[1]
    xdg_mounts_a = [cmd_a[i + 1] for i, t in enumerate(cmd_a)
                    if t == "-v" and "odin-xdg" in cmd_a[i + 1] and cmd_a[i + 1].endswith(":rw")]
    xdg_mounts_b = [cmd_b[i + 1] for i, t in enumerate(cmd_b)
                    if t == "-v" and "odin-xdg" in cmd_b[i + 1] and cmd_b[i + 1].endswith(":rw")]
    assert len(xdg_mounts_a) == 2, f"expected 2 xdg bind-mounts, got {len(xdg_mounts_a)}"
    assert len(xdg_mounts_b) == 2, f"expected 2 xdg bind-mounts, got {len(xdg_mounts_b)}"
    srcs_a = [m.split(":")[0] for m in xdg_mounts_a]
    srcs_b = [m.split(":")[0] for m in xdg_mounts_b]
    assert srcs_a != srcs_b, "two dispatches must mount from different host directories"


@pytest.mark.parametrize("agent", ["claude", "codex", "agy", "gemini"])
def test_non_opencode_agents_no_xdg_isolation(agent, tmp_path, monkeypatch):
    """Only opencode-family agents need XDG isolation — other agents must not
    receive XDG env vars (they either don't use SQLite or already isolate)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    h = get_harness(agent, AgentConfig(cli_command=agent, sandbox_mode="microsandbox"))
    cmd = h._build_msb_command([agent, "run", "hi"], str(tmp_path))
    joined = " ".join(cmd)
    assert "XDG_DATA_HOME=" not in joined
    assert "XDG_CONFIG_HOME=" not in joined


def test_opencode_xdg_credential_mounts_still_readonly(tmp_path, monkeypatch):
    """Credential files for opencode-family agents must still mount read-only
    into the guest, even with XDG isolation active."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config/opencode").mkdir(parents=True)
    (tmp_path / ".config/opencode/opencode.jsonc").write_text("{}")
    (tmp_path / ".local/share/opencode").mkdir(parents=True)
    (tmp_path / ".local/share/opencode/auth.json").write_text("{}")
    h = get_harness("glm", AgentConfig(cli_command="opencode", sandbox_mode="microsandbox"))
    calls = _fake_msb_run(monkeypatch)
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    joined = " ".join(calls[0])
    assert ":ro" in joined
    assert "opencode.jsonc" in joined or "auth.json" in joined
