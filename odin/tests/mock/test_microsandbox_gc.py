"""Tests for the ``odin gc`` subcommand and the startup orphan sweep.

Scope of this file:

- ``odin gc`` (dry-run by default) reports disk usage:
  - microsandbox sandboxes (count + size, classified ephemeral vs named)
  - microsandbox snapshots (count + size; never touched)
  - git worktrees (count + size; node_modules sizes reported separately)
- ``odin gc --prune`` removes orphans only; never named sandboxes, never snapshots,
  never non-odin worktrees.
- ``MicrosandboxHarness.sweep_startup_orphans()`` (the backstop) issues
  ``msb remove <name>`` for each orphan returned by the orphan filter; never
  for any non-odin-msb-* name.
- Pure-logic helpers:
  - ``_classify_worktree(p)`` — returns whether a path is an odin-managed worktree
  - ``_is_node_modules_path(p)`` — keeps the heuristic honest
  - ``_partition_orphans(names)`` — refuses to remove anything not matching prefix
"""

import json
from pathlib import Path

from odin.harnesses.microsandbox import MicrosandboxHarness

# ---------- microsandbox orphan partition (pure logic) -------------------------

class TestPartitionOrphans:
    """The single safety net for ``msb remove``: only ``odin-msb-*`` may pass."""

    def test_keeps_only_odin_msb_prefixed(self):
        keep, drop = MicrosandboxHarness._partition_orphans(
            ["odin-msb-aaaa", "odin-msb-bbbb", "odinbuild", "odin-agents", "msb-xyz"]
        )
        assert keep == ["odin-msb-aaaa", "odin-msb-bbbb"]
        assert set(drop) == {"odinbuild", "odin-agents", "msb-xyz"}

    def test_empty_input(self):
        keep, drop = MicrosandboxHarness._partition_orphans([])
        assert keep == [] and drop == []


# ---------- startup sweep (backstop) -------------------------------------------

