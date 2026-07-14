"""Microsandbox harness — run an agent CLI inside a libkrun microVM.

Decorator harness (peer to :class:`ForkdHarness`): wraps an inner CLI harness and
runs its command inside a per-task microVM via the ``msb run`` CLI. Unlike forkd,
microsandbox runs on macOS (HVF) as well as Linux (KVM), boots in <100ms, and needs
no daemon, sudo, or TAP networking. The host worktree is bind-mounted in, the agent
runs confined (the guest cannot see the host filesystem), and edits persist back to
the host worktree.

Staging mirrors forkd but uses microsandbox's simpler primitives:
- workspace + credentials + MCP config are bind-mounted (``-v``), not tarballed;
- the guest reaches the host TaskIt backend by LAN IP + a scoped ``--net-rule``,
  so forkd's HTTP proxy shim is unnecessary;
- agent CLIs are baked into the snapshot image, not staged per task.

The inner harness is only asked for its CLI command (``build_execute_command``).
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from odin.harnesses.base import (
    BaseHarness,
    extract_text_from_line,
    extract_text_from_stream,
    validate_odin_status,
    validate_odin_status_full,
)
from odin.models import AgentConfig, TaskResult

_GUEST_HOME = "/root"  # microsandbox guests run as root

# All ephemeral per-run sandboxes the harness creates MUST start with this
# prefix — it is the single safety net that lets `_partition_orphans` and
# `sweep_startup_orphans` distinguish "this VM was created by a (now-gone)
# run" from "this VM is a deliberate persistent sandbox like ``odinbuild``
# or the image used to build snapshots". Never loosen the prefix.
_EPHEMERAL_SANDBOX_PREFIX = "odin-msb-"

# Persistent sandboxes and snapshot images that exist alongside ours; these
# names must NEVER appear in any removal set. Treat this list as the deny-list
# override over the prefix filter (defensive — the prefix alone is enough).
_PROTECTED_SANDBOX_NAMES = frozenset({"odinbuild"})

_DEFAULT_SANDBOX_HOME = Path("~/.microsandbox/sandboxes")
_DEFAULT_SNAPSHOT_HOME = Path("~/.microsandbox/snapshots")

# The canonical trace file is bind-mounted into the guest at this path and tee'd
# from INSIDE the VM. msb buffers guest stdout and discards it entirely on a
# timeout-kill (proven live 2026-07-04) — the bind mount is the only channel that
# both streams in real time (virtio-fs) and survives the VM being killed.
_GUEST_TRACE_FILE = "/odin-trace.jsonl"

# opencode's `run --format json` prints ONE final blob — nothing mid-run. With the
# live-trace channel active, inject --print-logs so its stderr INFO stream (merged
# via 2>&1 | tee) shows live progress (proven: logs arrive within seconds of boot).
_LIVE_TRACE_FLAGS = {
    "glm": ["--print-logs", "--log-level", "INFO"],
    "minimax": ["--print-logs", "--log-level", "INFO"],
}

# Per-agent host credential paths (relative to $HOME) to mount read-only into the
# guest. Mirrors forkd's _CREDENTIAL_PATHS. Missing paths are skipped.
_CREDENTIAL_PATHS = {
    "claude": [".claude", ".claude.json"],
    "codex": [".codex/auth.json", ".codex/config.toml"],
    "gemini": [".gemini", ".config/google-cloud"],
    # agy (Google Antigravity) deliberately mounts NOTHING: its OAuth token is not
    # in a file but in the OS credential store (macOS Keychain / Linux Secret
    # Service). The harness reads the host credential and seeds a per-run
    # gnome-keyring inside the guest (see _agy_token / _extra_env / the guest
    # bootstrap). Read-only mounts of ~/.gemini would also block agy writing its
    # own state dir in-guest. (#125 root cause: the old file-mount design never
    # supplied a token → "not logged into Antigravity".)
    "glm": [
        ".config/opencode/opencode.jsonc",
        ".local/share/opencode/auth.json",
        ".local/share/opencode/account.json",
    ],
    "minimax": [
        ".config/opencode/opencode.jsonc",
        ".local/share/opencode/auth.json",
        ".local/share/opencode/account.json",
        ".local/share/kilo/auth.json",
    ],
}

# Allow-all flags injected into the inner command — safe because the agent is
# confined to the microVM. Only agents that (a) need a bypass for non-interactive
# use and (b) accept the flag. opencode-family (glm/minimax) `run` is already
# non-interactive and rejects unknown flags, so they get nothing (proven live).
# codex already carries its own bypass from CodexHarness; listed here for the
# rare fake-command path, injected idempotently.
_SANDBOX_BYPASS_FLAGS = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "gemini": ["--skip-trust"],
    # agy 1.0.x's single non-interactive gate (proven against the real CLI in
    # task #110 — the legacy `--headless --approve all` pair is rejected). The
    # AgyHarness also emits this flag, so _inject_bypass_flags() is idempotent.
    "agy": ["--dangerously-skip-permissions"],
}

# agy authenticates against Google from inside the guest by reading its OAuth
# token from the freedesktop Secret Service (go-keyring's Linux backend). There is
# no daemon in a fresh microVM, so before agy runs we stand up a per-run,
# empty-password gnome-keyring under a private D-Bus session and seed the token
# (passed in via the AGY_SECRET env, never inlined) under the exact service/account
# go-keyring looks up: service=gemini, username=antigravity. Proven live 2026-07-06.
# All steps are best-effort (`2>/dev/null`); if AGY_SECRET is unset agy just fails
# auth as before, without breaking the wrapper. Requires gnome-keyring +
# libsecret-tools + dbus-x11 in the image (extend_image_agy_keyring.sh).
# The whole prologue is grouped with its stdout/stderr sent to /dev/null so the
# daemon's env echo and any keyring chatter never reach agy's output/trace; the
# command substitution still captures the daemon env for `eval` (it reads the
# inner stdout directly, unaffected by the group's outer redirect).
_AGY_KEYRING_BOOTSTRAP = (
    "{ "
    'mkdir -p "$HOME/.local/share/keyrings"; '
    'rm -f "$HOME/.local/share/keyrings/login.keyring"; '
    "printf 'login\\n' > \"$HOME/.local/share/keyrings/default\"; "
    'eval "$(printf \'\\n\' | gnome-keyring-daemon --unlock '
    '--components=secrets,pkcs11 --daemonize 2>/dev/null)"; '
    'if [ -n "$AGY_SECRET" ]; then printf \'%s\' "$AGY_SECRET" | '
    "secret-tool store --label=gemini service gemini username antigravity; fi; "
    "} >/dev/null 2>&1; "
)

# Per-agent provider API-key env vars: passed into the guest when set on the host.
_API_KEY_ENV_VARS = {
    "glm": ("ZAI_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "codex": ("OPENAI_API_KEY",),
}

# Agents that use the opencode CLI and need per-task XDG isolation to prevent
# concurrent dispatches racing on shared SQLite databases.
_OPENCODE_FAMILY = {"glm", "minimax"}

# Guest paths for per-task XDG directories (under /mnt which survives bind-mounts;
# /tmp is tmpfs and swallows them — proven for MCP config staging).
_GUEST_XDG_CONFIG = "/mnt/odin-xdg-config"
_GUEST_XDG_DATA = "/mnt/odin-xdg-data"


def _detect_host_ip() -> Optional[str]:
    """The host's LAN IP — the address a microVM guest can reach the host by
    (the guest cannot reach the host's 127.0.0.1)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _mirror_extracted_to_out(trace_path: str, output_path: str, stop: threading.Event) -> None:
    """Tail the growing bind-mounted trace file and mirror extracted text into the
    ``.out`` file — this is what makes ``odin logs -f <task>`` live for sandboxed
    runs (same contract as ``base.read_with_trace`` for host runs)."""
    pos = 0
    pending = ""
    try:
        with open(output_path, "w") as of:
            while True:
                chunk = ""
                try:
                    with open(trace_path, "r", errors="replace") as tf:
                        tf.seek(pos)
                        chunk = tf.read()
                        pos = tf.tell()
                except OSError:
                    pass
                if chunk:
                    pending += chunk
                    lines = pending.split("\n")
                    pending = lines.pop()  # keep the trailing partial line
                    for ln in lines:
                        text = extract_text_from_line(ln)
                        if text:
                            of.write(text)
                            of.flush()
                elif stop.is_set():
                    if pending:
                        text = extract_text_from_line(pending)
                        if text:
                            of.write(text)
                    return
                stop.wait(1.0)
    except OSError:
        return


def _to_text(data) -> str:
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""


def guest_reachable_url(url: str, host_ip_override: Optional[str] = None) -> str:
    """Rewrite a host-local URL (127.0.0.1 / localhost) to the host's LAN IP so a
    microVM guest can reach it. Used at MCP-config generation time so workspace
    configs (opencode.json, .codex/config.toml) are born guest-reachable — no
    placeholder rewriting needed. Returns the URL unchanged when no IP is found."""
    ip = host_ip_override or _detect_host_ip()
    if not ip:
        return url
    return url.replace("127.0.0.1", ip).replace("localhost", ip)


class MicrosandboxHarness(BaseHarness):
    """Wrap an inner CLI harness and execute it inside a microsandbox microVM."""

    def __init__(self, name: str, inner: BaseHarness, config: AgentConfig):
        super().__init__(config)
        self.harness_name = name
        self.inner = inner

    @property
    def name(self) -> str:
        return f"{self.inner.name} (microsandbox)"

    def build_execute_command(self, prompt: str, context: dict) -> Optional[List[str]]:
        # Force the orchestrator down the execute() path — tmux cannot wrap a VM flow.
        return None

    def build_interactive_command(
        self, system_prompt_file: str, context: dict
    ) -> Optional[List[str]]:
        # Planning runs on the host; only task execution runs inside the VM.
        return self.inner.build_interactive_command(system_prompt_file, context)

    @property
    def supports_system_prompt_flag(self) -> bool:
        return bool(getattr(self.inner, "supports_system_prompt_flag", False))

    async def is_available(self) -> bool:
        if not self._microsandbox_bin():
            return False
        return self.inner.build_execute_command("availability-probe", {}) is not None

    async def execute(self, prompt: str, context: dict) -> TaskResult:
        import asyncio

        start = time.monotonic()
        try:
            output = await asyncio.to_thread(self._execute_sync, prompt, context)
            stdout_text = extract_text_from_stream(output)
            duration = (time.monotonic() - start) * 1000
            metadata = {"sandbox": "microsandbox"}
            status_obj = None
            if context.get("validate_status", True):
                status_obj = validate_odin_status_full(
                    stdout_text,
                    worktree_path=context.get("working_dir"),
                )
                success, error = status_obj.as_legacy_tuple()
            else:
                success, error = True, None
            if status_obj is not None and status_obj.raw_block is not None:
                metadata["malformed_status"] = {
                    "raw_block": status_obj.raw_block,
                    "inferred": status_obj.inferred,
                    "inference_reason": status_obj.inference_reason,
                }
            self._write_trace_files(
                context, raw_output=output, extracted_output=stdout_text
            )
            return TaskResult(
                success=success,
                output=stdout_text,
                error=error,
                duration_ms=round(duration, 1),
                agent=self.name,
                metadata=metadata,
            )
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            return TaskResult(
                success=False,
                output="",
                error=str(exc),
                duration_ms=round(duration, 1),
                agent=self.name,
                metadata={"sandbox": "microsandbox"},
            )

    # --- staging helpers ---------------------------------------------------

    def _microsandbox_bin(self) -> Optional[str]:
        """Resolve the `msb` binary path, or None if microsandbox isn't installed."""
        import shutil

        if self.config.microsandbox_bin:
            return self.config.microsandbox_bin
        found = shutil.which("msb")
        if found:
            return found
        for cand in (
            Path.home() / ".local/bin/msb",
            Path.home() / ".microsandbox/bin/msb",
        ):
            if cand.exists():
                return str(cand)
        return None

    def _host_ip(self) -> Optional[str]:
        """LAN IP the guest can reach the host by (config override or auto-detect)."""
        return self.config.microsandbox_host_ip or _detect_host_ip()

    def _inject_bypass_flags(self, cmd: List[str]) -> List[str]:
        flags = _SANDBOX_BYPASS_FLAGS.get(self.harness_name, [])
        existing = set(cmd)
        return cmd + [f for f in flags if f not in existing]

    def _inject_live_log_flags(self, cmd: List[str]) -> List[str]:
        """Insert the per-agent live-progress flags right after the subcommand
        (opencode wants flags after ``run``). No-op when the agent has none or
        the flags are already present."""
        flags = _LIVE_TRACE_FLAGS.get(self.harness_name, [])
        if not flags or flags[0] in cmd:
            return cmd
        try:
            i = cmd.index("run") + 1
        except ValueError:
            return cmd
        return cmd[:i] + flags + cmd[i:]

    def _credential_mounts(
        self, xdg_config_dir: Optional[str] = None, xdg_data_dir: Optional[str] = None,
    ) -> List[str]:
        """Read-only ``-v`` mounts of the agent's host credentials into the guest.

        Claude on macOS is special: its OAuth token lives in the Keychain (not a
        file), so nothing is mounted — the token is passed via ``CLAUDE_CODE_OAUTH_TOKEN``
        env instead (see :meth:`_extra_env`). On Linux, claude's ``.credentials.json``
        is a real file inside ``~/.claude`` and mounts normally.

        For opencode-family agents (glm/minimax), credentials mount into per-task
        XDG staging directories when provided, so each concurrent dispatch gets its
        own writable XDG tree instead of racing on ``/root``.
        """
        if self.harness_name == "claude" and sys.platform == "darwin":
            return []
        home = Path.home()
        mounts: List[str] = []
        use_xdg = self.harness_name in _OPENCODE_FAMILY and xdg_config_dir and xdg_data_dir
        for rel in _CREDENTIAL_PATHS.get(self.harness_name, []):
            host_path = home / rel
            if not host_path.exists():
                continue
            if use_xdg:
                if rel.startswith(".config/"):
                    guest_dest = f"{xdg_config_dir}/{rel[len('.config/'):]}"
                else:
                    guest_dest = f"{xdg_data_dir}/{rel[len('.local/share/'):]}"
                mounts += ["-v", f"{host_path}:{guest_dest}:ro"]
            else:
                mounts += ["-v", f"{host_path}:{_GUEST_HOME}/{rel}:ro"]
        return mounts

    def _workspace_mounts(
        self, workdir_host: str, read_only: bool = False
    ) -> Tuple[List[str], str]:
        """Mount flags + guest workdir for the task workspace.

        Plain directory: mount at ``microsandbox_workspace_mount`` (/workspace).

        Linked git worktree (odin task workspaces — ``.git`` is a *file* with a
        ``gitdir:`` pointer into the main repo's ``.git/worktrees/<n>``): mount the
        worktree AND the main repo's ``.git`` at their **identical host paths** so
        git resolves inside the guest, and use the host path as the workdir (no
        path rewriting). With only the worktree mounted, git is dead in-guest
        ("fatal: not a git repository") — glm burned 2×30 min on that (F30).

        ``read_only`` bind-mounts every repo path ``:ro`` — used by reflection so a
        reviewer can READ the worktree/diff but never mutate the repo. Execution
        runs pass the default (``:rw``) so agents can still commit.
        """
        mode = "ro" if read_only else "rw"
        wt = Path(workdir_host).resolve()
        dotgit = wt / ".git"
        if dotgit.is_file():
            try:
                pointer = dotgit.read_text().strip()
            except OSError:
                pointer = ""
            if pointer.startswith("gitdir:"):
                gitdir = Path(pointer.split(":", 1)[1].strip())
                main_git = gitdir.parent.parent  # <repo>/.git/worktrees/<n> → <repo>/.git
                if main_git.name == ".git" and main_git.is_dir():
                    mounts = [
                        "-v", f"{wt}:{wt}:{mode}",
                        "-v", f"{main_git}:{main_git}:{mode}",
                    ]
                    return mounts, str(wt)
        guest = self.config.microsandbox_workspace_mount
        return ["-v", f"{wt}:{guest}:{mode}"], guest

    def _claude_token_files(self, context: Optional[dict] = None) -> List[Path]:
        """Candidate ``.claude-token`` locations (highest precedence first). The token
        is written here by the TaskIt settings UI (into the board's working_dir) or by
        hand (`claude setup-token > .claude-token`), kept out of git, and read host-side
        — never mounted into the agent's workspace."""
        ctx = context or {}
        return claude_token_candidate_paths(
            working_dir=ctx.get("working_dir"),
            explicit_file=self.config.microsandbox_claude_token_file,
        )

    def _claude_keychain_token(self) -> Optional[str]:
        """Live OAuth access token of the host's logged-in ``claude`` CLI, read from
        the macOS Keychain (service ``Claude Code-credentials``, ``claudeAiOauth``
        payload — same source OpenClaw/hermes-agent read).

        This is fresher than any hand-pasted ``.claude-token``: the CLI refreshes it
        on use, and its refresh tokens are single-use, so we only ever *read* here —
        never refresh — and only hand the guest the short-lived access token. Returns
        None off-macOS, when the keychain is unreadable (locked / headless daemon),
        or when the token is expired or inside a 60s expiry buffer (fix: run any
        ``claude`` command on the host to refresh the keychain).
        """
        if sys.platform != "darwin":
            return None
        try:
            proc = subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return None
            creds = json.loads(proc.stdout.strip()).get("claudeAiOauth") or {}
            tok = creds.get("accessToken")
            expires_at_ms = creds.get("expiresAt")
            if not tok:
                return None
            if expires_at_ms and time.time() * 1000 >= expires_at_ms - 60_000:
                return None
            return tok
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    @staticmethod
    def _read_token_file(path: Path) -> Optional[str]:
        try:
            if path.is_file():
                tok = path.read_text().strip()
                if tok:
                    return tok
        except OSError:
            pass
        return None

    def _claude_token(self, context: Optional[dict] = None) -> Optional[str]:
        """Resolve the claude OAuth token, freshest-first: env var → explicitly
        configured token file → macOS Keychain (live CLI credential) → discovered
        ``.claude-token`` files (TaskIt UI drops / setup-token by hand)."""
        tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if tok:
            return tok
        if self.config.microsandbox_claude_token_file:
            tok = self._read_token_file(
                Path(self.config.microsandbox_claude_token_file).expanduser())
            if tok:
                return tok
        tok = self._claude_keychain_token()
        if tok:
            return tok
        for cand in self._claude_token_files(context):
            tok = self._read_token_file(cand)
            if tok:
                return tok
        return None

    def _agy_token(self, context: Optional[dict] = None) -> Optional[str]:
        """Resolve agy's OAuth credential as the raw JSON to seed into the guest's
        Secret Service. Order: env ``AGY_SECRET`` → configured token file → host
        credential store.

        The host store differs by platform, but the guest is always Linux, so we
        always hand it the DECODED JSON (the darwin ``go-keyring-base64:`` wrapper
        is a macOS-only encoding — the Linux go-keyring backend stores/reads raw):
          - macOS: ``security find-generic-password -s gemini -w`` → strip the
            ``go-keyring-base64:`` prefix → base64-decode.
          - Linux host: ``secret-tool lookup service gemini username antigravity``
            (already the raw JSON).
        Returns None when no credential is found (agy then fails auth in-guest).
        """
        import base64

        tok = os.environ.get("AGY_SECRET")
        if tok:
            return tok
        cfg_file = getattr(self.config, "microsandbox_agy_token_file", None)
        if cfg_file:
            tok = self._read_token_file(Path(cfg_file).expanduser())
            if tok:
                return tok
        try:
            if sys.platform == "darwin":
                proc = subprocess.run(
                    ["security", "find-generic-password", "-s", "gemini", "-w"],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode != 0:
                    return None
                raw = proc.stdout.strip()
                prefix = "go-keyring-base64:"
                if raw.startswith(prefix):
                    return base64.b64decode(raw[len(prefix):]).decode("utf-8")
                return raw or None
            proc = subprocess.run(
                ["secret-tool", "lookup", "service", "gemini",
                 "username", "antigravity"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return None
            return proc.stdout.strip() or None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def _extra_env(self, context: Optional[dict] = None) -> List[str]:
        """`-e KEY=value` flags: claude/agy OAuth token + provider API keys when present."""
        env: List[str] = []
        if self.harness_name == "claude":
            tok = self._claude_token(context)
            if tok:
                env += ["-e", f"CLAUDE_CODE_OAUTH_TOKEN={tok}"]
            # The guest runs as root; claude refuses --dangerously-skip-permissions as
            # root unless it detects a sandbox. We genuinely are a microVM sandbox.
            env += ["-e", "IS_SANDBOX=1"]
        if self.harness_name == "agy":
            tok = self._agy_token(context)
            if tok:
                env += ["-e", f"AGY_SECRET={tok}"]
        if self.harness_name in _OPENCODE_FAMILY:
            xdg_config = (context or {}).get("_xdg_config_dir")
            xdg_data = (context or {}).get("_xdg_data_dir")
            if xdg_config:
                env += ["-e", f"XDG_CONFIG_HOME={xdg_config}"]
            if xdg_data:
                env += ["-e", f"XDG_DATA_HOME={xdg_data}"]
        provider_vars = _API_KEY_ENV_VARS.get(self.harness_name, ())
        for var in provider_vars:  # host-set provider keys pass through
            val = os.environ.get(var)
            if val:
                env += ["-e", f"{var}={val}"]
        if provider_vars and self.config.api_key:  # explicit config key wins
            env += ["-e", f"{provider_vars[0]}={self.config.api_key}"]
        return env

    def _net_flags(self, context: dict) -> List[str]:
        """Egress policy. No flags = msb's implicit allow@public (provider APIs work,
        host LAN blocked). When the task needs the host TaskIt MCP, open egress with
        ``--net-default-egress allow``.

        Why not a scoped allowlist (F31, proven live 2026-07-04): once ANY --net-rule
        exists, msb's DNS forwarder only resolves DOMAINS that have an explicit
        domain rule — IP-group rules like ``allow@public`` do NOT unlock DNS, so
        provider APIs die with "Name or service not known". Scoped per-provider
        domain allowlists are initiative 07.3; user-configured rules still win.
        """
        cfg = self.config
        flags: List[str] = []
        rules = list(cfg.microsandbox_net_rules or [])
        net_default = cfg.microsandbox_net_default
        needs_host = bool(context.get("mcp_config") or context.get("taskit_base_url"))
        if needs_host and not rules and not net_default:
            return ["--net-default-egress", "allow"]
        if net_default:
            flags += ["--net-default", net_default]
        for r in rules:
            flags += ["--net-rule", r]
        return flags

    def _stage_mcp_config(
        self, context: dict, cmd: List[str], tmpdir: str
    ) -> Tuple[List[str], List[str]]:
        """Mount the per-task MCP config into the guest, rewriting the TaskIt URL to
        the host LAN IP (guest can't reach 127.0.0.1). Returns (cmd, extra_mounts)."""
        mcp = context.get("mcp_config")
        if not mcp:
            return cmd, []
        host_path = Path(str(mcp)).expanduser()
        if not host_path.is_file():
            return cmd, []
        data = host_path.read_text()
        host_ip = self._host_ip()
        if host_ip:
            data = data.replace("127.0.0.1", host_ip).replace("localhost", host_ip)
        staged = Path(tmpdir) / "mcp-config.json"
        staged.write_text(data)
        # Stage OUTSIDE the guest's /tmp: /tmp is a tmpfs in the microVM that the
        # kernel mounts over any bind-mount placed under it, so a config staged to
        # /tmp/... is invisible to the agent (claude then aborts: "MCP config file
        # not found"). /mnt is a plain dir a file bind-mount survives in (proven live).
        guest_mcp = "/mnt/odin-mcp-config.json"
        mounts = ["-v", f"{staged}:{guest_mcp}:ro"]
        cmd = [p.replace(str(host_path), guest_mcp) for p in cmd]
        return cmd, mounts

    def _build_msb_command(
        self,
        inner_cmd: List[str],
        workdir_host: str,
        context: Optional[dict] = None,
        extra_mounts: Optional[List[str]] = None,
        live_trace: Optional[str] = None,
        sandbox_name: Optional[str] = None,
    ) -> List[str]:
        """Construct the ``msb run`` argv that boots a VM and runs the inner command.

        ``sandbox_name`` sets the deterministic name the VM will live under
        (``~/.microsandbox/sandboxes/<name>``). The harness ALWAYS passes one
        so the finally block can ``msb remove <name>`` to clean up the per-run
        snapshot copy. (Without a name, msb auto-mints ``msb-<hash>`` and the
        harness has nothing deterministic to remove — every run leaks the
        disk copy.)
        """
        cfg = self.config
        ctx = context or {}
        read_only = bool(ctx.get("read_only_workspace"))
        ws_mounts, guest = self._workspace_mounts(workdir_host, read_only=read_only)
        cmd: List[str] = [
            self._microsandbox_bin() or "msb",
            "run",
            "--no-tty",
            "--timeout",
            f"{cfg.microsandbox_timeout_secs}s",
        ]
        if sandbox_name:
            cmd += ["--name", sandbox_name]
        if cfg.microsandbox_snapshot:
            cmd += ["--snapshot", cfg.microsandbox_snapshot]
        else:
            # image goes at the end (positional); snapshot replaces it.
            pass
        cmd += ws_mounts + ["--workdir", guest]
        if cfg.microsandbox_mem_size_mib:
            cmd += ["--memory", f"{cfg.microsandbox_mem_size_mib}M"]
        if cfg.microsandbox_cpus:
            cmd += ["--cpus", str(cfg.microsandbox_cpus)]
        xdg_config_dir = ctx.get("_xdg_config_dir")
        xdg_data_dir = ctx.get("_xdg_data_dir")
        cmd += self._credential_mounts(xdg_config_dir, xdg_data_dir)
        cmd += list(extra_mounts or [])
        cmd += self._extra_env(ctx)
        cmd += self._net_flags(ctx)
        cmd += list(cfg.microsandbox_extra_args or [])
        if not cfg.microsandbox_snapshot:
            cmd += [cfg.microsandbox_image]
        # Run inside a shell with stdin closed IN-GUEST: msb does not propagate the
        # host process's DEVNULL stdin to the guest, and agent CLIs (opencode/codex)
        # block reading an open stdin. Redirecting `</dev/null` in-guest is what makes
        # the run terminate (proven live — without it the guest hangs to timeout).
        # agy authenticates via a Secret Service seeded in-guest — its command runs
        # under dbus-run-session (private session bus) with a keyring-bootstrap
        # prologue. Other agents run in a plain shell.
        is_agy = self.harness_name == "agy"
        bootstrap = _AGY_KEYRING_BOOTSTRAP if is_agy else ""
        exec_prefix = ["dbus-run-session", "--"] if is_agy else []
        if live_trace:
            # Stream the agent's stdout+stderr through tee into the bind-mounted
            # canonical trace file — live on the host, and it survives a VM kill.
            # bash (not dash) for pipefail so the agent's exit code is preserved.
            cmd += ["-v", f"{live_trace}:{_GUEST_TRACE_FILE}"]
            guest_cmd = (
                bootstrap
                + "set -o pipefail; "
                + shlex.join(inner_cmd)
                + f" </dev/null 2>&1 | tee {_GUEST_TRACE_FILE}"
            )
            cmd += ["--", *exec_prefix, "bash", "-lc", guest_cmd]
        else:
            guest_cmd = bootstrap + shlex.join(inner_cmd) + " </dev/null"
            cmd += ["--", *exec_prefix, "sh", "-lc", guest_cmd]
        return cmd

    def _execute_sync(self, prompt: str, context: dict) -> str:
        inner_cmd = self.inner.build_execute_command(prompt, context)
        if not inner_cmd:
            raise RuntimeError(
                f"{self.inner.name} produced no CLI command to run in microsandbox "
                "(API-only harnesses cannot be sandboxed this way)."
            )
        inner_cmd = self._inject_bypass_flags(inner_cmd)
        if context.get("trace_file"):
            inner_cmd = self._inject_live_log_flags(inner_cmd)
        workdir_host = context.get("working_dir") or os.getcwd()
        # Rewrite host worktree path -> guest mount so the command resolves in the VM.
        # (For linked worktrees the guest workdir IS the host path — rewrite no-ops.)
        _, guest = self._workspace_mounts(
            workdir_host, read_only=bool(context.get("read_only_workspace"))
        )
        resolved = str(Path(workdir_host).resolve())
        if guest != resolved:
            inner_cmd = [p.replace(resolved, guest) for p in inner_cmd]

        # One deterministic name per run — the single handle the finally block
        # uses to remove the per-run sandbox. Generated before msb is even
        # invoked, so a crash between this line and `subprocess.run(...)` still
        # leaves a name we can target from `sweep_startup_orphans` later.
        sandbox_name = self._ephemeral_sandbox_name(context)

        tmpdir = tempfile.mkdtemp(prefix="odin-msb-")
        try:
            inner_cmd, mcp_mounts = self._stage_mcp_config(context, inner_cmd, tmpdir)
            trace_file = context.get("trace_file")
            output_file = context.get("output_file")
            live_trace: Optional[Path] = None
            if trace_file:
                # Absolute is load-bearing: msb parses a relative -v source as a
                # NAMED VOLUME and refuses to boot ("invalid config: volume
                # name…"). Reflection passes relative paths (F37).
                live_trace = Path(trace_file).resolve()
                live_trace.parent.mkdir(parents=True, exist_ok=True)
                live_trace.write_text("")  # bind mount needs an existing file
            xdg_mounts: List[str] = []
            if self.harness_name in _OPENCODE_FAMILY:
                host_xdg_config = Path(tmpdir) / "xdg-config"
                host_xdg_data = Path(tmpdir) / "xdg-data"
                host_xdg_config.mkdir(exist_ok=True)
                host_xdg_data.mkdir(exist_ok=True)
                xdg_mounts = [
                    "-v", f"{host_xdg_config}:{_GUEST_XDG_CONFIG}:rw",
                    "-v", f"{host_xdg_data}:{_GUEST_XDG_DATA}:rw",
                ]
                context["_xdg_config_dir"] = _GUEST_XDG_CONFIG
                context["_xdg_data_dir"] = _GUEST_XDG_DATA
            run_cmd = self._build_msb_command(
                inner_cmd,
                workdir_host,
                context=context,
                extra_mounts=mcp_mounts + xdg_mounts,
                live_trace=str(live_trace) if live_trace else None,
                sandbox_name=sandbox_name,
            )
            host_timeout = self.config.microsandbox_timeout_secs + 120
            stop = threading.Event()
            mirror: Optional[threading.Thread] = None
            if live_trace and output_file:
                mirror = threading.Thread(
                    target=_mirror_extracted_to_out,
                    args=(str(live_trace), str(output_file), stop),
                    daemon=True,
                )
                mirror.start()
            timeout_note = ""
            try:
                proc = subprocess.run(
                    run_cmd,
                    capture_output=True,
                    text=True,
                    timeout=host_timeout,
                    stdin=subprocess.DEVNULL,  # agent CLIs can block on an open stdin pipe
                )
                rc, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
            except subprocess.TimeoutExpired as exc:
                # The msb --timeout should fire first; this is the host backstop.
                # Whatever the guest tee'd into the bind mount is already on disk.
                rc = -1
                stdout = _to_text(exc.stdout)
                stderr = _to_text(exc.stderr)
                timeout_note = (
                    f"\n[odin] host timeout after {host_timeout}s — VM killed; "
                    "partial trace preserved"
                )
            finally:
                # Stop the live-trace mirror BEFORE removing the per-run sandbox,
                # so any in-flight writes to the bind mount are flushed first.
                # Then explicitly remove the ephemeral sandbox — the rm is
                # best-effort and best-effort must run whether the run succeeded,
                # failed, or timed out; otherwise every run leaks the per-VM
                # snapshot copy. (See ``_remove_sandbox`` — it never touches
                # anything not matching ``odin-msb-*``.)
                stop.set()
                if mirror:
                    mirror.join(timeout=5)
                # Order: msb sandbox remove first (stops the VM, releases
                # bind-mounted file handles inside the sandbox dir), then
                # _remove_sandbox_dir (frees the disk copy). The dir prune
                # is the structural fix for the 58 ~3 GB leaked dirs — msb
                # remove alone is exactly what left them behind.
                MicrosandboxHarness._remove_sandbox(sandbox_name)
                MicrosandboxHarness._remove_sandbox_dir(sandbox_name)
            if live_trace:
                try:
                    raw = live_trace.read_text()
                except OSError:
                    raw = ""
                if raw:
                    if rc != 0 and stderr:
                        raw = f"{raw}\n{stderr}"
                    return raw + timeout_note
            output = stdout
            if rc != 0 and stderr:
                output = f"{output}\n{stderr}" if output else stderr
            return output + timeout_note
        finally:
            # Run-end hygiene: every run creates an ``odin-msb-XXXXX`` temp dir
            # for MCP staging. Always remove it — otherwise a tightly recurring
            # exec_task poll leaks thousands of staging dirs. Guarded against
            # double-remove (the cleanup just above is in a separate finally
            # block, so the dir is independent).
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _write_trace_files(
        self, context: dict, *, raw_output: str, extracted_output: str
    ) -> None:
        output_file = context.get("output_file")
        trace_file = context.get("trace_file")
        if trace_file:
            path = Path(trace_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw_output)
        if output_file:
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(extracted_output)

    # --- ephemeral-sandbox lifecycle -----------------------------------------
    # The harness creates one ``msb-<hash>`` VM per run whose disk is a full
    # copy of the ``odin-agents`` snapshot (~3 GB). Until this section existed
    # that copy lived forever after the run — every confined task leaked ~3 GB.
    # The fix is structural: lifecycle is owned by the run boundary, not by an
    # out-of-band cron. (Per-run finally removes the per-run sandbox; startup
    # sweep handles the crashes whose finally never ran.)

    def _ephemeral_sandbox_name(self, context: Optional[dict] = None) -> str:
        """Deterministic per-run sandbox name. The first 8 hex chars of a uuid4
        give the run a stable handle for cleanup without leaking run IDs (the
        full task ID is plumbed only when the orchestrator passes one).
        """
        ctx = context or {}
        # Honor a caller-supplied task_id when available (lets a debugging
        # operator match a leaked sandbox to a task by name) without making it
        # mandatory: reflection + planning paths call without one.
        task_id = ctx.get("task_id") or ctx.get("trace_file") or ""
        suffix = uuid.uuid4().hex[:8]
        if task_id:
            # Use the first 8 hex chars of the task id when present
            base = "".join(c for c in str(task_id) if c.isalnum())[:8].lower() or suffix
            return f"{_EPHEMERAL_SANDBOX_PREFIX}{base}-{suffix}"
        return f"{_EPHEMERAL_SANDBOX_PREFIX}{suffix}"

    @staticmethod
    def _is_ephemeral_msb_sandbox(name: str) -> bool:
        """True only for names the harness itself minted.

        Used by the orphan filter to keep ``odinbuild``, snapshots, and any
        future operator-managed sandbox untouched. The prefix check alone is
        enough; the protected-name override is defensive belt-and-braces.
        """
        if not name:
            return False
        if not name.startswith(_EPHEMERAL_SANDBOX_PREFIX):
            return False
        if name in _PROTECTED_SANDBOX_NAMES:
            return False
        return True

    @staticmethod
    def _partition_orphans(names: Iterable[str]) -> Tuple[List[str], List[str]]:
        """Return (ephemeral, refused). Refused = everything we will NOT remove.

        A single safety net used by ``sweep_startup_orphans`` and the ``gc``
        subcommand. Refusal is logged (caller's job) so an operator can spot
        unexpected names that found their way into the orphan pool.
        """
        kept: List[str] = []
        refused: List[str] = []
        for n in names:
            if MicrosandboxHarness._is_ephemeral_msb_sandbox(n):
                kept.append(n)
            else:
                refused.append(n)
        return kept, refused

    @staticmethod
    def _parse_msb_orphans(stdout: str, fmt: str) -> List[str]:
        """Extract ephemeral sandbox names from the output of ``msb list``.

        ``fmt`` is ``"json"`` (prefer) or ``"plain"`` (newline-separated names).
        Anything not matching the ephemeral prefix is dropped — same safety net
        as above. Empty output → empty list (never an error).
        """
        if not stdout:
            return []
        names: List[str] = []
        if fmt == "json":
            try:
                data = json.loads(stdout)
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict):
                            n = entry.get("name") or entry.get("Name") or ""
                        else:
                            n = str(entry)
                        if n:
                            names.append(n)
                elif isinstance(data, dict):
                    # some msb versions wrap the list under a key like
                    # ``{"sandboxes": [...]}``; accept either
                    for key in ("sandboxes", "items", "data", "Sandboxes"):
                        if key in data and isinstance(data[key], list):
                            for entry in data[key]:
                                if isinstance(entry, dict):
                                    n = entry.get("name") or entry.get("Name") or ""
                                    if n:
                                        names.append(n)
            except (ValueError, TypeError):
                return []
        else:
            for line in stdout.splitlines():
                n = line.strip()
                if n:
                    names.append(n)
        kept, _dropped = MicrosandboxHarness._partition_orphans(names)
        return kept

    @staticmethod
    def _msb_argv() -> List[str]:
        """Resolve the ``msb`` argv prefix (path + any required env).

        Returns just the path (``["/.../msb"]``); env vars are handled by the
        caller's ``subprocess.run(env=...)``. Falls back to ``["msb"]`` if not
        installed — the cleanup calls are best-effort and silently succeed when
        msb is absent.
        """
        for cand in (
            Path("~/.local/bin/msb").expanduser(),
            Path("~/.microsandbox/bin/msb").expanduser(),
            Path("/usr/local/bin/msb"),
        ):
            if cand.exists():
                return [str(cand)]
        # PATH discovery last — the path-less form lets subprocess's PATH
        # resolution do its thing.
        return ["msb"]

    @staticmethod
    def _run_msb(args: List[str], *, timeout: int = 30) -> "subprocess.CompletedProcess[bytes]":  # type: ignore[name-defined]
        """Thin wrapper around ``msb <args>`` for cleanup/list operations.

        Captures stdout/stderr, defaults to a short timeout (these calls are
        housekeeping — a hung msb would block the harness), and returns the
        completed process. Tests patch this method.
        """
        try:
            return subprocess.run(
                MicrosandboxHarness._msb_argv() + list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError):
            # Defensive: cleanup must never raise. A failure here means the
            # sandbox is leaked; ``sweep_startup_orphans`` will retry on the
            # next executor start.
            return subprocess.CompletedProcess(args, returncode=-1, stdout="", stderr="")

    @staticmethod
    def _list_disk_ephemeral_sandboxes() -> List[str]:
        """Source-of-truth list of ephemeral sandbox directories: read the
        sandbox home directly off disk, applying the ``odin-msb-*`` prefix
        filter.

        The disk IS the canonical record of what exists. ``msb sandbox list``
        can drop entries — msb tracks its own metadata independent of the
        per-VM disk copy, and it has been observed to return empty even
        when multi-GB dirs remain on disk (exactly the failure mode that
        left 58 orphan directories on the operator's host, ~183 GB /
        85% disk). The orphan sweep therefore MUST not trust ``msb list``
        as its only view of "what's there" — disk reading is the only
        listing it uses.

        The result is filtered through :meth:`_partition_orphans` so
        non-ephemeral names (``odinbuild``, anything not matching the
        prefix) NEVER appear — same safety net as the parser-based path.
        Empty list when the sandbox home itself doesn't exist yet (fresh
        host, no runs yet). Never raises — best-effort.
        """
        try:
            home = _DEFAULT_SANDBOX_HOME.expanduser()
            if not home.is_dir():
                return []
            kept, _dropped = MicrosandboxHarness._partition_orphans(
                p.name for p in home.iterdir() if p.is_dir()
            )
            return kept
        except OSError:
            return []

    @staticmethod
    def _list_msb_known_sandboxes() -> List[str]:
        """Diagnostic cross-check: ephemeral sandbox names that ``msb``
        itself reports. NEVER used as the sweep's source of truth — disk
        is canonical (see :meth:`_list_disk_ephemeral_sandboxes`).

        Tries ``msb sandbox list --json`` first (preferred), falls back
        to ``msb sandbox list`` (plain text). Returns an empty list when
        msb is absent, errors, or returns no parseable output. Comparing
        this list against the disk-truth list surfaces the "msb has
        dropped its record but the disk dir remains" failure mode to the
        operator log.
        """
        proc = MicrosandboxHarness._run_msb(["sandbox", "list", "--json"])
        if proc.returncode == 0 and proc.stdout:
            kept = MicrosandboxHarness._parse_msb_orphans(proc.stdout, "json")
            if kept:
                return kept
            # msb responded with a parseable but empty list — that's a
            # valid signal (nothing msb knows about), don't fall through.
            stripped = proc.stdout.strip()
            if stripped.startswith("[") or stripped.startswith("{"):
                return []
        proc = MicrosandboxHarness._run_msb(["sandbox", "list"])
        if proc.returncode == 0 and proc.stdout:
            return MicrosandboxHarness._parse_msb_orphans(proc.stdout, "plain")
        return []

    @staticmethod
    def _list_managed_sandboxes() -> List[str]:
        """Backward-compatible alias for :meth:`_list_disk_ephemeral_sandboxes`.

        The naming survived an internal rename — historically the body was
        ``msb sandbox list`` with a disk fallback, but the disk fallback
        was the correct part and the ``msb`` path was the bug (it could
        return an empty list and still look successful, masking the very
        leak the sweep is supposed to fix). All call sites that need to
        enumerate orphans — the startup sweep, the reconciler sweep,
        ``odin gc``'s report — now read disk directly via this alias.
        """
        return MicrosandboxHarness._list_disk_ephemeral_sandboxes()

    @staticmethod
    def _remove_sandbox(name: str, *, timeout: int = 30) -> bool:
        """Best-effort ``msb sandbox remove <name>``. Returns True on success.

        Hardened against double-remove (rc=1 from "already removed" treated as
        success) and against msb itself being absent — the harness leaks the
        sandbox ONLY in the latter case, and the startup sweep picks it up on
        the next executor start.
        """
        if not name:
            return False
        if not MicrosandboxHarness._is_ephemeral_msb_sandbox(name):
            # Refusal: the only safety net for "do not remove persistent
            # sandboxes". Should never trip from inside the harness (the
            # harness only mints ``odin-msb-*`` names), but if a future caller
            # mistakes the API for a generic remove-helper we fail closed.
            return False
        proc = MicrosandboxHarness._run_msb(
            ["sandbox", "remove", "--name", name], timeout=timeout,
        )
        if proc.returncode == 0:
            return True
        # Treat "already gone" as success (idempotent). msb prints something
        # like ``Error: sandbox not found`` to stderr in that case.
        stderr = (proc.stderr or "").lower()
        if "not found" in stderr or "does not exist" in stderr:
            return True
        return False

    @staticmethod
    def _remove_sandbox_dir(name: str) -> bool:
        """Direct removal of the per-run sandbox directory at
        ``~/.microsandbox/sandboxes/<name>``. Belt-and-braces alongside
        :meth:`_remove_sandbox`: if msb is missing or fails to free the disk,
        the dir still gets cleaned up. This is the structural fix for the
        ``msb sandbox remove`` silent-leak that left 58 ~3 GB dirs on the
        operator's host (~183 GB / 85% disk).

        Refuses anything not matching the ephemeral prefix (so
        ``odinbuild`` and snapshot images are safe) and is idempotent
        (already-gone returns True). Never touches ``~/.microsandbox/snapshots/``
        — the sandbox and snapshot homes are separate directories, but the
        ``_is_ephemeral_msb_sandbox`` guard makes the refusal structural.
        """
        if not name or not MicrosandboxHarness._is_ephemeral_msb_sandbox(name):
            return False
        home = _DEFAULT_SANDBOX_HOME.expanduser()
        target = home / name
        # Belt-and-braces path safety: never operate on a path under the
        # snapshots dir even if a future caller misroutes us.
        if "snapshots" in target.parts:
            return False
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            return True
        except OSError:
            return False

    @staticmethod
    def _sandbox_dir_size(name: str) -> int:
        """Bytes used by ``~/.microsandbox/sandboxes/<name>``, 0 if absent
        or refused (non-ephemeral names return 0 so a future caller can't
        accidentally walk into a multi-GB persistent sandbox like
        ``odinbuild``). Used by the sweep to log bytes freed."""
        if not name or not MicrosandboxHarness._is_ephemeral_msb_sandbox(name):
            return 0
        home = _DEFAULT_SANDBOX_HOME.expanduser()
        target = home / name
        if "snapshots" in target.parts or not target.is_dir():
            return 0
        return MicrosandboxHarness._dir_size(target)

    @staticmethod
    def _dir_size(path: Path) -> int:
        """Walk the tree summing file sizes (symlinks = 0). Module-level
        helper kept local so :meth:`_sandbox_dir_size` and any future caller
        can reuse it without importing from ``odin.gc`` (which would
        circular-import)."""
        total = 0
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        try:
            for dirpath, _dirs, filenames in os.walk(str(resolved)):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        size = 0 if os.path.islink(fp) else os.lstat(fp).st_size
                    except OSError:
                        continue
                    total += size
        except OSError:
            return 0
        return total

    @staticmethod
    def _live_msb_process_exists(name: str) -> bool:
        """True if a live msb process is currently running the sandbox
        ``name`` — i.e. ``pgrep`` finds a cmdline matching
        ``msb run ... --name <name>``. Used by the sweep to skip sandboxes
        that are real VMs, not orphans. Returns True (assume live) on any
        error so a broken ``pgrep`` call can never widen the sweep into
        killing live VMs.
        """
        if not name:
            return False
        try:
            proc = subprocess.run(
                ["pgrep", "-f", f"msb run.*--name {name}"],
                capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return True

    @classmethod
    def sweep_startup_orphans(cls, *, dry_run: bool = False) -> List[str]:
        """Backstop for crashed runs: remove any ``odin-msb-*`` sandboxes left
        behind by a previous process that died before its finally block ran.

        Called by the orchestrator on initialization. Best-effort and idempotent.
        Refuses to touch any sandbox whose name doesn't match the ephemeral
        prefix — the rename-prefix filter is the single safety guarantee.

        Each orphan that is removed ALSO gets its on-disk directory deleted
        (:meth:`_remove_sandbox_dir`) — ``msb sandbox remove`` alone is what
        left 58 ~3 GB dirs on disk. Bytes freed are logged once at the end
        via :meth:`sweep_startup_orphans_report`. Sandboxes with a live msb
        process owning them are skipped — they are real VMs, not orphans.
        """
        try:
            names = cls._list_managed_sandboxes()
        except Exception:
            return []
        if dry_run:
            return list(names)
        report = cls._sweep_orphans_internal(names, dry_run=False)
        return report["names_removed"]

    @classmethod
    def sweep_startup_orphans_report(cls, *, dry_run: bool = False) -> dict:
        """The trust-friendly version of :meth:`sweep_startup_orphans`.

        Returns ``{"names_removed": [...], "bytes_freed": int, "message": str}``
        so the reconciler / ``odin gc`` can log a one-line "freed X GiB across
        N orphans" entry without re-walking the dir tree.
        """
        try:
            names = cls._list_managed_sandboxes()
        except Exception:
            return {"names_removed": [], "bytes_freed": 0, "message": ""}
        return cls._sweep_orphans_internal(names, dry_run=dry_run)

    @classmethod
    def _sweep_orphans_internal(cls, names: List[str], *, dry_run: bool) -> dict:
        """Shared body for the two public sweep entry points.

        Reads ``msb sandbox list`` ONLY as a diagnostic cross-check, and
        logs a warning when disk-truth lists entries that ``msb`` no
        longer reports — the exact signature of the 58-dir leak this
        sweeper was added to fix. The removal decisions themselves are
        driven exclusively by the disk-truth ``names`` argument (which
        the public entry points populate via
        :meth:`_list_disk_ephemeral_sandboxes`), so a swept orphan whose
        msb record has already been dropped is still removed.
        """
        if dry_run:
            return {
                "names_removed": list(names),
                "bytes_freed": 0,
                "message": "",
            }
        # Cross-check against msb's view (diagnostic only — NEVER gates
        # removal). When msb's view is a strict subset of the disk-truth
        # names, those are exactly the kind of orphans the harness
        # leaked once and could leak again, so the operator sees a single
        # log line that names them.
        try:
            msb_known = set(cls._list_msb_known_sandboxes())
        except Exception:
            msb_known = set()
        disk_set = set(names)
        leaked_from_msb = sorted(disk_set - msb_known)
        if leaked_from_msb:
            import logging
            logging.getLogger("odin.microsandbox").warning(
                "sweep found %d sandbox dir(s) on disk that msb sandbox "
                "list no longer reports (msb metadata dropped, disk "
                "remained \u2014 the original 58-dir leak pattern): %s",
                len(leaked_from_msb), ", ".join(leaked_from_msb),
            )
        removed: List[str] = []
        bytes_freed = 0
        for name in names:
            # Skip sandboxes with a live msb process owning them — those are
            # real VMs, not orphans. Live check uses pgrep; if pgrep errors,
            # _live_msb_process_exists defaults to True (don't kill).
            if cls._live_msb_process_exists(name):
                continue
            size_before = cls._sandbox_dir_size(name)
            if not cls._remove_sandbox(name):
                continue
            # Belt-and-braces: nuke the on-disk dir even if msb remove
            # returned success — this is exactly how 58 dirs leaked.
            if cls._remove_sandbox_dir(name):
                removed.append(name)
                bytes_freed += size_before
        if bytes_freed > 0:
            message = (
                f"microsandbox sweep freed {_fmt_freed_bytes(bytes_freed)} "
                f"across {len(removed)} orphan(s)"
            )
            import logging
            logging.getLogger("odin.microsandbox").info(message)
            return {"names_removed": removed, "bytes_freed": bytes_freed, "message": message}
        return {"names_removed": removed, "bytes_freed": 0, "message": ""}

    # --- size breakdown (used by `odin gc`) -----------------------------------

    @staticmethod
    def _size_breakdown(path: Path) -> dict:
        """Compute the disk footprint of ``path`` split by ``node_modules``.

        Used by ``odin gc`` to honestly report what an orphan worktree costs —
        ``node_modules`` typically holds ~1 GB of npm-install output that is
        never reused across worktrees (the simplest fix is a shared npm cache
        — BACKLOG item). The breakdown keeps the two costs separable so the
        operator can see the per-run ``npm install`` tax.
        """
        path = Path(path)
        info = {"size_bytes": 0, "node_modules_bytes": 0, "rest_bytes": 0}
        if not path.is_dir():
            return info
        nm_bytes = 0
        rest_bytes = 0
        # Resolve symlinks-to-dirs before walking so we don't loop on cycles
        # via the host's `.git -> ../../.git/worktrees/...` pointer; the
        # /__pycache__/ + node_modules recursion problem is real on dev trees.
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if not resolved.is_dir():
            return info
        for dirpath, dirnames, filenames in os.walk(str(resolved)):
            # Don't descend into git internals — they're shared with the main
            # repo and double-counting them misreports the worktree's true
            # marginal cost.
            norm_dirpath = dirpath.replace("\\", "/")
            if "/.git/" in norm_dirpath:
                dirnames[:] = []
                continue
            # Don't follow the worktree's "node_modules" into a second level
            # beyond the top one (the heuristic; real fix is a shared cache).
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.islink(fp):
                        size = 0  # symlinks are zero marginal cost
                    else:
                        size = os.lstat(fp).st_size
                except OSError:
                    continue
                norm_fp = fp.replace("\\", "/")
                if "/node_modules/" in norm_fp:
                    nm_bytes += size
                else:
                    rest_bytes += size
        info["node_modules_bytes"] = nm_bytes
        info["rest_bytes"] = rest_bytes
        info["size_bytes"] = nm_bytes + rest_bytes
        return info


def _fmt_freed_bytes(n: int) -> str:
    """Format a byte count for the sweep log line (KiB-style rounding)."""
    if n <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}"
        f /= 1024
    return f"{n} B"


def claude_token_candidate_paths(
    working_dir: Optional[str] = None,
    explicit_file: Optional[str] = None,
) -> List[Path]:
    """Public list of ``.claude-token`` candidate paths in precedence order.

    Mirrors :meth:`MicrosandboxHarness._claude_token_files` but takes its
    inputs as plain arguments so ``odin doctor`` can probe every path
    without constructing a harness instance.

    Order (highest precedence first — the harness resolves in this order
    after the macOS Keychain live source):

    1. ``explicit_file`` (configured ``microsandbox_claude_token_file``)
    2. ``working_dir`` and its ancestors up to the git root — where the
       TaskIt settings UI writes the token for a board (``board.working_dir``),
       and which also covers linked worktrees.
    3. ``Path.cwd() / ".claude-token"`` (fallback for ad-hoc repos)
    4. ``~/.odin/.claude-token`` and ``~/.claude-token`` (user-global)

    Returned paths are not existence-checked — callers (the harness and
    ``odin doctor``) decide what to do with a missing file.
    """
    paths: List[Path] = []
    if explicit_file:
        paths.append(Path(explicit_file).expanduser())
    if working_dir:
        try:
            here = Path(working_dir).resolve()
        except OSError:
            here = Path(working_dir)
        for anc in [here, *here.parents]:
            paths.append(anc / ".claude-token")
            if (anc / ".git").exists():
                break
    paths += [
        Path.cwd() / ".claude-token",
        Path.home() / ".odin" / ".claude-token",
        Path.home() / ".claude-token",
    ]
    return paths
