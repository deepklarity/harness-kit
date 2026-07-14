"""Tests for forkd harness wiring."""

import json
import subprocess
import tarfile
import time
from pathlib import Path
from odin.harnesses.forkd import ForkdHarness, _AssetServer, _CREDENTIAL_PATHS, _SANDBOX_BYPASS_FLAGS
from odin.harnesses.registry import get_harness
from odin.models import AgentConfig


def test_registry_wraps_harness_when_forkd_enabled():
    harness = get_harness("codex", AgentConfig(cli_command="codex", run_in_forkd=True))

    assert isinstance(harness, ForkdHarness)
    assert harness.build_execute_command("prompt", {}) is None
    assert harness.inner.build_execute_command("prompt", {}) is not None


def test_workspace_tar_respects_default_excludes(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "keep.txt").write_text("keep")
    (work / ".git").mkdir()
    (work / ".git" / "config").write_text("git")
    (work / "node_modules").mkdir()
    (work / "node_modules" / "pkg.js").write_text("pkg")

    out = tmp_path / "workspace.tgz"
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), AgentConfig())
    harness._make_workspace_tar(work, out)

    with tarfile.open(out, "r:gz") as tf:
        names = set(tf.getnames())

    assert "keep.txt" in names
    assert ".git/config" not in names
    assert "node_modules/pkg.js" not in names




def test_workspace_restore_respects_excludes(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".odin").mkdir()
    (work / ".odin" / "config.yaml").write_text("host-config")
    (work / ".gemini").mkdir()
    (work / ".gemini" / "settings.json").write_text("host-gemini")
    (work / ".mcp.json").write_text("host-mcp")
    (work / "opencode.json").write_text("host-opencode")

    result = tmp_path / "result.tgz"
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / ".odin").mkdir()
    (staged / ".odin" / "config.yaml").write_text("sandbox-config")
    (staged / ".gemini").mkdir()
    (staged / ".gemini" / "settings.json").write_text("sandbox-gemini")
    (staged / ".mcp.json").write_text("sandbox-mcp")
    (staged / "opencode.json").write_text("sandbox-opencode")
    (staged / "app.js").write_text("sandbox-app")
    with tarfile.open(result, "w:gz") as tf:
        for path in staged.rglob("*"):
            tf.add(path, arcname=path.relative_to(staged).as_posix(), recursive=False)

    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), AgentConfig())
    harness._restore_workspace_tar(work, result)

    assert (work / ".odin" / "config.yaml").read_text() == "host-config"
    assert (work / ".gemini" / "settings.json").read_text() == "host-gemini"
    assert (work / ".mcp.json").read_text() == "host-mcp"
    assert (work / "opencode.json").read_text() == "host-opencode"
    assert (work / "app.js").read_text() == "sandbox-app"




def test_asset_server_appends_live_stdout_to_trace_and_output(tmp_path):
    import urllib.request

    assets = tmp_path / "assets"
    assets.mkdir()
    trace_file = tmp_path / "task.trace.jsonl"
    output_file = tmp_path / "task.out"
    server = _AssetServer(assets, bind_host="127.0.0.1", trace_file=str(trace_file), output_file=str(output_file))
    server.start()
    try:
        data = b'{"type":"message","role":"assistant","content":"hello live"}\n'
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/__odin_trace/stdout",
            data=data,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).close()
    finally:
        server.stop()

    assert trace_file.read_text() == data.decode()
    assert output_file.read_text() == "hello live"

def test_execute_writes_output_and_trace_files(tmp_path, monkeypatch):
    cfg = AgentConfig(cli_command="codex", run_in_forkd=True)
    harness = get_harness("codex", cfg)
    monkeypatch.setattr(harness, "_execute_sync", lambda prompt, context: '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n')

    output_file = tmp_path / "task.out"
    trace_file = tmp_path / "task.trace.jsonl"
    import asyncio
    result = asyncio.run(harness.execute("prompt", {
        "output_file": str(output_file),
        "trace_file": str(trace_file),
        "validate_status": False,
    }))

    assert result.success is True
    assert trace_file.read_text().startswith('{"type":"item.completed"')
    assert output_file.read_text() == result.output


def test_glm_forkd_adds_final_status_reminder(monkeypatch):
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)
    captured = {}

    def fake_execute(prompt, context):
        captured["prompt"] = prompt
        return json.dumps({"type": "text", "text": "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nok"}) + "\n"

    monkeypatch.setattr(harness, "_execute_sync", fake_execute)
    import asyncio
    result = asyncio.run(harness.execute("do work", {}))

    assert result.success is True
    assert "GLM FORKD COMPLETION REQUIREMENT" in captured["prompt"]
    assert "Do not call another tool after the block" in captured["prompt"]


def test_glm_forkd_synthesizes_status_after_successful_proof(monkeypatch):
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)
    proof_args = {
        "comment_type": "proof",
        "content": "Implemented filter tabs and verified screenshots.",
        "screenshot_paths": ["/tmp/proof.png"],
    }
    raw = "\n".join([
        json.dumps({
            "type": "tool",
            "tool": "taskit_add_comment",
            "arguments": json.dumps(proof_args),
            "output": json.dumps({"comment_id": 3713, "screenshots_attached": 1}),
        }),
        json.dumps({
            "type": "step_finish",
            "part": {
                "reason": "stop",
                "tokens": {"input": 848, "output": 1, "reasoning": 57, "total": 19082},
            },
        }),
    ])
    monkeypatch.setattr(harness, "_execute_sync", lambda prompt, context: raw)

    import asyncio
    result = asyncio.run(harness.execute("do work", {}))

    assert result.success is True
    assert result.error is None
    assert "-------ODIN-STATUS-------" in result.output
    assert "SUCCESS" in result.output
    assert "Implemented filter tabs" in result.output
    assert result.metadata["glm_status_synthesized"] is True