class TestStartupOrphanSweep:
    """``sweep_startup_orphans`` is the backstop for crashed runs (a crash
    cannot run its own finally). It MUST only remove ``odin-msb-*`` sandboxes;
    never persistent ones like ``odinbuild`` or snapshots."""

    def _install_fake_msb(
        self, monkeypatch, *,
        list_output: str,
        list_format: str = "json",
        live_names=(),
    ):
        """Patch the static helper that lists msb sandboxes + the remove helper.

        ``live_names`` is a set of names that have a live msb process owning
        them — the sweep must SKIP those (they are real VMs, not orphans)."""
        calls = []
        live_set = set(live_names)

        def fake_list(home=None, **kw):
            calls.append(("list", list_output, list_format))
            return MicrosandboxHarness._parse_msb_orphans(list_output, list_format)

        def fake_remove(name, **kw):
            calls.append(("remove", name))
            return True

        def fake_live(name, **kw):
            calls.append(("live_check", name))
            return name in live_set

        monkeypatch.setattr(MicrosandboxHarness, "_list_managed_sandboxes", staticmethod(fake_list))
        monkeypatch.setattr(MicrosandboxHarness, "_remove_sandbox", staticmethod(fake_remove))
        monkeypatch.setattr(MicrosandboxHarness, "_live_msb_process_exists", staticmethod(fake_live))
        monkeypatch.setattr(MicrosandboxHarness, "_remove_sandbox_dir", staticmethod(lambda n, **kw: calls.append(("dir_remove", n)) or True))
        monkeypatch.setattr(MicrosandboxHarness, "_sandbox_dir_size", staticmethod(lambda n, **kw: calls.append(("size", n)) or 1024))
        return calls

    def test_sweep_removes_only_ephemeral(self, monkeypatch):
        calls = self._install_fake_msb(
            monkeypatch,
            list_output=json.dumps([
                {"name": "odin-msb-aaaa1111"},
                {"name": "odin-msb-bbbb2222"},
                {"name": "odinbuild"},
                {"name": "odin-agents"},
            ]),
        )
        removed = MicrosandboxHarness.sweep_startup_orphans(dry_run=False)
        assert set(removed) == {"odin-msb-aaaa1111", "odin-msb-bbbb2222"}
        # Persistent names NEVER appear as a remove call.
        for entry in calls:
            # entries are (kind, *payload)
            if entry[0] == "remove":
                # in the test, fake_remove was registered as
                # fake_remove(name, **kw) → calls.append(("remove", name))
                name = entry[1]
                assert name in {"odin-msb-aaaa1111", "odin-msb-bbbb2222"}, (
                    f"sweep tried to remove protected sandbox {name!r}"
                )
                assert name not in {"odinbuild", "odin-agents"}, (
                    f"sweep tried to remove protected sandbox {name!r}"
                )

    def test_sweep_dry_run_returns_intent_without_calling(self, monkeypatch):
        calls = self._install_fake_msb(
            monkeypatch,
            list_output=json.dumps([{"name": "odin-msb-aaaa1111"}]),
        )
        intent = MicrosandboxHarness.sweep_startup_orphans(dry_run=True)
        assert intent == ["odin-msb-aaaa1111"]
        assert all(c[0] != "remove" for c in calls), (
            f"dry-run issued an msb remove: {calls}"
        )

    def test_sweep_handles_no_listing(self, monkeypatch):
        # If msb is absent or returns nothing, the backstop is a no-op.
        calls = self._install_fake_msb(monkeypatch, list_output="[]")
        removed = MicrosandboxHarness.sweep_startup_orphans(dry_run=False)
        assert removed == []
        assert all(c[0] != "remove" for c in calls)

    # ── task #215: belt-and-braces sweep (live-process sparing + bytes) ──

    def test_sweep_spares_live_vm(self, monkeypatch):
        """A sandbox whose msb process is still alive is a real VM, not an
        orphan. Killing its disk dir while the VM is running would corrupt
        state. The sweep MUST skip any name returned by
        ``_live_msb_process_exists``."""
        calls = self._install_fake_msb(
            monkeypatch,
            list_output=json.dumps([
                {"name": "odin-msb-live111"},
                {"name": "odin-msb-dead222"},
            ]),
            live_names={"odin-msb-live111"},
        )
        removed = MicrosandboxHarness.sweep_startup_orphans(dry_run=False)
        # Only the dead one was removed; the live VM was skipped.
        assert removed == ["odin-msb-dead222"]
        # Both names were checked — neither was silently skipped.
        live_checked = [c[1] for c in calls if c[0] == "live_check"]
        assert set(live_checked) == {"odin-msb-live111", "odin-msb-dead222"}
        # No msb remove OR dir prune for the live name.
        for entry in calls:
            if entry[0] in ("remove", "dir_remove"):
                assert entry[1] != "odin-msb-live111", (
                    f"sweep removed a live VM: {entry}"
                )

    def test_sweep_calls_dir_prune_alongside_msb_remove(self, monkeypatch):
        """Belt-and-braces: the sweep must nuke the on-disk dir even after
        ``msb sandbox remove`` — the latter is exactly how 58 leaked dirs
        accumulated on the operator's host."""
        calls = self._install_fake_msb(
            monkeypatch,
            list_output=json.dumps([{"name": "odin-msb-aaaa1111"}]),
        )
        MicrosandboxHarness.sweep_startup_orphans(dry_run=False)
        removed_names = {c[1] for c in calls if c[0] == "remove"}
        dir_removed_names = {c[1] for c in calls if c[0] == "dir_remove"}
        assert removed_names == {"odin-msb-aaaa1111"}
        assert dir_removed_names == {"odin-msb-aaaa1111"}, (
            f"every msb remove must be paired with a dir prune; got {calls}"
        )

    def test_sweep_report_returns_freed_bytes(self, monkeypatch):
        """The trust log + the operator-facing ``odin gc`` output want to
        show "X GiB reclaimed". ``sweep_startup_orphans_report`` returns
        the same names plus the bytes the sweep actually freed."""
        # Fake _sandbox_dir_size to return deterministic values per name.
        sizes = {
            "odin-msb-aaaa1111": 3 * 1024**3,
            "odin-msb-bbbb2222": 2 * 1024**3,
        }
        monkeypatch.setattr(MicrosandboxHarness, "_list_managed_sandboxes", staticmethod(
            lambda **kw: ["odin-msb-aaaa1111", "odin-msb-bbbb2222"]
        ))
        monkeypatch.setattr(MicrosandboxHarness, "_live_msb_process_exists", staticmethod(
            lambda name, **kw: False,
        ))
        monkeypatch.setattr(MicrosandboxHarness, "_remove_sandbox", staticmethod(
            lambda name, **kw: True,
        ))
        monkeypatch.setattr(MicrosandboxHarness, "_remove_sandbox_dir", staticmethod(
            lambda name, **kw: True,
        ))
        monkeypatch.setattr(MicrosandboxHarness, "_sandbox_dir_size", staticmethod(
            lambda name, **kw: sizes.get(name, 0),
        ))
        report = MicrosandboxHarness.sweep_startup_orphans_report()
        assert report["names_removed"] == ["odin-msb-aaaa1111", "odin-msb-bbbb2222"]
        assert report["bytes_freed"] == 5 * 1024**3
        # The trust log message exists for the operator.
        assert "message" in report

    def test_sweep_dry_run_does_not_prune_dirs(self, monkeypatch):
        """Dry-run returns intent only — no msb remove, no dir prune."""
        calls = self._install_fake_msb(
            monkeypatch,
            list_output=json.dumps([{"name": "odin-msb-aaaa1111"}]),
        )
        intent = MicrosandboxHarness.sweep_startup_orphans(dry_run=True)
        assert intent == ["odin-msb-aaaa1111"]
        # Nothing actually removed.
        assert not any(c[0] == "remove" for c in calls)
        assert not any(c[0] == "dir_remove" for c in calls)


