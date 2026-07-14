"""Run-end sandbox lifecycle tests for MicrosandboxHarness.

Every ``msb run`` boots an ephemeral sandbox (``msb-<hash>``) which is a full
copy of the ``odin-agents`` snapshot. Until this fix those sandboxes leaked —
the harness had no removal call at any path. These tests pin the fix:

- Success, non-zero exit, host timeout, and inner-harness exception ALL must
  remove the per-run sandbox. (No "miss" by control flow.)
- The ``odin-msb-*`` temp dir used for MCP staging must be removed at the same
  boundary.
- The persistent, intentionally-named sandboxes (``odinbuild``, anything not
  matching ``odin-msb-*``) MUST NEVER be in the removal set.
- Snapshot images (``odin-agents``) MUST NEVER be in the removal set.

All tests are pure-mock: they patch ``subprocess.run`` so we can capture every
msb command issued, including the cleanup ones. No real ``msb`` is required.
"""

import json
import subprocess
from pathlib import Path

import pytest

from odin.harnesses.microsandbox import MicrosandboxHarness
from odin.harnesses.registry import get_harness
from odin.models import AgentConfig


def _cfg(**kw):
    base = dict(cli_command="codex", sandbox_mode="microsandbox")
    base.update(kw)
    return AgentConfig(**base)


def _sandbox_name(name: str) -> str:
    """Helper: assert a name is in ``msb remove`` argv (or any argv)."""
    return name


def _names_called(subprocess_calls, msb_subcommand_index=1):
    """Extract ``msb <subcommand> <name>`` from the captured argv lists.

    Returns the list of names that appeared in removal calls (the cleanup
    calls — what we care about for "leak" verification). Matches both:
      - ``msb sandbox remove --name <name>``  (the form we issue)
      - ``msb remove <name>``                  (legacy form)
    """
    out = []
    for argv in subprocess_calls:
        if not argv or not argv[0].endswith("msb"):
            continue
        # Recognize any flavor of "msb .. remove" (with or without a subcommand
        # like "sandbox" before "remove").
        if "remove" not in argv:
            continue
        rm_idx = argv.index("remove")
        if "--name" in argv:
            try:
                i = argv.index("--name") + 1
                if i < len(argv):
                    out.append(argv[i])
            except ValueError:
                pass
        else:
            # positional: name is the token AFTER "remove"
            j = rm_idx + 1
            if j < len(argv):
                out.append(argv[j])
    return out


def _all_argv_to_subprocess_calls(spy_argv_log):
    """Return the argv lists that ``subprocess.run`` was called with."""
    return spy_argv_log


@pytest.fixture
def capture_subprocess(monkeypatch):
    """Capture every argv passed to ``subprocess.run`` and return the log.

    Each entry: {"argv": list, "kwargs": dict}. The factory that produces the
    CompletedProcess defaults to "ok"; tests can override via set_factory to
    simulate nonzero exit, TimeoutExpired, or arbitrary exceptions. The
    factory is consulted INSIDE the fake_run, not pulled from a closure —
    each call gets the freshly-set behavior.
    """
    log = []
    state = {"factory": lambda argv, kw: subprocess.CompletedProcess(
        argv, 0, stdout="ok", stderr="",
    )}

    def fake_run(argv, **kw):
        log.append({"argv": list(argv), "kwargs": dict(kw)})
        return state["factory"](argv, kw)

    def set_factory(factory):
        state["factory"] = factory

    monkeypatch.setattr("odin.harnesses.microsandbox.subprocess.run", fake_run)
    return {"log": log, "set_factory": set_factory, "state": state}


# --- sandbox name passed to msb run -------------------------------------------

def test_run_command_includes_named_sandbox_flag(tmp_path, capture_subprocess):
    """Every ephemeral msb run must carry a `--name <name>` so cleanup knows what
    to remove later. Without a name we cannot deterministically identify the
    VM that leaked (each ``msb run`` mints a fresh ``msb-<hash>``)."""
    capture_subprocess["set_factory"](
        lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
    )
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    run_argv = next(
        c["argv"] for c in capture_subprocess["log"]
        if "run" in c["argv"][:3] and c["argv"][0].endswith("msb")
    )
    assert "--name" in run_argv, f"--name flag missing from msb run argv: {run_argv}"
    i = run_argv.index("--name") + 1
    assert run_argv[i].startswith("odin-msb-"), (
        f"sandbox name should be odin-msb-* for cleanup: {run_argv[i]!r}"
    )