def test_glm_forkd_missing_status_error_includes_raw_tail(monkeypatch):
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)
    raw = "\n".join([
        json.dumps({"type": "text", "text": "Proof submitted to TaskIt."}),
        json.dumps({
            "type": "step_finish",
            "part": {
                "reason": "stop",
                "tokens": {"input": 848, "output": 1, "reasoning": 57, "total": 19082},
            },
        }),
    ])
    monkeypatch.setattr(harness, "_execute_sync", lambda prompt, context: raw)

    import asyncio
    result = asyncio.run(harness.execute("do work", {}))

    assert result.success is False
    assert "GLM raw output tail" in result.error
    assert "Proof submitted to TaskIt" in result.error
    assert "last step_finish: reason=stop" in result.error
    assert "output=1" in result.error


def test_run_guest_script_uses_forkd_exec_timeout(monkeypatch, tmp_path):
    cfg = AgentConfig(cli_command="codex", run_in_forkd=True)
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)
    calls = []

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            calls.append(("terminate",))

        def wait(self, timeout=None):
            calls.append(("wait", timeout))

    def fake_run(forkd, args, *, scripts_dir, timeout, check=True):
        calls.append(tuple(args))
        import subprocess
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(harness, "_run_forkd_command", fake_run)
    monkeypatch.setattr(harness, "_start_fork_process", lambda forkd, tag, scripts_dir, settle_secs: FakeProc())
    monkeypatch.setattr(harness, "_wait_for_guest_agent", lambda forkd, scripts_dir, timeout: None)

    harness._run_guest_script(
        forkd=["forkd"],
        kernel=tmp_path / "vmlinux",
        scripts_dir=None,
        rootfs=tmp_path / "rootfs.ext4",
        script_command="echo hi",
        timeout=123,
    )

    assert any(call[:1] == ("snapshot",) for call in calls)
    assert any(call[:3] == ("exec", "--target", "10.42.0.2:8888") and "123" in call for call in calls)
    assert any(call[:1] == ("rmi",) for call in calls)



def test_controller_mode_spawns_per_child_netns(monkeypatch, tmp_path):
    cfg = AgentConfig(cli_command="codex", run_in_forkd=True, forkd_mode="controller")
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)
    calls = []

    def fake_controller(method, path, *, json=None, timeout, check=True):
        calls.append((method, path, json))
        if method == "GET" and path == "/v1/snapshots/odin-node-22-slim/info":
            return {"memory_logical_bytes": 4096 * 1024 * 1024}
        if method == "GET" and path == "/v1/snapshots":
            return [{"tag": "odin-node-22-slim"}]
        if method == "POST" and path == "/v1/sandboxes":
            return [{"id": "sb-1", "netns": "forkd-child-1"}]
        if method == "POST" and path == "/v1/sandboxes/sb-1/ping":
            return {"pong": True}
        if method == "POST" and path == "/v1/sandboxes/sb-1/exec":
            if json and json.get("args") == ["sh", "-lc", "grep MemTotal /proc/meminfo"]:
                return {"stdout": "MemTotal:        4194304 kB\n", "stderr": "", "exit_code": 0}
            return {"stdout": "ok", "stderr": "", "exit_code": 0}
        return None

    monkeypatch.setattr(harness, "_controller_request", fake_controller)
    proc = harness._run_guest_script_controller(
        forkd=["forkd"],
        kernel=tmp_path / "vmlinux",
        scripts_dir=None,
        rootfs=tmp_path / "rootfs.ext4",
        script_command="echo ok",
        timeout=77,
    )

    spawn = next(call for call in calls if call[1] == "/v1/sandboxes")
    exec_call = next(call for call in reversed(calls) if call[1] == "/v1/sandboxes/sb-1/exec" and call[2].get("timeout_secs") == 77)
    assert spawn[2]["per_child_netns"] is True
    assert spawn[2]["n"] == 1
    assert exec_call[2]["timeout_secs"] == 77
    assert proc.stdout == "ok"


def test_forkd_maps_planning_output_paths(tmp_path):
    cfg = AgentConfig(cli_command="codex", run_in_forkd=True)
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)
    host_plan = tmp_path / ".odin" / "plans" / "plan_sp_test.json"
    cmd = ["codex", "exec", f"Write your final plan as a JSON array to: `{host_plan}`"]

    mappings = harness._extract_output_path_mappings(cmd)
    rewritten = harness._rewrite_mapped_paths(cmd, mappings)

    assert mappings == [(host_plan, "/tmp/odin-forkd-mapped/0/plan_sp_test.json")]
    assert str(host_plan) not in rewritten[-1]
    assert "/tmp/odin-forkd-mapped/0/plan_sp_test.json" in rewritten[-1]


