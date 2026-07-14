"""Proof harness: run the same XDG-isolation path the harness uses, capture the
exact env/mount config it generates for two same-tick dispatches, and emit a
machine-readable proof artifact.

This is what the task's acceptance criterion demands: "the exact env/mount
config a staged run generates (from a test, not hand-written)."

Run with:
    .venv-test/bin/python testing_tools/print_xdg_proof.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ODIN_PKG = REPO_ROOT / "odin"
sys.path.insert(0, str(ODIN_PKG / "src"))

from odin.harnesses.microsandbox import MicrosandboxHarness  # noqa: E402
from odin.harnesses.registry import get_harness  # noqa: E402
from odin.models import AgentConfig  # noqa: E402


def capture_one_dispatch(agent, workdir, fake_calls):
    """Stage one opencode-family dispatch the way the harness does: build a real
    AgentConfig, call get_harness, run _execute_sync through a fake subprocess.run
    that captures the msb argv, and pull the env + mounts out of that argv."""
    cfg = AgentConfig(cli_command="opencode", sandbox_mode="microsandbox")
    h = get_harness(agent, cfg)

    # Per _opencode_inner_cmd in the existing tests
    monkey_inner = lambda prompt, context: [
        "opencode", "run", "--format", "json", "-m",
        "zai-coding-plan/glm-5.2" if agent == "glm" else "minimax-coding-plan/MiniMax-M3",
        "p",
    ]
    import odin.harnesses.microsandbox as msb_mod
    original_run = msb_mod.subprocess.run

    def fake_run(argv, **kw):
        fake_calls.append({
            "agent": agent,
            "argv": list(argv),
        })
        from odin.harnesses import microsandbox as m
        return m.subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    msb_mod.subprocess.run = fake_run
    try:
        # _execute_sync mutates context (sets _xdg_config_dir / _xdg_data_dir);
        # pass a fresh dict per call so two same-tick dispatches don't share state.
        ctx = {"working_dir": str(workdir)}
        h._execute_sync("p", ctx)
    finally:
        msb_mod.subprocess.run = original_run

    last = fake_calls[-1]
    argv = last["argv"]

    # Pull the relevant flags out of argv
    env = {}
    i = 0
    while i < len(argv):
        if argv[i] == "-e":
            kv = argv[i + 1]
            if "=" in kv:
                k, v = kv.split("=", 1)
                env[k] = v
            i += 2
        else:
            i += 1

    mounts = []
    i = 0
    while i < len(argv):
        if argv[i] == "-v":
            mounts.append(argv[i + 1])
            i += 2
        else:
            i += 1

    return {"agent": agent, "argv": argv, "env": env, "mounts": mounts}


def main():
    fake_calls = []
    with tempfile.TemporaryDirectory(prefix="odin-xdg-proof-") as tmp:
        home = Path(tmp)
        (home / ".config/opencode").mkdir(parents=True)
        (home / ".config/opencode/opencode.jsonc").write_text("{}")
        (home / ".local/share/opencode").mkdir(parents=True)
        (home / ".local/share/opencode/auth.json").write_text("{}")
        # Make Path.home() resolve to our tmp so the credential-mount scanner
        # sees the staged files (matches what tests do with monkeypatch.setenv).
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            # Two same-tick dispatches of glm (same shape as minimax — both are
            # opencode-family per _OPENCODE_FAMILY).
            dispatches = [
                capture_one_dispatch("glm", home, fake_calls),
                capture_one_dispatch("glm", home, fake_calls),
            ]
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

    # Reduce to the proof artifact: env + mounts per dispatch, plus the
    # "are these distinct?" assertion the task asks for.
    out = {"dispatches": []}
    xdg_mount_sources = []
    for d in dispatches:
        out["dispatches"].append({
            "agent": d["agent"],
            "env": d["env"],
            "mounts": d["mounts"],
        })
        for m in d["mounts"]:
            if "/odin-xdg-" in m and m.endswith(":rw"):
                xdg_mount_sources.append(m.split(":")[0])

    # The guest env values (XDG_CONFIG_HOME, XDG_DATA_HOME) are stable across
    # dispatches -- they point to GUEST-SIDE mount points (/mnt/odin-xdg-*).
    # The actual isolation is provided by the bind-mount SOURCES: each
    # _execute_sync mints its own tmpdir (/tmp/odin-msb-<token>/), and mounts
    # that tmpdir's xdg-{config,data} subtrees onto the shared guest paths.
    # Two same-tick dispatches therefore back the same guest path with two
    # different host directories -- no shared SQLite, no race.
    xdg_mounts_a = [m for m in dispatches[0]["mounts"]
                    if "/mnt/odin-xdg-" in m and m.endswith(":rw")]
    xdg_mounts_b = [m for m in dispatches[1]["mounts"]
                    if "/mnt/odin-xdg-" in m and m.endswith(":rw")]
    srcs_a = sorted(m.split(":")[0] for m in xdg_mounts_a)
    srcs_b = sorted(m.split(":")[0] for m in xdg_mounts_b)

    out["assertion"] = {
        "claim": "Two same-tick opencode-family dispatches get distinct XDG host backing dirs",
        "why_env_is_stable": (
            "XDG_CONFIG_HOME/XDG_DATA_HOME point to guest-side mount points "
            "(/mnt/odin-xdg-config, /mnt/odin-xdg-data) — these are STABLE by design. "
            "Isolation comes from each dispatch mounting its own HOST directory "
            "onto those shared guest paths, so the guest sees a private XDG tree."
        ),
        "dispatch_a_xdg_mounts_rw": xdg_mounts_a,
        "dispatch_b_xdg_mounts_rw": xdg_mounts_b,
        "dispatch_a_xdg_mount_sources": srcs_a,
        "dispatch_b_xdg_mount_sources": srcs_b,
        "host_backing_dirs_distinct": srcs_a != srcs_b,
        "dispatch_a_xdg_env": {
            "XDG_CONFIG_HOME": dispatches[0]["env"].get("XDG_CONFIG_HOME"),
            "XDG_DATA_HOME": dispatches[0]["env"].get("XDG_DATA_HOME"),
        },
        "dispatch_b_xdg_env": {
            "XDG_CONFIG_HOME": dispatches[1]["env"].get("XDG_CONFIG_HOME"),
            "XDG_DATA_HOME": dispatches[1]["env"].get("XDG_DATA_HOME"),
        },
        "guest_env_paths_identical": (
            dispatches[0]["env"].get("XDG_CONFIG_HOME")
            == dispatches[1]["env"].get("XDG_CONFIG_HOME")
            and dispatches[0]["env"].get("XDG_DATA_HOME")
            == dispatches[1]["env"].get("XDG_DATA_HOME")
        ),
    }
    print(json.dumps(out, indent=2))
    if not out["assertion"]["host_backing_dirs_distinct"]:
        sys.exit("FAIL: same-tick dispatches share an XDG host backing dir")
    if not out["assertion"]["guest_env_paths_identical"]:
        sys.exit("FAIL: XDG env should point to the same guest mount path")


if __name__ == "__main__":
    main()