# ---------- gc report (pure logic) --------------------------------------------

class TestGcReportShape:
    """The report shape the CLI prints / reads — pinned here so the
    CLI surface is stable."""

    def test_report_has_three_keys(self, monkeypatch):
        from odin import gc as gc_mod
        # Stub out the IO probes with deterministic data.
        monkeypatch.setattr(gc_mod, "_microsandbox_sandboxes", lambda: [
            {"name": "odin-msb-aaaa", "ephemeral": True, "size_bytes": 3 * 1024**3},
            {"name": "odinbuild", "ephemeral": False, "size_bytes": 4 * 1024**3},
        ])
        monkeypatch.setattr(gc_mod, "_microsandbox_snapshots", lambda: [
            {"name": "odin-agents", "size_bytes": 4 * 1024**3},
        ])
        monkeypatch.setattr(gc_mod, "_odin_worktrees", lambda project_root=None: [
            {
                "path": "/tmp/wt",
                "size_bytes": 100 * 1024**2,
                "node_modules_bytes": 1 * 1024**3,
                "has_changes": False,
                "managed": True,
            },
        ])
        report = gc_mod.collect_report()
        assert set(report.keys()) == {"sandboxes", "snapshots", "worktrees", "totals"}
        assert report["totals"]["ephemeral_sandboxes_count"] == 1
        assert report["totals"]["ephemeral_sandboxes_bytes"] == 3 * 1024**3
        assert report["totals"]["named_sandboxes_count"] == 1
        assert report["totals"]["snapshots_count"] == 1
        assert report["totals"]["worktrees_count"] == 1
        assert report["totals"]["node_modules_bytes"] == 1 * 1024**3

    def test_node_modules_size_separate_from_worktree(self, tmp_path, monkeypatch):
        # Build a fake worktree with a real node_modules to exercise the walk.
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "node_modules").mkdir()
        big = wt / "node_modules" / "big.txt"
        big.write_bytes(b"x" * (2 * 1024**2))  # 2 MB
        info = MicrosandboxHarness._size_breakdown(wt)
        assert info["node_modules_bytes"] >= 2 * 1024**2
        # And node_modules is NOT counted toward the worktree "rest"
        assert info["rest_bytes"] + info["node_modules_bytes"] == info["size_bytes"]

    def test_total_size_includes_node_modules_in_overall(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "node_modules").mkdir()
        (wt / "node_modules" / "f.txt").write_bytes(b"x" * (5 * 1024**2))
        info = MicrosandboxHarness._size_breakdown(wt)
        # overall must NOT double-count node_modules
        assert info["size_bytes"] == info["rest_bytes"] + info["node_modules_bytes"]


# ---------- gc prune (the actual removal) -------------------------------------