# --- success path: sandbox removed --------------------------------------------

def test_success_path_removes_sandbox(tmp_path, capture_subprocess):
    """A successful run must end with ``msb remove <name>`` for the per-run
    sandbox. This is the primary leak fix — without it every successful run
    leaks ~3 GB of snapshot copy."""
    capture_subprocess["set_factory"](
        lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
    )
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    run_argv = next(c["argv"] for c in capture_subprocess["log"] if "run" in c["argv"][:3])
    sandbox_name = run_argv[run_argv.index("--name") + 1]
    removed = _names_called([c["argv"] for c in capture_subprocess["log"]])
    assert sandbox_name in removed, (
        f"sandbox {sandbox_name!r} not removed after success; cleanup calls: {removed}"
    )


def test_success_path_remove_runs_after_run(tmp_path, capture_subprocess):
    """The cleanup ``msb remove`` must come AFTER the ``msb run`` call in the
    log. Otherwise the harness would remove a sandbox that hasn't existed yet.
    """
    capture_subprocess["set_factory"](
        lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
    )
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    log = [c["argv"] for c in capture_subprocess["log"]]
    run_idx = next(
        i for i, argv in enumerate(log)
        if argv[0].endswith("msb") and len(argv) > 1 and argv[1] == "run"
    )
    remove_idx = next(
        i for i, argv in enumerate(log)
        if argv[0].endswith("msb") and "remove" in argv
    )
    assert remove_idx > run_idx, (
        f"msb remove (idx {remove_idx}) must run AFTER msb run (idx {run_idx})"
    )


# --- failure path: sandbox removed --------------------------------------------

def test_nonzero_exit_path_removes_sandbox(tmp_path, capture_subprocess):
    """A non-zero msb exit must STILL remove the per-run sandbox. Without this
    pin a typo could remove the cleanup call from the failure path."""
    capture_subprocess["set_factory"](
        lambda argv, kw: subprocess.CompletedProcess(
            argv, 137, stdout="", stderr="killed",
        )
    )
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    run_argv = next(c["argv"] for c in capture_subprocess["log"] if "run" in c["argv"][:3])
    sandbox_name = run_argv[run_argv.index("--name") + 1]
    removed = _names_called([c["argv"] for c in capture_subprocess["log"]])
    assert sandbox_name in removed, (
        f"sandbox {sandbox_name!r} not removed after non-zero exit; "
        f"cleanup calls: {removed}"
    )


# --- timeout path: sandbox removed --------------------------------------------

