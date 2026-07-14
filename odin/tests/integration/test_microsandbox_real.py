"""Real-VM integration test for MicrosandboxHarness (requires `msb` installed).

Boots an actual libkrun microVM and runs a command through the full
MicrosandboxHarness.execute() path — the proof that the harness works end to end,
not just against mocks. Excluded from the default suite (tests/integration is
ignored via addopts). Run explicitly:

    python -m pytest tests/integration/test_microsandbox_real.py -o addopts="" -v
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from odin.harnesses.base import BaseHarness
from odin.harnesses.microsandbox import MicrosandboxHarness
from odin.models import AgentConfig, TaskResult


def _msb_available() -> bool:
    if shutil.which("msb"):
        return True
    return any(
        (Path.home() / p).exists()
        for p in (".local/bin/msb", ".microsandbox/bin/msb")
    )


pytestmark = pytest.mark.skipif(
    not _msb_available(), reason="microsandbox (msb) not installed"
)

_STATUS = "printf '%s\\n' '-------ODIN-STATUS-------' SUCCESS"


class _EchoInner(BaseHarness):
    """Minimal inner harness whose CLI emits a valid ODIN-STATUS SUCCESS block.

    The inner harness is only ever asked for build_execute_command by the
    decorator, so execute() is a stub.
    """

    @property
    def name(self) -> str:
        return "echo-agent"

    async def execute(self, prompt: str, context: dict) -> TaskResult:  # pragma: no cover
        raise NotImplementedError("inner is used only for build_execute_command")

    async def is_available(self) -> bool:
        return True

    def build_execute_command(self, prompt, context):
        return ["sh", "-lc", f"echo 'did the work'; {_STATUS}"]


def _cfg():
    return AgentConfig(
        sandbox_mode="microsandbox",
        microsandbox_image="alpine",
        microsandbox_timeout_secs=60,
    )


def test_microsandbox_executes_inner_command_in_real_vm(tmp_path):
    cfg = _cfg()
    harness = MicrosandboxHarness("echo", _EchoInner(cfg), cfg)
    result: TaskResult = asyncio.run(
        harness.execute("do the task", {"working_dir": str(tmp_path)})
    )
    assert result.success is True, f"error={result.error!r} output={result.output!r}"
    assert result.metadata["sandbox"] == "microsandbox"
    assert "did the work" in result.output
    assert result.agent.endswith("(microsandbox)")


def test_microsandbox_workspace_edits_persist_to_host(tmp_path):
    cfg = _cfg()

    class _WriterInner(_EchoInner):
        def build_execute_command(self, prompt, context):
            return [
                "sh",
                "-lc",
                f"echo hello-from-guest > /workspace/artifact.txt; {_STATUS}",
            ]

    harness = MicrosandboxHarness("echo", _WriterInner(cfg), cfg)
    result = asyncio.run(
        harness.execute("write a file", {"working_dir": str(tmp_path)})
    )
    assert result.success is True, f"error={result.error!r} output={result.output!r}"
    artifact = tmp_path / "artifact.txt"
    assert artifact.exists(), "guest write did not persist back to host worktree"
    assert artifact.read_text().strip() == "hello-from-guest"


def _snapshot_ready(name="odin-agents"):
    return (Path.home() / f".microsandbox/snapshots/{name}").exists()


def _glm_creds_present():
    return (Path.home() / ".local/share/opencode/auth.json").exists()


@pytest.mark.skipif(
    not (_msb_available() and _snapshot_ready() and _glm_creds_present()),
    reason="needs msb + odin-agents snapshot + glm (opencode) credentials",
)
def test_glm_runs_confined_via_hardened_harness(tmp_path):
    """Full hardened path: MicrosandboxHarness wrapping the real GLMHarness boots the
    odin-agents snapshot, mounts opencode creds read-only, injects no bypass flag,
    runs glm confined at 4 GB with stdin closed, and returns a real model response."""
    from odin.harnesses.glm import GLMHarness

    cfg = AgentConfig(
        cli_command="opencode",
        sandbox_mode="microsandbox",
        microsandbox_snapshot="odin-agents",
        microsandbox_timeout_secs=150,
    )
    harness = MicrosandboxHarness("glm", GLMHarness(cfg), cfg)
    result = asyncio.run(harness.execute(
        "Reply with exactly the token HARDENED_GLM_OK and nothing else.",
        {
            "working_dir": str(tmp_path),
            "model": "zai-coding-plan/glm-4.7",
            "validate_status": False,
        },
    ))
    assert result.success is True, f"error={result.error!r} out={result.output[:400]!r}"
    assert "HARDENED_GLM_OK" in result.output
    assert result.metadata.get("sandbox") == "microsandbox"


def _repo_root():
    # board 5's working_dir is the repo root; the TaskIt UI writes .claude-token there.
    return Path(__file__).resolve().parents[3]


def _claude_harness(**cfg_kw):
    from odin.harnesses.claude import ClaudeHarness

    cfg = AgentConfig(
        cli_command="claude",
        sandbox_mode="microsandbox",
        microsandbox_snapshot="odin-agents",
        microsandbox_timeout_secs=180,
        **cfg_kw,
    )
    return MicrosandboxHarness("claude", ClaudeHarness(cfg), cfg)


def _claude_token_resolvable():
    # Production resolution: env → explicit file → macOS Keychain → .claude-token.
    return _claude_harness()._claude_token({"working_dir": str(_repo_root())}) is not None


@pytest.mark.skipif(
    not (_msb_available() and _snapshot_ready() and _claude_token_resolvable()),
    reason="needs msb + odin-agents snapshot + a resolvable claude OAuth token "
           "(keychain login or .claude-token via TaskIt UI)",
)
def test_claude_runs_confined_via_hardened_harness(tmp_path, monkeypatch):
    """Full hardened path for claude: MicrosandboxHarness wrapping ClaudeHarness
    resolves the OAuth token the way production does (env → explicit file → macOS
    Keychain → discovered .claude-token, here rooted at the board's working_dir),
    injects it as CLAUDE_CODE_OAUTH_TOKEN, and runs claude confined in the
    odin-agents snapshot."""
    harness = _claude_harness()
    tok = harness._claude_token({"working_dir": str(_repo_root())})
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", tok)
    result = asyncio.run(harness.execute(
        "Reply with exactly the token SANDBOX_CLAUDE_OK and nothing else.",
        {"working_dir": str(tmp_path), "validate_status": False},
    ))
    assert result.success is True, f"error={result.error!r} out={result.output[:400]!r}"
    assert "SANDBOX_CLAUDE_OK" in result.output
    assert result.metadata.get("sandbox") == "microsandbox"


@pytest.mark.skipif(
    not (_msb_available() and _snapshot_ready()),
    reason="needs msb + odin-agents snapshot",
)
def test_live_trace_streams_and_survives_kill(tmp_path):
    """The canonical trace file grows WHILE the guest runs (bind-mount tee) and
    keeps everything written so far when the VM is timeout-killed — msb's own
    stdout buffers and is discarded on kill, which is why the mount exists."""
    import threading
    import time

    class TickInner:
        name = "tick-inner"

        def build_execute_command(self, prompt, context):
            return ["sh", "-c", "for i in $(seq 1 30); do echo TICK $i; sleep 1; done"]

    cfg = AgentConfig(
        cli_command="sh",
        sandbox_mode="microsandbox",
        microsandbox_snapshot="odin-agents",
        microsandbox_timeout_secs=8,   # kill mid-count
        microsandbox_mem_size_mib=512,  # shell-only guest boots small
    )
    h = MicrosandboxHarness("tick", TickInner(), cfg)
    trace = tmp_path / "t.trace.jsonl"
    out = tmp_path / "t.out"
    ctx = {
        "working_dir": str(tmp_path),
        "trace_file": str(trace),
        "output_file": str(out),
        "validate_status": False,
    }

    box = {}
    t = threading.Thread(target=lambda: box.update(r=asyncio.run(h.execute("p", ctx))))
    t.start()
    sizes = []
    for _ in range(40):
        time.sleep(1)
        sizes.append(trace.stat().st_size if trace.exists() else 0)
        if not t.is_alive():
            break
    t.join(timeout=90)

    grew_mid_run = any(b > a for a, b in zip(sizes, sizes[1:]))
    assert grew_mid_run, f"trace never grew during the run: {sizes}"
    assert "TICK 1" in trace.read_text()        # survived the timeout kill
    assert "TICK 1" in box["r"].output          # file is the output source
    assert "TICK 1" in out.read_text()          # .out mirror → `odin logs -f` is live


@pytest.mark.skipif(
    not (_msb_available() and _snapshot_ready()),
    reason="needs msb + odin-agents snapshot",
)
def test_linked_worktree_git_works_in_guest(tmp_path):
    """Odin task workspaces are LINKED worktrees — .git is a pointer file into the
    main repo. The harness must mount both at identical host paths so git works
    in-guest (F30: glm burned 2x30min on 'fatal: not a git repository')."""
    import subprocess as sp

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.txt").write_text("a\n")
    sp.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    sp.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "init"], check=True)
    wt = tmp_path / "wt"
    sp.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "task-x"],
           check=True)

    class GitInner:
        name = "git-inner"

        def build_execute_command(self, prompt, context):
            return ["sh", "-c",
                    "git status --porcelain >/dev/null && echo GIT_OK && "
                    "echo guest > g.txt && git add g.txt && "
                    "git -c user.email=g@g -c user.name=g commit -qm guest-commit && "
                    "echo COMMIT_OK"]

    cfg = AgentConfig(
        cli_command="sh",
        sandbox_mode="microsandbox",
        microsandbox_snapshot="odin-agents",
        microsandbox_timeout_secs=60,
        microsandbox_mem_size_mib=512,
    )
    h = MicrosandboxHarness("gitprobe", GitInner(), cfg)
    result = asyncio.run(h.execute("p", {"working_dir": str(wt), "validate_status": False}))
    assert "GIT_OK" in result.output and "COMMIT_OK" in result.output, result.output[:400]
    head = sp.run(["git", "-C", str(wt), "log", "-1", "--format=%s"],
                  capture_output=True, text=True).stdout.strip()
    assert head == "guest-commit"  # the guest's commit persisted to the host worktree