def test_restore_mapped_outputs(tmp_path):
    cfg = AgentConfig(cli_command="codex", run_in_forkd=True)
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "mapped-output-0").write_text('[{"id":"task_1"}]')
    host_plan = tmp_path / "host" / ".odin" / "plans" / "plan.json"

    harness._restore_mapped_outputs(assets, [(host_plan, "/guest/plan.json")])

    assert host_plan.read_text() == '[{"id":"task_1"}]'


def test_opencode_credential_paths_include_local_share():
    for agent in ("glm", "minimax"):
        paths = _CREDENTIAL_PATHS.get(agent, [])
        assert ".local/share/opencode/auth.json" in paths, f"{agent} missing auth.json path"
        assert ".local/share/opencode/account.json" in paths, f"{agent} missing account.json path"
    assert ".local/share/kilo/auth.json" in _CREDENTIAL_PATHS["minimax"]


def test_api_key_env_block_includes_gemini_aliases():
    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True, api_key="secret")
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)

    block = harness._api_key_env_block()

    assert "export GEMINI_API_KEY=secret" in block
    assert "export GEMINIAPIKEY=secret" in block




def test_host_home_ignores_ambient_sandbox_home(monkeypatch, tmp_path):
    real_home = tmp_path / "real-home"
    monkeypatch.setenv("HOME", "/tmp/odin-home")
    monkeypatch.delenv("ODIN_HOST_HOME", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr("odin.harnesses.forkd.os.getuid", lambda: 1234)
    monkeypatch.setattr(
        "odin.harnesses.forkd.pwd.getpwuid",
        lambda uid: type("Pw", (), {"pw_dir": str(real_home)})(),
    )

    assert ForkdHarness._host_home() == real_home.resolve()


def test_gemini_oauth_fresh_token_is_copied_without_refresh(monkeypatch, tmp_path):
    home = tmp_path / "home"
    gemini_dir = home / ".gemini"
    gemini_dir.mkdir(parents=True)
    (gemini_dir / "oauth_creds.json").write_text(json.dumps({
        "access_token": "access",
        "refresh_token": "refresh",
        "expiry_date": int(time.time() * 1000) + 3600_000,
    }))

    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True)
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_host_home", lambda: home)
    monkeypatch.setattr(
        "odin.harnesses.forkd.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fresh token should not refresh")),
    )

    out = tmp_path / "creds.tgz"
    harness._make_creds_tar(out)

    with tarfile.open(out, "r:gz") as tf:
        assert ".gemini/oauth_creds.json" in set(tf.getnames())


def test_gemini_oauth_refresh_forces_host_gemini_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    gemini_dir = home / ".gemini"
    gemini_dir.mkdir(parents=True)
    oauth = gemini_dir / "oauth_creds.json"
    oauth.write_text(json.dumps({
        "access_token": "old-access",
        "refresh_token": "refresh",
        "expiry_date": int(time.time() * 1000) - 1,
    }))

    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True)
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_host_home", lambda: home)
    monkeypatch.setenv("GEMINI_CLI_HOME", "/tmp/odin-home")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        oauth.write_text(json.dumps({
            "access_token": "new-access",
            "refresh_token": "refresh",
            "expiry_date": int(time.time() * 1000) + 3600_000,
        }))
        return subprocess.CompletedProcess(cmd, 0, '{"ok":true}', "")

    monkeypatch.setattr("odin.harnesses.forkd.subprocess.run", fake_run)
    harness._make_creds_tar(tmp_path / "creds.tgz")

    assert seen["HOME"] == str(home)
    assert seen["GEMINI_CLI_HOME"] == str(home)


def test_gemini_oauth_expiring_token_is_refreshed_before_copy(monkeypatch, tmp_path):
    home = tmp_path / "home"
    gemini_dir = home / ".gemini"
    gemini_dir.mkdir(parents=True)
    oauth = gemini_dir / "oauth_creds.json"
    oauth.write_text(json.dumps({
        "access_token": "old-access",
        "refresh_token": "refresh",
        "expiry_date": int(time.time() * 1000) - 1,
    }))

    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True, default_model="gemini-test")
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_host_home", lambda: home)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        oauth.write_text(json.dumps({
            "access_token": "new-access",
            "refresh_token": "refresh",
            "expiry_date": int(time.time() * 1000) + 3600_000,
        }))
        return subprocess.CompletedProcess(cmd, 0, '{"ok":true}', "")

    monkeypatch.setattr("odin.harnesses.forkd.subprocess.run", fake_run)

    out = tmp_path / "creds.tgz"
    harness._make_creds_tar(out)

    assert calls
    assert calls[0][0] == "gemini"
    assert "--model" in calls[0]
    with tarfile.open(out, "r:gz") as tf:
        member = tf.extractfile(".gemini/oauth_creds.json")
        assert member is not None
        assert json.loads(member.read().decode())["access_token"] == "new-access"


def test_gemini_oauth_refresh_failure_is_actionable(monkeypatch, tmp_path):
    home = tmp_path / "home"
    gemini_dir = home / ".gemini"
    gemini_dir.mkdir(parents=True)
    (gemini_dir / "oauth_creds.json").write_text(json.dumps({
        "access_token": "old-access",
        "refresh_token": "refresh",
        "expiry_date": int(time.time() * 1000) - 1,
    }))

    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True)
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_host_home", lambda: home)
    monkeypatch.setattr(
        "odin.harnesses.forkd.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 41, "", "Manual authorization is required"),
    )

    try:
        harness._make_creds_tar(tmp_path / "creds.tgz")
    except RuntimeError as exc:
        msg = str(exc)
        assert "could not refresh" in msg
        assert "Run `gemini` once on the host" in msg
    else:
        raise AssertionError("expected expired Gemini OAuth refresh failure")