class TestGcPrune:
    """``prune_report`` removes orphans only; refuses for named sandboxes /
    snapshots / non-odin worktrees."""

    def _setup(self, monkeypatch, *, sandboxes, snapshots, worktrees):
        from odin import gc as gc_mod
        monkeypatch.setattr(gc_mod, "_microsandbox_sandboxes", lambda: sandboxes)
        monkeypatch.setattr(gc_mod, "_microsandbox_snapshots", lambda: snapshots)
        monkeypatch.setattr(gc_mod, "_odin_worktrees", lambda project_root=None: worktrees)
        # And stub the actual `msb remove` / `git worktree remove` IO so we can
        # observe what got removed without touching real files.
        removed_sandboxes = []
        removed_worktrees = []

        def fake_remove(name, **kw):
            removed_sandboxes.append(name)
            return True

        def fake_wt_remove(p, **kw):
            removed_worktrees.append(str(p))
            return True

        monkeypatch.setattr(
            MicrosandboxHarness, "_remove_sandbox", staticmethod(fake_remove),
        )
        monkeypatch.setattr(gc_mod, "_remove_worktree", fake_wt_remove)
        return {"removed_sandboxes": removed_sandboxes, "removed_worktrees": removed_worktrees}

    def test_dry_run_does_nothing(self, monkeypatch):
        from odin import gc as gc_mod
        # Empty sandbox list — collect_prune_plan should produce an empty
        # plan and the recorder must NOT see any remove calls. Pinned as
        # the docstring-default behavior of the CLI (`--prune` is opt-in).
        recorder = self._setup(
            monkeypatch, sandboxes=[], snapshots=[], worktrees=[],
        )
        actions = gc_mod.collect_prune_plan()
        assert actions == []  # nothing to reclaim
        assert recorder["removed_sandboxes"] == []



    def test_prune_only_removes_ephemeral_sandboxes(self, monkeypatch):
        from odin import gc as gc_mod
        sandbox_report = [
            {"name": "odin-msb-aaaa", "ephemeral": True, "size_bytes": 1024},
            {"name": "odin-msb-bbbb", "ephemeral": True, "size_bytes": 2048},
            {"name": "odinbuild", "ephemeral": False, "size_bytes": 999},
            {"name": "odin-agents", "ephemeral": False, "size_bytes": 888},
        ]
        # Snapshots live in a separate field — but if a snapshot mistakenly
        # ended up in sandboxes, the safety net must still refuse to remove.
        recorder = self._setup(
            monkeypatch, sandboxes=sandbox_report, snapshots=[], worktrees=[],
        )
        actions = gc_mod.execute_prune()
        names_removed = {a["name"] for a in actions if a["kind"] == "sandbox"}
        assert names_removed == {"odin-msb-aaaa", "odin-msb-bbbb"}
        assert recorder["removed_sandboxes"] == ["odin-msb-aaaa", "odin-msb-bbbb"]




    def test_prune_never_touches_snapshots(self, monkeypatch):
        from odin import gc as gc_mod
        self._setup(
            monkeypatch,
            sandboxes=[],
            snapshots=[
                {"name": "odin-agents", "size_bytes": 4 * 1024**3},
                {"name": "odin-glm-base", "size_bytes": 1 * 1024**3},
            ],
            worktrees=[],
        )
        actions = gc_mod.execute_prune()
        assert all(a["kind"] != "snapshot" for a in actions), (
            f"snapshots must not appear in prune actions: {actions}"
        )

    def test_prune_reports_estimated_reclaim(self, monkeypatch):
        from odin import gc as gc_mod
        self._setup(
            monkeypatch,
            sandboxes=[
                {"name": "odin-msb-aaaa", "ephemeral": True, "size_bytes": 3 * 1024**3},
            ],
            snapshots=[],
            worktrees=[],
        )
        actions = gc_mod.execute_prune()
        sandbox_actions = [a for a in actions if a["kind"] == "sandbox"]
        assert sandbox_actions
        assert sandbox_actions[0]["estimated_reclaim_bytes"] == 3 * 1024**3


# ---------- live integration smoke: the CLI command is wired ------------------

class TestGcCliContract:
    """The ``gc`` command must exist, default to dry-run, and never auto-prune.
    Tested via Fire's CLI wiring (smoke only — the rich output lives in the
    CLI surface, pinned separately in the integration test)."""

    def test_gc_command_present(self):
        from odin.cli import OdinCLI
        assert hasattr(OdinCLI, "gc")

    def test_gc_command_default_is_dry_run(self):
        import inspect

        from odin.cli import OdinCLI
        sig = inspect.signature(OdinCLI.gc)
        assert sig.parameters["prune"].default is False


# ---------- size breakdown: pure utility on a real tmp path -------------------