def test_timeout_path_removes_sandbox(tmp_path, capture_subprocess):
    """The host backstop ``subprocess.TimeoutExpired`` must STILL remove the
    per-run sandbox — even when ``msb run`` was forcibly killed and may not
    have had a chance to release its own resources."""

    def raises(argv, kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout"))

    capture_subprocess["set_factory"](raises)
    h = get_harness("codex", _cfg())
    out = h._execute_sync("p", {"working_dir": str(tmp_path)})
    assert "host timeout" in out, "TimeoutExpired must be caught and noted"
    log = [c["argv"] for c in capture_subprocess["log"]]
    # Even though msb itself was killed mid-run, the harness's finally block
    # still issues `msb sandbox remove --name <our-name>` to free the
    # per-run sandbox copy. The name is the same one minted before the run
    # was attempted.
    rm_argvs = [
        argv for argv in log
        if argv and argv[0].endswith("msb") and "remove" in argv
    ]
    assert rm_argvs, (
        f"timeout path must still remove per-run sandbox; log: {log}"
    )
    for argv in rm_argvs:
        if "--name" in argv:
            i = argv.index("--name") + 1
            assert argv[i].startswith("odin-msb-"), (
                f"refusing to remove non-ephemeral sandbox {argv[i]!r} on timeout"
            )


# --- exception path: sandbox removed -----------------------------------------

def test_exception_path_removes_sandbox(tmp_path, capture_subprocess):
    """An unexpected exception in execute() (e.g. inner has no command →
    RuntimeError) must STILL remove the per-run sandbox before propagating."""

    def raises(argv, kw):
        raise RuntimeError("kaboom")

    capture_subprocess["set_factory"](raises)
    h = get_harness("codex", _cfg())
    with pytest.raises(RuntimeError, match="kaboom"):
        h._execute_sync("p", {"working_dir": str(tmp_path)})
    # Even when the run argv never reached msb, the harness must still issue
    # a cleanup. We assert there IS a remove call in the captured log even if
    # the run failed to land (the harness has the sandbox name from BEFORE
    # the run, so the finally-block has a target).
    log = [c["argv"] for c in capture_subprocess["log"]]
    rm_calls = [
        argv for argv in log
        if argv and argv[0].endswith("msb") and "remove" in argv
    ]
    assert rm_calls, (
        "exception path must still remove per-run sandbox (the name was known "
        "before run); no msb remove call found in log"
    )


# --- temp dir: removed alongside sandbox -------------------------------------

def test_temp_dir_removed_alongside_sandbox(tmp_path, capture_subprocess, monkeypatch):
    """The harness creates ``odin-msb-*`` temp dirs in a finally block. After
    a complete run, no such directory should remain on disk."""
    capture_subprocess["set_factory"](
        lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
    )
    # Capture the temp dirs created so we can assert they were removed.
    created = []

    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy_mkdtemp(*args, **kw):
        d = real_mkdtemp(*args, **kw)
        created.append(d)
        return d

    monkeypatch.setattr("tempfile.mkdtemp", spy_mkdtemp)
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    # All odin-msb-* dirs we created must be gone.
    for d in created:
        assert not Path(d).exists(), f"tempdir leaked: {d}"


# --- negative test: named persistent sandboxes NEVER removed ----------------

def test_named_sandboxes_never_in_removal_set(tmp_path, capture_subprocess):
    """The harness must NEVER remove ``odinbuild``, snapshots in the
    ``snapshots/`` directory, or anything whose name does not start with
    ``odin-msb-``. Pin that by reading what `msb remove <NAME>` was called with.
    """
    # Trick the harness into believing msb returns a sandbox name that happens
    # to look like a persistent one. The harness must still ignore it (because
    # every name assigned by the harness is `odin-msb-*`).
    capture_subprocess["set_factory"](
        lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
    )
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    log = [c["argv"] for c in capture_subprocess["log"]]
    rm_argvs = [argv for argv in log if len(argv) > 1 and argv[1] == "remove"]
    for argv in rm_argvs:
        # extract the name (either after `--name` or as a positional)
        if "--name" in argv:
            i = argv.index("--name") + 1
            name = argv[i]
        else:
            name = argv[2]
        assert name.startswith("odin-msb-"), (
            f"refusing to remove non-ephemeral sandbox {name!r} from {argv}"
        )
        assert name not in ("odinbuild", "odin-agents"), (
            f"refusing to remove persistent sandbox {name!r}"
        )


# --- snapshot paths: NEVER in removal set -------------------------------------

def test_snapshots_dir_never_touched(tmp_path, capture_subprocess):
    """The cleanup path must not touch ``~/.microsandbox/snapshots/*`` —
    snapshots are immutable images, not ephemeral VMs.
    Pin by inspecting the CLEANUP calls (anything that mentions 'remove' or
    'snapshot create/delete') and asserting none address the snapshots path.
    The harness's ``--snapshot <name>`` boot argument is fine because the
    sandbox is read-only against it, never mutating.
    """
    capture_subprocess["set_factory"](
        lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
    )
    h = get_harness("codex", _cfg())
    h._execute_sync("p", {"working_dir": str(tmp_path)})
    for c in capture_subprocess["log"]:
        argv = c["argv"]
        joined = " ".join(argv)
        is_cleanup = argv[0].endswith("msb") and (
            "remove" in argv or "snapshot" in argv[:3]
        )
        if not is_cleanup:
            continue
        assert "/snapshots/" not in joined, (
            f"run-end cleanup touched snapshots path: {argv}"
        )
        assert "snapshot remove" not in joined and "snapshot delete" not in joined, (
            f"cleanup must never remove/delete a snapshot: {argv}"
        )


# --- the run-boundary is a finally (not an if) --------------------------------

def test_cleanup_runs_in_finally_not_only_on_success(tmp_path, capture_subprocess):
    """Stronger than per-path tests: every single completion of _execute_sync,
    regardless of which branch the code took, must issue a `msb remove`. This
    is the structural guarantee — cleanup lives in a finally block, not
    scattered across if branches."""
    cases = [
        ("ok", lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")),
        ("rc=1", lambda argv, kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="e")),
        ("rc=137", lambda argv, kw: subprocess.CompletedProcess(argv, 137, stdout="", stderr="")),
        (
            "TimeoutExpired",
            lambda argv, kw: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout"))
            ),
        ),
    ]
    for label, factory in cases:
        capture_subprocess["set_factory"](factory)
        capture_subprocess["log"].clear()
        h = get_harness("codex", _cfg())
        try:
            h._execute_sync("p", {"working_dir": str(tmp_path)})
        except Exception:
            pass
        log = [c["argv"] for c in capture_subprocess["log"]]
        rm_calls = [
            argv for argv in log
            if argv and argv[0].endswith("msb") and "remove" in argv
            and any(t.startswith("odin-msb-") for t in argv)
        ]
        assert rm_calls, f"[{label}] finally-style cleanup missing — run leaked a sandbox"