def test_bypass_flags_injected_for_claude():
    cfg = AgentConfig(cli_command="claude", run_in_forkd=True)
    harness = ForkdHarness("claude", get_harness("claude", AgentConfig(cli_command="claude")), cfg)
    cmd = ["claude", "-p", "test prompt", "--output-format", "stream-json"]
    result = harness._inject_sandbox_bypass_flags(cmd)
    assert "--dangerously-skip-permissions" in result


def test_bypass_flags_not_duplicated():
    cfg = AgentConfig(cli_command="claude", run_in_forkd=True)
    harness = ForkdHarness("claude", get_harness("claude", AgentConfig(cli_command="claude")), cfg)
    cmd = ["claude", "-p", "test", "--dangerously-skip-permissions"]
    result = harness._inject_sandbox_bypass_flags(cmd)
    assert result.count("--dangerously-skip-permissions") == 1


def test_bypass_flags_for_gemini():
    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True)
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    cmd = ["gemini", "-p", "test prompt", "--output-format", "stream-json", "--yolo"]
    result = harness._inject_sandbox_bypass_flags(cmd)
    assert "--skip-trust" in result


def test_bypass_flags_for_opencode_agents():
    for agent in ("glm", "minimax"):
        cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
        harness = ForkdHarness(agent, get_harness(agent, AgentConfig(cli_command="opencode")), cfg)
        cmd = ["opencode", "run", "--format", "json", "-m", "test-model", "prompt"]
        result = harness._inject_sandbox_bypass_flags(cmd)
        assert "--dangerously-skip-permissions" in result


def test_bypass_flags_for_opencode_agents_not_duplicated():
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)
    cmd = ["opencode", "run", "--dangerously-skip-permissions", "prompt"]
    result = harness._inject_sandbox_bypass_flags(cmd)
    assert result.count("--dangerously-skip-permissions") == 1




def test_sandbox_script_uses_node_launcher_for_gemini():
    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True, api_key="secret")
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    script = harness._sandbox_script(
        cli_name="gemini",
        cli_cmd=["gemini", "-p", "test", "--output-format", "stream-json"],
        port=8080,
        timeout=300,
        asset_host="10.42.0.1",
        path_mappings=[],
    )
    assert 'exec node "%s" "$@"' in script
    assert 'export GEMINI_CLI_HOME="$HOME_DIR"' in script
    assert '"selectedType":"gemini-api-key"' in script


def test_sandbox_script_keeps_gemini_oauth_by_default(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-secret")
    cfg = AgentConfig(cli_command="gemini", run_in_forkd=True)
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    script = harness._sandbox_script(
        cli_name="gemini",
        cli_cmd=["gemini", "-p", "test", "--output-format", "stream-json"],
        port=8080,
        timeout=300,
        asset_host="10.42.0.1",
        path_mappings=[],
    )
    assert 'exec node "%s" "$@"' in script
    assert 'export GEMINI_CLI_HOME="$HOME_DIR"' in script
    assert '"selectedType":"oauth-personal"' in script
    assert '"selectedType":"gemini-api-key"' not in script
    assert "ambient-secret" not in script






def test_generated_forkd_runner_py_compiles(tmp_path):
    import py_compile

    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("minimax", get_harness("minimax", AgentConfig(cli_command="opencode")), cfg)
    script = harness._sandbox_script(
        cli_name="opencode",
        cli_cmd=["opencode", "run", "test"],
        port=8080,
        timeout=300,
        asset_host="10.42.0.1",
        path_mappings=[],
        trace_upload=True,
    )
    runner = script.split("cat > /tmp/odin-forkd-runner.py << 'ODIN_FORKD_RUNNER'", 1)[1].split("\nODIN_FORKD_RUNNER", 1)[0].lstrip("\n")
    runner_path = tmp_path / "odin-forkd-runner.py"
    runner_path.write_text(runner)

    py_compile.compile(str(runner_path), doraise=True)
    assert ' + "\\n")' in runner

def test_sandbox_script_redirects_runner_stderr_to_uploaded_file():
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("minimax", get_harness("minimax", AgentConfig(cli_command="opencode")), cfg)
    script = harness._sandbox_script(
        cli_name="opencode",
        cli_cmd=["opencode", "run", "test"],
        port=8080,
        timeout=300,
        asset_host="10.42.0.1",
        path_mappings=[],
        trace_upload=True,
    )
    assert "python3 /tmp/odin-forkd-runner.py >> /tmp/odin-forkd-stdout.txt 2>> /tmp/odin-forkd-stderr.txt" in script

def test_sandbox_script_includes_opencode_config():
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)
    script = harness._sandbox_script(
        cli_name="opencode",
        cli_cmd=["opencode", "run", "--format", "json", "test"],
        port=8080,
        timeout=300,
        asset_host="10.42.0.1",
        path_mappings=[],
    )
    assert "opencode.json" in script
    assert '"$schema"' in script
    assert '"permission"' not in script