class TestSizeBreakdownEdgeCases:
    def test_missing_path_returns_zero(self, tmp_path):
        info = MicrosandboxHarness._size_breakdown(tmp_path / "nope")
        assert info["size_bytes"] == 0
        assert info["node_modules_bytes"] == 0
        assert info["rest_bytes"] == 0

    def test_only_node_modules(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "node_modules").mkdir()
        (wt / "node_modules" / "a").write_bytes(b"x" * 1024)
        info = MicrosandboxHarness._size_breakdown(wt)
        assert info["node_modules_bytes"] >= 1024
        assert info["rest_bytes"] == 0  # everything else is the dir entry itself


# ---------- disk-as-truth listing (task #215 review feedback) -----------------
#
# The original implementation had the sweep driven by ``msb sandbox list``
# output — but ``msb`` can return empty even when multi-GB dirs remain on
# disk (it tracks its own metadata independent of the per-VM disk copy,
# and that diverged exactly once already, leaving 58 ~3 GB orphans on the
# operator's host). The fix reads the disk directly. These tests pin that
# invariant: when ``msb`` reports nothing but the disk has orphan dirs,
# the sweep MUST still sweep them.

class TestDiskEphemeralListing:
    """``_list_disk_ephemeral_sandboxes`` and the backward-compatible
    ``_list_managed_sandboxes`` alias return ONLY what is on disk under
    the prefix-filtered sandbox home — never anything that ``msb``
    reports. The alias signature is what the orphan sweep consumes."""

    def _make_home(self, root: Path, names):
        home = root / "sandboxes"
        home.mkdir()
        for n in names:
            (home / n).mkdir()
        return home

    def test_disk_listing_reads_only_disk(
        self, tmp_path, monkeypatch,
    ):
        """The disk listing MUST NOT call ``msb`` at all. We patch
        ``_run_msb`` to raise if invoked — the listing is purely disk-based,
        so reaching for ``msb`` would be a regression."""
        home = self._make_home(tmp_path, [
            "odin-msb-aaaa1111", "odin-msb-bbbb2222", "odinbuild",
        ])
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )

        def explode(*a, **kw):
            raise AssertionError(
                f"_list_disk_ephemeral_sandboxes must not shell out to msb; got {a}"
            )

        monkeypatch.setattr(MicrosandboxHarness, "_run_msb", staticmethod(explode))
        names = MicrosandboxHarness._list_disk_ephemeral_sandboxes()
        assert set(names) == {"odin-msb-aaaa1111", "odin-msb-bbbb2222"}
        assert "odinbuild" not in names, "odinbuild is persistent — must not appear"

    def test_managed_sandboxes_alias_is_disk_truth(self, tmp_path, monkeypatch):
        """``_list_managed_sandboxes`` (the legacy name the sweep consumes)
        is now a thin alias for the disk-reading implementation. Even when
        a fake ``msb`` would have returned a non-empty list, the alias
        reads disk instead — that's the structural fix."""
        home = self._make_home(tmp_path, ["odin-msb-aaaa1111"])
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )

        def fake_run(args, **kw):
            import subprocess as sp
            return sp.CompletedProcess(
                args, 0,
                stdout='[{"name": "odin-msb-msb00001"}, {"name": "odin-msb-msb00002"}]',
                stderr="",
            )

        monkeypatch.setattr(MicrosandboxHarness, "_run_msb", staticmethod(fake_run))
        names = MicrosandboxHarness._list_managed_sandboxes()
        # Disk says ``odin-msb-aaaa1111``; even though the fake msb
        # returned two different names, the alias reads disk.
        assert names == ["odin-msb-aaaa1111"]

    def test_disk_listing_empty_when_home_missing(self, tmp_path, monkeypatch):
        missing = tmp_path / "no-such-sandbox-home"
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", missing,
        )
        assert MicrosandboxHarness._list_disk_ephemeral_sandboxes() == []