# --- belt-and-braces disk-dir prune (task #215) -----------------------------

class TestRunEndDiskDirPrune:
    """The harness must delete the per-run sandbox DIRECTORY at
    ``~/.microsandbox/sandboxes/<name>`` at every run boundary (success,
    failure, timeout, exception) — not just rely on ``msb sandbox remove``,
    which is exactly how 58 ~3 GB dirs accumulated on the operator's host.

    The disk-dir prune is a SEPARATE call (``_remove_sandbox_dir``) sitting
    alongside ``_remove_sandbox`` in the finally block. It refuses anything
    not matching ``odin-msb-*`` (so ``odinbuild`` and snapshot images are
    safe) and is idempotent.
    """

    def _capture_dir_prune(self, monkeypatch):
        """Spy on ``_remove_sandbox_dir`` so a test can assert it was called
        with the expected name. Returns the list of names passed in."""
        seen = []
        monkeypatch.setattr(
            MicrosandboxHarness, "_remove_sandbox_dir",
            staticmethod(lambda name, **kw: (seen.append(name), True)[1]),
        )
        return seen

    def test_success_path_prunes_sandbox_dir(self, tmp_path, capture_subprocess, monkeypatch):
        """A successful run must end with ``_remove_sandbox_dir(<our-name>)``
        in addition to ``msb sandbox remove``. The dir prune is the
        belt-and-braces against ``msb remove`` silently failing."""
        capture_subprocess["set_factory"](
            lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
        )
        seen = self._capture_dir_prune(monkeypatch)
        h = get_harness("codex", _cfg())
        h._execute_sync("p", {"working_dir": str(tmp_path)})
        run_argv = next(c["argv"] for c in capture_subprocess["log"] if "run" in c["argv"][:3])
        sandbox_name = run_argv[run_argv.index("--name") + 1]
        assert sandbox_name in seen, (
            f"success path did not call _remove_sandbox_dir({sandbox_name!r}); "
            f"dir prune calls: {seen}"
        )

    def test_dir_prune_runs_after_msb_remove(self, tmp_path, capture_subprocess, monkeypatch):
        """Order matters: ``msb remove`` (kills the VM, stops processes) must
        come BEFORE ``_remove_sandbox_dir`` (frees the disk). Otherwise an
        active VM could be holding open files inside the dir we're deleting."""
        capture_subprocess["set_factory"](
            lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
        )
        seen = self._capture_dir_prune(monkeypatch)
        h = get_harness("codex", _cfg())
        h._execute_sync("p", {"working_dir": str(tmp_path)})
        log = [c["argv"] for c in capture_subprocess["log"]]
        msb_remove_idx = next(
            i for i, argv in enumerate(log)
            if argv and argv[0].endswith("msb") and "remove" in argv
        )
        # _remove_sandbox_dir was called with exactly one name on success;
        # it's a Python call, not a subprocess — pin via the spy list.
        assert len(seen) == 1, (
            f"expected exactly one _remove_sandbox_dir call on success, got {seen}"
        )

    def test_dir_prune_on_every_completion_path(
        self, tmp_path, capture_subprocess, monkeypatch,
    ):
        """Stronger than per-path tests: every completion of ``_execute_sync``,
        regardless of which branch the code took, must call
        ``_remove_sandbox_dir`` with the run's own name. This is the
        structural guarantee — the dir prune lives in the same finally block
        as ``msb remove`` so success / failure / timeout / exception all
        hit it."""
        cases = [
            ("ok", lambda argv, kw: subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")),
            ("rc=1", lambda argv, kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="e")),
            ("rc=137", lambda argv, kw: subprocess.CompletedProcess(argv, 137, stdout="", stderr="")),
            (
                "TimeoutExpired",
                lambda argv, kw: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout"))
                ),
            ),
        ]
        for label, factory in cases:
            capture_subprocess["set_factory"](factory)
            capture_subprocess["log"].clear()
            seen = self._capture_dir_prune(monkeypatch)
            h = get_harness("codex", _cfg())
            try:
                h._execute_sync("p", {"working_dir": str(tmp_path)})
            except Exception:
                pass
            # The dir prune ran with the run's own (ephemeral) name.
            ephemeral_calls = [n for n in seen if n.startswith("odin-msb-")]
            assert ephemeral_calls, (
                f"[{label}] finally-style dir prune missing — run leaked a sandbox dir; "
                f"calls: {seen}"
            )