def test_sandbox_script_no_opencode_config_for_other_agents():
    cfg = AgentConfig(cli_command="codex", run_in_forkd=True)
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)
    script = harness._sandbox_script(
        cli_name="codex",
        cli_cmd=["codex", "exec", "--json", "test"],
        port=8080,
        timeout=300,
        asset_host="10.42.0.1",
        path_mappings=[],
    )
    assert "opencode.json" not in script


def test_controller_snapshot_uses_memory_suffix_when_existing_tag_is_too_small(monkeypatch):
    cfg = AgentConfig(
        cli_command="opencode",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_snapshot_tag="odin-node22-4g",
        forkd_mem_size_mib=4096,
    )
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)

    def fake_controller(method, path, *, json=None, timeout, check=True):
        if method == "GET" and path == "/v1/snapshots/odin-node22-4g/info":
            return {"memory_logical_bytes": 512 * 1024 * 1024}
        if method == "GET" and path == "/v1/snapshots":
            return [{"tag": "odin-node22-4g"}]
        raise AssertionError((method, path, json))

    monkeypatch.setattr(harness, "_controller_request", fake_controller)

    assert harness._controller_snapshot_tag_for_memory("odin-node22-4g") == "odin-node22-4g-m4096"


def test_build_controller_snapshot_uses_cli_xdg_data_home(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="opencode",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_mem_size_mib=4096,
        forkd_tap="forkd-tap0",
    )
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)
    calls = []

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Proc()

    monkeypatch.setattr("odin.harnesses.forkd.subprocess.run", fake_run)
    monkeypatch.setattr("odin.harnesses.forkd.os.geteuid", lambda: 1000)

    harness._build_controller_snapshot_with_cli(
        ["forkd"],
        tmp_path / "vmlinux",
        "/opt/forkd/scripts",
        tmp_path / "rootfs.ext4",
        "odin-node22-4g-cli",
    )

    cmd = calls[0][0]
    assert cmd[:3] == ["sudo", "-n", "env"]
    assert "XDG_DATA_HOME=/var/lib" in cmd
    assert "--mem-size-mib" in cmd
    assert cmd[cmd.index("--mem-size-mib") + 1] == "4096"
    assert "--tag" in cmd
    assert cmd[cmd.index("--tag") + 1] == "odin-node22-4g-cli"


def test_controller_snapshot_prefers_existing_compatible_family_snapshot(monkeypatch):
    cfg = AgentConfig(
        cli_command="opencode",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_snapshot_tag="odin-node22-4g",
        forkd_mem_size_mib=1536,
    )
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)

    def fake_controller(method, path, *, json=None, timeout, check=True):
        if method == "GET" and path == "/v1/snapshots/odin-node22-4g/info":
            return {"memory_logical_bytes": 512 * 1024 * 1024}
        if method == "GET" and path == "/v1/snapshots/odin-node22-1536-cli/info":
            return {"memory_logical_bytes": 1536 * 1024 * 1024}
        if method == "GET" and path == "/v1/snapshots":
            return [{"tag": "odin-node22-4g"}, {"tag": "odin-node22-1536-cli"}]
        raise AssertionError((method, path, json))

    monkeypatch.setattr(harness, "_controller_request", fake_controller)

    assert harness._controller_snapshot_tag_for_memory("odin-node22-4g") == "odin-node22-1536-cli"


def test_assert_controller_guest_memory_allows_normal_kernel_overhead(monkeypatch):
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True, forkd_mode="controller", forkd_mem_size_mib=1536)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)

    def fake_controller_ok(method, path, *, json=None, timeout, check=True):
        assert method == "POST"
        assert path == "/v1/sandboxes/sb-1/exec"
        return {"stdout": "MemTotal:        1525568 kB\n", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(harness, "_controller_request", fake_controller_ok)
    harness._assert_controller_guest_memory("sb-1")


def test_assert_controller_guest_memory_allows_4g_reserved_overhead(monkeypatch):
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True, forkd_mode="controller", forkd_mem_size_mib=4096)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)

    def fake_controller_ok(method, path, *, json=None, timeout, check=True):
        assert method == "POST"
        assert path == "/v1/sandboxes/sb-1/exec"
        return {"stdout": "MemTotal:        4036608 kB\n", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(harness, "_controller_request", fake_controller_ok)
    harness._assert_controller_guest_memory("sb-1")


def test_assert_controller_guest_memory_rejects_undersized_guest(monkeypatch):
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True, forkd_mode="controller", forkd_mem_size_mib=1536)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)

    def fake_controller_bad(method, path, *, json=None, timeout, check=True):
        assert method == "POST"
        assert path == "/v1/sandboxes/sb-1/exec"
        return {"stdout": "MemTotal:         495680 kB\n", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(harness, "_controller_request", fake_controller_bad)

    try:
        harness._assert_controller_guest_memory("sb-1")
    except RuntimeError as exc:
        assert "booted with only" in str(exc)
    else:
        raise AssertionError("expected undersized guest memory to raise")


def test_workspace_tar_force_includes_generated_mcp_config_and_rewrites_taskit_url(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".gemini").mkdir()
    mcp_config = work / ".gemini" / "settings.json"
    mcp_config.write_text('{"mcpServers":{"taskit":{"env":{"TASKIT_URL":"__ODIN_FORKD_TASKIT_URL__"}}}}')

    out = tmp_path / "workspace.tgz"
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), AgentConfig())
    harness._make_workspace_tar(
        work,
        out,
        guest_taskit_url="http://10.42.0.1:12345",
        include_paths=[mcp_config],
    )

    with tarfile.open(out, "r:gz") as tf:
        names = set(tf.getnames())
        extracted = tf.extractfile(".gemini/settings.json").read().decode()

    assert ".gemini/settings.json" in names
    assert "http://10.42.0.1:12345" in extracted
    assert "__ODIN_FORKD_TASKIT_URL__" not in extracted