class TestMsbKnownCrossCheck:
    """``_list_msb_known_sandboxes`` returns what ``msb`` itself reports —
    used ONLY as a diagnostic cross-check inside the sweep, never as the
    removal list itself. The test verifies it parses both JSON and plain
    text outputs and applies the same prefix filter as the disk path."""

    def _install_fake_msb(self, monkeypatch, json_stdout=None, plain_stdout=None):
        """Build a ``_run_msb`` that returns ``json_stdout`` for the JSON
        listing call and ``plain_stdout`` for the plain-text fallback."""
        def fake_run(args, **kw):
            import subprocess as sp
            if args[:3] == ["sandbox", "list", "--json"] and json_stdout is not None:
                return sp.CompletedProcess(args, 0, stdout=json_stdout, stderr="")
            if args[:2] == ["sandbox", "list"] and plain_stdout is not None:
                return sp.CompletedProcess(args, 0, stdout=plain_stdout, stderr="")
            # Either nothing asked for, or both empty — produce a non-zero
            # exit so the cross-check returns [] rather than silently
            # returning the wrong list.
            return sp.CompletedProcess(args, 1, stdout="", stderr="msb missing")
        monkeypatch.setattr(MicrosandboxHarness, "_run_msb", staticmethod(fake_run))

    def test_parses_json_list(self, monkeypatch):
        self._install_fake_msb(
            monkeypatch,
            json_stdout=json.dumps([
                {"name": "odin-msb-aaaa1111"},
                {"name": "odinbuild"},
            ]),
        )
        assert MicrosandboxHarness._list_msb_known_sandboxes() == ["odin-msb-aaaa1111"]

    def test_falls_back_to_plain_text(self, monkeypatch):
        self._install_fake_msb(
            monkeypatch,
            plain_stdout="odin-msb-aaaa1111\nodinbuild\n",
        )
        assert MicrosandboxHarness._list_msb_known_sandboxes() == ["odin-msb-aaaa1111"]

    def test_empty_list_when_msb_silent(self, monkeypatch):
        self._install_fake_msb(monkeypatch)
        assert MicrosandboxHarness._list_msb_known_sandboxes() == []

    def test_empty_json_array_means_nothing_known(self, monkeypatch):
        """An explicit empty JSON array (``[]``) is a valid ``msb``
        response and must be treated as such — ``msb`` has nothing to
        report. Previously the implementation ``fall-through``-ed in this
        case and ended up reading disk instead, which was actually a
        happy accident; the cross-check method makes the empty-array
        contract honest."""
        self._install_fake_msb(monkeypatch, json_stdout="[]")
        assert MicrosandboxHarness._list_msb_known_sandboxes() == []