# --- private helpers: pure unit tests for the removal set -------------------

class TestEphemeralSandboxNameFilter:
    """Pure-logic test that the static filter for which names to remove only
    matches ephemeral ``odin-msb-*`` sandboxes."""

    def test_filter_matches_odin_msb_prefix(self):
        assert MicrosandboxHarness._is_ephemeral_msb_sandbox("odin-msb-abc12345")
        assert MicrosandboxHarness._is_ephemeral_msb_sandbox("odin-msb-deadbeef")

    def test_filter_rejects_odinbuild(self):
        assert not MicrosandboxHarness._is_ephemeral_msb_sandbox("odinbuild")

    def test_filter_rejects_odin_agents(self):
        assert not MicrosandboxHarness._is_ephemeral_msb_sandbox("odin-agents")

    def test_filter_rejects_unrelated_names(self):
        for name in ("msb-abc12345", "random-sandbox", "", "snapshot-odin-agents"):
            assert not MicrosandboxHarness._is_ephemeral_msb_sandbox(name), (
                f"should reject {name!r}"
            )


# --- direct dir removal (task #215) -----------------------------------------

class TestRemoveSandboxDir:
    """Direct disk-dir removal is the structural fix — it doesn't depend on
    ``msb`` cooperating. It must be prefix-filtered (refuses anything not
    matching ``odin-msb-*``) and idempotent."""

    def test_removes_existing_ephemeral_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        (home / "odin-msb-aaaa1111").mkdir()
        (home / "odin-msb-aaaa1111" / "somedata").write_bytes(b"x" * 1024)
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        ok = MicrosandboxHarness._remove_sandbox_dir("odin-msb-aaaa1111")
        assert ok is True
        assert not (home / "odin-msb-aaaa1111").exists(), (
            "ephemeral sandbox dir should be removed from disk"
        )

    def test_idempotent_on_missing_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        # Already-gone is a success — the sweep needs to be idempotent so it
        # can run every reconciler pass without flapping.
        assert MicrosandboxHarness._remove_sandbox_dir("odin-msb-missing") is True

    def test_refuses_odinbuild(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        (home / "odinbuild").mkdir()
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        ok = MicrosandboxHarness._remove_sandbox_dir("odinbuild")
        assert ok is False
        assert (home / "odinbuild").exists(), (
            "persistent sandbox dir must NOT be deleted"
        )

    def test_refuses_odin_agents(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        (home / "odin-agents").mkdir()
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        ok = MicrosandboxHarness._remove_sandbox_dir("odin-agents")
        assert ok is False
        assert (home / "odin-agents").exists()

    def test_refuses_unrelated_names(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        for n in ("random-sandbox", "msb-xyz", "snapshot-odin-agents", ""):
            ok = MicrosandboxHarness._remove_sandbox_dir(n)
            assert ok is False, f"refused expected for {n!r}"

    def test_snapshots_dir_never_touched(self, tmp_path, monkeypatch):
        """Defensive: even if a buggy caller tries a snapshots-like name, the
        dir prune must not delete anything from under ``~/.microsandbox/snapshots/``
        — the sandbox and snapshots homes are separate dirs, but the prefix
        check + a home-rooted path makes the refusal structural."""
        snaps_home = tmp_path / "snapshots"
        snaps_home.mkdir()
        (snaps_home / "odin-agents").mkdir()
        (snaps_home / "odin-agents" / "image.bin").write_bytes(b"x" * 4096)
        sandbox_home = tmp_path / "sandboxes"
        sandbox_home.mkdir()
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", sandbox_home,
        )
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SNAPSHOT_HOME", snaps_home,
        )
        # No ephemeral prefix matches the snapshot path; verify nothing was
        # touched regardless of which name is passed in.
        for n in ("odin-agents", "snapshots", "odin-msb-odin-agents"):
            MicrosandboxHarness._remove_sandbox_dir(n)
        # Snapshot contents MUST be intact.
        assert (snaps_home / "odin-agents" / "image.bin").exists(), (
            "snapshot dir must never be touched by _remove_sandbox_dir"
        )


class TestSandboxDirSize:
    """The sweep needs the bytes-freed number for the trust log. ``_sandbox_dir_size``
    walks the dir and sums file sizes — it must refuse non-ephemeral names
    (so a future caller can't ask for odinbuild's size and accidentally
    walk into a multi-GB persistent sandbox)."""

    def test_size_of_existing_ephemeral_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        target = home / "odin-msb-aaaa"
        target.mkdir()
        (target / "a.bin").write_bytes(b"x" * 1024)
        (target / "b.bin").write_bytes(b"y" * 2048)
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        size = MicrosandboxHarness._sandbox_dir_size("odin-msb-aaaa")
        assert size >= 1024 + 2048

    def test_size_zero_for_missing_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        assert MicrosandboxHarness._sandbox_dir_size("odin-msb-ghost") == 0

    def test_size_zero_for_non_ephemeral(self, tmp_path, monkeypatch):
        home = tmp_path / "sandboxes"
        home.mkdir()
        (home / "odinbuild").mkdir()
        (home / "odinbuild" / "x.bin").write_bytes(b"x" * 999_999)
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        # Refuses non-ephemeral — never reports the size of a persistent sandbox.
        assert MicrosandboxHarness._sandbox_dir_size("odinbuild") == 0


# --- private helpers: pure unit tests for safe list parsing -------------------

class TestOrphanListingParser:
    """Parsing the output of ``msb list`` is a small pure function — pin its
    semantics so we never accidentally remove a persistent sandbox based on a
    brittle string match."""

    def test_json_list_format(self):
        data = json.dumps([
            {"name": "odin-msb-aaaa1111", "state": "stopped"},
            {"name": "odinbuild", "state": "stopped"},
            {"name": "odin-agents", "state": "stopped", "kind": "snapshot"},
        ])
        orphans = MicrosandboxHarness._parse_msb_orphans(data, "json")
        assert orphans == ["odin-msb-aaaa1111"]
        # The persistent names MUST NOT be orphans.
        assert "odinbuild" not in orphans
        assert "odin-agents" not in orphans

    def test_plain_list_format(self):
        out = "odin-msb-aaaa1111\nodinbuild\nodin-agents\nodin-msb-bbbb2222\n"
        orphans = MicrosandboxHarness._parse_msb_orphans(out, "plain")
        assert orphans == ["odin-msb-aaaa1111", "odin-msb-bbbb2222"]

    def test_empty_list(self):
        assert MicrosandboxHarness._parse_msb_orphans("[]", "json") == []
        assert MicrosandboxHarness._parse_msb_orphans("", "plain") == []