def test_workspace_tar_still_excludes_non_generated_host_agent_config(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".gemini").mkdir()
    (work / ".gemini" / "oauth_creds.json").write_text("secret")

    out = tmp_path / "workspace.tgz"
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), AgentConfig())
    harness._make_workspace_tar(work, out)

    with tarfile.open(out, "r:gz") as tf:
        names = set(tf.getnames())

    assert ".gemini/oauth_creds.json" not in names


def test_sandbox_script_preserves_opencode_mcp_config_and_stages_taskit_mcp(tmp_path):
    cfg = AgentConfig(cli_command="opencode", run_in_forkd=True)
    harness = ForkdHarness("glm", get_harness("glm", AgentConfig(cli_command="opencode")), cfg)

    script = harness._sandbox_script(
        cli_name="opencode",
        cli_cmd=["opencode", "run", "hello"],
        port=1234,
        timeout=60,
        asset_host="10.42.0.1",
        path_mappings=[],
    )

    assert "taskit-mcp" in script
    assert "if [ ! -f /tmp/odin-workspace/opencode.json ]; then" in script
    assert "ODIN_TASKIT_MCP" in script


def test_taskit_mcp_shim_supports_question_endpoint():
    from odin.harnesses.forkd import _TASKIT_MCP_SHIM

    assert 'if ctype == "question"' in _TASKIT_MCP_SHIM
    assert 'post_json("question/"' in _TASKIT_MCP_SHIM
    assert 'TASKIT_AUTHOR_EMAIL' in _TASKIT_MCP_SHIM


def test_rootfs_cache_key_includes_extra_packages(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="codex",
        run_in_forkd=True,
        forkd_cache_dir=str(tmp_path),
        forkd_image="node:22-slim",
        forkd_extra=["python3", "ca-certificates", "git", "chromium"],
    )
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)
    calls = []

    def fake_run(forkd, args, *, scripts_dir, timeout, check=True):
        calls.append(args)
        output = Path(args[args.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("rootfs")
        import subprocess
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(harness, "_run_forkd_command", fake_run)
    rootfs = harness._ensure_rootfs(["forkd"], tmp_path / "vmlinux", None)

    assert rootfs.name == "node-22-slim-python3-ca-certificates-git-chromium-s8192.ext4"
    assert "chromium" in calls[0]
    assert calls[0][calls[0].index("--size-mib") + 1] == "8192"


def test_browser_rootfs_build_failure_is_actionable(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="codex",
        run_in_forkd=True,
        forkd_cache_dir=str(tmp_path),
        forkd_extra=["python3", "chromium"],
    )
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)

    def fake_run(*args, **kwargs):
        raise RuntimeError("sudo: a password is required")

    monkeypatch.setattr(harness, "_run_forkd_command", fake_run)

    import pytest
    with pytest.raises(RuntimeError) as exc:
        harness._ensure_rootfs(["forkd"], tmp_path / "vmlinux", None)

    msg = str(exc.value)
    assert "forkd browser snapshot/rootfs is not provisioned" in msg
    assert "Odin will not install Chromium inside each disposable task sandbox" in msg
    assert "Provision command: sudo -E" in msg


def test_sandbox_script_preflights_chromium_when_required():
    cfg = AgentConfig(cli_command="codex", run_in_forkd=True)
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)

    script = harness._sandbox_script(
        cli_name="codex",
        cli_cmd=["codex", "exec", "hello"],
        port=1234,
        timeout=60,
        asset_host="10.42.0.1",
        path_mappings=[],
        require_chrome=True,
    )

    assert "forkd chrome preflight failed" in script
    assert "apt-get" not in script
    assert "PUPPETEER_EXECUTABLE_PATH" in script


def test_controller_browser_required_uses_browser_snapshot_tag():
    cfg = AgentConfig(
        cli_command="codex",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_snapshot_tag="odin-node22-4g-cli",
    )
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)

    assert harness._controller_base_tag(require_chrome=False) == "odin-node22-4g-cli"
    assert harness._controller_base_tag(require_chrome=True) == "odin-node22-4g-cli-browser"

    cfg.forkd_snapshot_tag = "odin-node22-4g-cli-browser"
    assert harness._controller_base_tag(require_chrome=True) == "odin-node22-4g-cli-browser"