class TestSweepFindsOrphansMsbHasForgotten:
    """The structural fix: the sweep must discover and remove disk dirs
    even when ``msb sandbox list`` reports nothing. This is the exact
    scenario that left 58 ~3 GB orphans on the operator's host — the
    previous implementation trusted ``msb`` as the listing source and
    therefore never deleted them. The tests below run the REAL sweep,
    not a monkey-patched copy, and assert disk truth wins."""

    def _install_fake_msb_silent(
        self, monkeypatch, home: Path, *,
        remove_returns: bool = True,
        live_names=(),
    ):
        """Install a fake ``_run_msb`` that returns empty JSON for
        ``sandbox list`` (simulating "msb has dropped its record") and
        success for ``sandbox remove``. Real ``_remove_sandbox_dir`` is
        left untouched so it operates on the planted disk dirs.
        """
        import subprocess as sp
        live_set = set(live_names)
        calls = {"list": 0, "remove": []}

        def fake_run(args, **kw):
            if args[:3] == ["sandbox", "list", "--json"]:
                calls["list"] += 1
                return sp.CompletedProcess(args, 0, stdout="[]", stderr="")
            if args[:2] == ["sandbox", "list"]:
                calls["list"] += 1
                return sp.CompletedProcess(args, 0, stdout="", stderr="")
            if args[:3] == ["sandbox", "remove"] or args[:2] == ["sandbox", "remove"]:
                name_idx = args.index("remove") + 2  # skip "--name"
                name = args[name_idx]
                calls["remove"].append(name)
                return sp.CompletedProcess(
                    args,
                    0 if remove_returns else 1,
                    stdout="", stderr=""
                )
            return sp.CompletedProcess(args, 1, stdout="", stderr="")

        monkeypatch.setattr(MicrosandboxHarness, "_run_msb", staticmethod(fake_run))
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )

        def fake_live(name, **kw):
            return name in live_set

        monkeypatch.setattr(
            MicrosandboxHarness, "_live_msb_process_exists",
            staticmethod(fake_live),
        )
        return calls

    def test_sweep_discovers_dirs_msb_has_forgotten(
        self, tmp_path, monkeypatch, caplog,
    ):
        """Plant 2 disk-only orphan dirs (``msb sandbox list`` returns
        ``[]`` — exactly the failure mode that left 58 leaks). The REAL
        sweep, with the real ``_list_disk_ephemeral_sandboxes`` and the
        real ``_remove_sandbox_dir`` (only ``_run_msb`` is faked), must
        remove both dirs and report divergence to the operator log."""
        import logging
        home = tmp_path / "sandboxes"
        home.mkdir()
        for n in ("odin-msb-forgotten1", "odin-msb-forgotten2"):
            d = home / n
            d.mkdir()
            (d / "leftover.bin").write_bytes(b"x" * 4096)
        # Persistent sandbox that must NEVER be touched.
        (home / "odinbuild").mkdir()
        (home / "odinbuild" / "keepme.bin").write_bytes(b"x" * 64)
        calls = self._install_fake_msb_silent(monkeypatch, home)

        with caplog.at_level(logging.WARNING, logger="odin.microsandbox"):
            report = MicrosandboxHarness.sweep_startup_orphans_report()

        # msb sees nothing — but disk has the orphans. The new sweep
        # must find them via the disk path.
        assert set(report["names_removed"]) == {
            "odin-msb-forgotten1", "odin-msb-forgotten2"
        }, (
            f"disk-truth sweep missed orphans msb had forgotten: {report}"
        )
        assert not (home / "odin-msb-forgotten1").exists()
        assert not (home / "odin-msb-forgotten2").exists()
        # odinbuild preserved.
        assert (home / "odinbuild" / "keepme.bin").exists(), (
            "sweep removed persistent odinbuild despite the prefix filter"
        )
        # The divergence log line names BOTH leaked orphans.
        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "odin-msb-forgotten1" in m and "odin-msb-forgotten2" in m
            for m in msgs
        ), f"missing msb-divergence log; records: {msgs}"
        # msb sandbox remove was called for both (the belt-and-braces).
        assert set(calls["remove"]) == {
            "odin-msb-forgotten1", "odin-msb-forgotten2"
        }

    def test_sweep_uses_disk_listing_when_msb_returns_empty(
        self, tmp_path, monkeypatch,
    ):
        """Tighter pin: the sweep's names source is the disk-truth
        listing (``_list_managed_sandboxes``, now an alias for the
        disk path). A test that calls the disk listing directly must
        return the planted dir even when msb's response is empty.
        """
        home = tmp_path / "sandboxes"
        home.mkdir()
        (home / "odin-msb-direct1").mkdir()
        # Mock ``_run_msb`` to simulate "msb has forgotten every VM".
        import subprocess as sp
        def fake_run(args, **kw):
            return sp.CompletedProcess(args, 0, stdout="[]", stderr="")
        monkeypatch.setattr(MicrosandboxHarness, "_run_msb", staticmethod(fake_run))
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        names = MicrosandboxHarness._list_managed_sandboxes()
        assert names == ["odin-msb-direct1"], (
            f"disk-truth listing missed planted dir even though the dir "
            f"is on disk; got {names}"
        )

    def test_sweep_handles_missing_msb_binary(self, tmp_path, monkeypatch):
        """Pure disk path: if ``msb`` is not on PATH (``_run_msb`` returns
        a failed-completed-process), the sweep must still find disk dirs.
        This is the host with a real msb daemon but a broken Python PATH."""
        home = tmp_path / "sandboxes"
        home.mkdir()
        (home / "odin-msb-orphaned").mkdir()
        (home / "odin-msb-orphaned" / "x").write_bytes(b"x" * 4096)
        # _run_msb returns rc=-1 from the defensive fallback the helper
        # already has — but we wrap it so the body under test sees an
        # empty result from list and removes that pretend-succeed.
        import subprocess as sp
        def fake_run(args, **kw):
            if args[:3] == ["sandbox", "list", "--json"] or args[:2] == ["sandbox", "list"]:
                return sp.CompletedProcess(args, 0, stdout="", stderr="no daemon")
            return sp.CompletedProcess(args, 0, stdout="", stderr="")
        monkeypatch.setattr(MicrosandboxHarness, "_run_msb", staticmethod(fake_run))
        monkeypatch.setattr(
            "odin.harnesses.microsandbox._DEFAULT_SANDBOX_HOME", home,
        )
        removed = MicrosandboxHarness.sweep_startup_orphans()
        assert removed == ["odin-msb-orphaned"]
        assert not (home / "odin-msb-orphaned").exists()