def test_controller_existing_snapshot_skips_rootfs_build(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="codex",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_snapshot_tag="odin-node22-4g-cli",
        forkd_mem_size_mib=4096,
        forkd_per_child_netns=False,
        forkd_bin="/bin/true",
        forkd_kernel=str(tmp_path / "vmlinux"),
    )
    harness = ForkdHarness("codex", get_harness("codex", AgentConfig(cli_command="codex")), cfg)
    monkeypatch.setattr(harness, "_controller_snapshot_tag_for_memory", lambda tag: "odin-node22-4g-cli")
    monkeypatch.setattr(harness, "_controller_snapshot_has_memory", lambda tag: True)
    monkeypatch.setattr(harness, "_ensure_rootfs", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rootfs build should be skipped")))

    calls = []

    class FakeAssetServer:
        instances = []

        def __init__(self, directory, bind_host, **_kwargs):
            self.directory = directory
            self.bind_host = bind_host
            self.port = 12345
            self.instances.append(self)

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr("odin.harnesses.forkd._AssetServer", FakeAssetServer)
    monkeypatch.setattr(harness, "_make_cli_tar", lambda cli, out: out.write_text("cli"))
    monkeypatch.setattr(harness, "_make_creds_tar", lambda out: out.write_text("creds"))
    monkeypatch.setattr(harness, "_make_workspace_tar", lambda *a, **k: None)
    monkeypatch.setattr(harness, "_sandbox_script", lambda **k: "echo ok")
    monkeypatch.setattr(harness, "_run_guest_script_controller", lambda **k: calls.append(k) or __import__('subprocess').CompletedProcess([], 0, "ok", ""))

    class Inner:
        name = "codex"
        def build_execute_command(self, prompt, context):
            return ["codex", "exec", prompt]

    harness.inner = Inner()
    import pytest
    with pytest.raises(RuntimeError, match="ok"):
        harness._execute_sync("hello", {"working_dir": str(tmp_path), "timeout_seconds": 10})

    assert calls[0]["rootfs"] == Path("/dev/null")
    assert FakeAssetServer.instances[0].bind_host == "0.0.0.0"


def test_controller_per_child_netns_advertises_10_43_but_binds_helpers_to_any(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="gemini",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_per_child_netns=True,
        forkd_snapshot_tag="odin-node22-4g-cli",
        forkd_mem_size_mib=4096,
        forkd_bin="/bin/true",
        forkd_kernel=str(tmp_path / "vmlinux"),
    )
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_controller_snapshot_tag_for_memory", lambda tag: "odin-node22-4g-cli")
    monkeypatch.setattr(harness, "_controller_snapshot_has_memory", lambda tag: True)

    seen = {"assets": [], "proxies": [], "guest_urls": []}

    class FakeAssetServer:
        def __init__(self, directory, bind_host, **_kwargs):
            seen["assets"].append(bind_host)
            self.port = 12345
        def start(self): return None
        def stop(self): return None

    class FakeTaskItProxy:
        def __init__(self, upstream, bind_host):
            seen["proxies"].append(bind_host)
            self.port = 23456
        def start(self): return None
        def stop(self): return None

    monkeypatch.setattr("odin.harnesses.forkd._AssetServer", FakeAssetServer)
    monkeypatch.setattr("odin.harnesses.forkd._TaskItProxy", FakeTaskItProxy)
    monkeypatch.setattr(harness, "_make_cli_tar", lambda cli, out: out.write_text("cli"))
    monkeypatch.setattr(harness, "_make_creds_tar", lambda out: out.write_text("creds"))
    monkeypatch.setattr(harness, "_make_workspace_tar", lambda *a, **k: seen["guest_urls"].append(k.get("guest_taskit_url")))
    monkeypatch.setattr(harness, "_sandbox_script", lambda **k: "echo ok")
    monkeypatch.setattr(harness, "_run_guest_script_controller", lambda **k: __import__('subprocess').CompletedProcess([], 0, "-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nok", ""))

    class Inner:
        name = "gemini"
        def build_execute_command(self, prompt, context):
            return ["gemini", "-p", prompt]

    harness.inner = Inner()
    import pytest
    with pytest.raises(RuntimeError, match="SUCCESS"):
        harness._execute_sync("hello", {"working_dir": str(tmp_path), "timeout_seconds": 10, "forkd_taskit_base_url": "http://127.0.0.1:9101"})

    assert seen["assets"] == ["0.0.0.0"]
    assert seen["proxies"] == ["0.0.0.0"]
    assert seen["guest_urls"] == ["http://10.43.0.1:23456"]




def test_shared_tap_controller_cleans_stale_same_tag_before_spawn(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="gemini",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_per_child_netns=False,
        forkd_snapshot_tag="odin-node22-4g-cli-browser",
        forkd_mem_size_mib=4096,
    )
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_ensure_controller_snapshot", lambda *a, **k: "odin-node22-4g-cli-browser")
    calls = []

    def fake_controller(method, path, *, json=None, timeout, check=True):
        calls.append((method, path, json))
        if method == "GET" and path == "/v1/sandboxes":
            return [
                {"id": "stale-browser", "snapshot_tag": "odin-node22-4g-cli-browser", "netns": None},
                {"id": "other-netns", "snapshot_tag": "odin-node22-4g-cli-browser", "netns": "forkd-child-2"},
            ]
        if method == "POST" and path == "/v1/sandboxes":
            return [{"id": "sb-new", "netns": None}]
        if method == "POST" and path == "/v1/sandboxes/sb-new/ping":
            return {"pong": True}
        if method == "POST" and path == "/v1/sandboxes/sb-new/exec":
            if json and json.get("args") == ["sh", "-lc", "grep MemTotal /proc/meminfo"]:
                return {"stdout": "MemTotal:        4194304 kB\n", "stderr": "", "exit_code": 0}
            return {"stdout": "ok", "stderr": "", "exit_code": 0}
        return None

    monkeypatch.setattr(harness, "_controller_request", fake_controller)
    proc = harness._run_guest_script_controller(
        forkd=["forkd"], kernel=tmp_path / "vmlinux", scripts_dir=None,
        rootfs=tmp_path / "rootfs.ext4", script_command="echo ok", timeout=30,
        per_child_netns=False,
    )

    assert proc.stdout == "ok"
    assert ("DELETE", "/v1/sandboxes/stale-browser", None) in calls
    assert ("DELETE", "/v1/sandboxes/other-netns", None) not in calls
    spawn = next(call for call in calls if call[0] == "POST" and call[1] == "/v1/sandboxes")
    assert spawn[2]["per_child_netns"] is False


def test_shared_tap_controller_retries_once_after_tap_busy(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="gemini",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_per_child_netns=False,
        forkd_snapshot_tag="odin-node22-4g-cli-browser",
        forkd_mem_size_mib=4096,
    )
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_ensure_controller_snapshot", lambda *a, **k: "odin-node22-4g-cli-browser")
    attempts = {"spawn": 0, "cleanup": 0}

    def fake_cleanup(tag):
        attempts["cleanup"] += 1

    def fake_controller(method, path, *, json=None, timeout, check=True):
        if method == "POST" and path == "/v1/sandboxes":
            attempts["spawn"] += 1
            if attempts["spawn"] == 1:
                raise RuntimeError("Open tap device failed: Resource busy (os error 16). Invalid TUN/TAP Backend provided by forkd-tap0")
            return [{"id": "sb-new", "netns": None}]
        if method == "POST" and path == "/v1/sandboxes/sb-new/ping":
            return {"pong": True}
        if method == "POST" and path == "/v1/sandboxes/sb-new/exec":
            if json and json.get("args") == ["sh", "-lc", "grep MemTotal /proc/meminfo"]:
                return {"stdout": "MemTotal:        4194304 kB\n", "stderr": "", "exit_code": 0}
            return {"stdout": "ok", "stderr": "", "exit_code": 0}
        return None

    monkeypatch.setattr(harness, "_cleanup_shared_tap_sandboxes", fake_cleanup)
    monkeypatch.setattr(harness, "_controller_request", fake_controller)
    proc = harness._run_guest_script_controller(
        forkd=["forkd"], kernel=tmp_path / "vmlinux", scripts_dir=None,
        rootfs=tmp_path / "rootfs.ext4", script_command="echo ok", timeout=30,
        per_child_netns=False,
    )

    assert proc.stdout == "ok"
    assert attempts == {"spawn": 2, "cleanup": 2}

def test_controller_browser_required_keeps_per_child_netns(monkeypatch, tmp_path):
    cfg = AgentConfig(
        cli_command="gemini",
        run_in_forkd=True,
        forkd_mode="controller",
        forkd_per_child_netns=True,
        forkd_snapshot_tag="odin-node22-4g-cli",
        forkd_mem_size_mib=4096,
        forkd_bin="/bin/true",
        forkd_kernel=str(tmp_path / "vmlinux"),
    )
    harness = ForkdHarness("gemini", get_harness("gemini", AgentConfig(cli_command="gemini")), cfg)
    monkeypatch.setattr(harness, "_controller_snapshot_tag_for_memory", lambda tag: "odin-node22-4g-cli-browser")
    monkeypatch.setattr(harness, "_controller_snapshot_has_memory", lambda tag: True)

    seen = {"guest_urls": [], "run_kwargs": []}

    class FakeAssetServer:
        def __init__(self, directory, bind_host, **_kwargs): self.port = 12345
        def start(self): return None
        def stop(self): return None

    class FakeTaskItProxy:
        def __init__(self, upstream, bind_host): self.port = 23456
        def start(self): return None
        def stop(self): return None

    monkeypatch.setattr("odin.harnesses.forkd._AssetServer", FakeAssetServer)
    monkeypatch.setattr("odin.harnesses.forkd._TaskItProxy", FakeTaskItProxy)
    monkeypatch.setattr(harness, "_make_cli_tar", lambda cli, out: out.write_text("cli"))
    monkeypatch.setattr(harness, "_make_creds_tar", lambda out: out.write_text("creds"))
    monkeypatch.setattr(harness, "_make_workspace_tar", lambda *a, **k: seen["guest_urls"].append(k.get("guest_taskit_url")))
    monkeypatch.setattr(harness, "_sandbox_script", lambda **k: "echo ok")
    monkeypatch.setattr(harness, "_run_guest_script_controller", lambda **k: seen["run_kwargs"].append(k) or __import__('subprocess').CompletedProcess([], 0, "ok", ""))

    class Inner:
        name = "gemini"
        def build_execute_command(self, prompt, context): return ["gemini", "-p", prompt]

    harness.inner = Inner()
    import pytest
    with pytest.raises(RuntimeError, match="ok"):
        harness._execute_sync("hello", {"working_dir": str(tmp_path), "timeout_seconds": 10, "forkd_taskit_base_url": "http://127.0.0.1:9101", "forkd_require_chrome": True})

    assert seen["guest_urls"] == ["http://10.43.0.1:23456"]
    assert seen["run_kwargs"][0]["per_child_netns"] is True
