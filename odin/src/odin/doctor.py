"""`odin doctor` — environment sanity checks for first-timers and operators.

A getting-started / triage tool. Each check is a named line with PASS/WARN/FAIL
and the exact fix command when red. The partial-provider honesty block says
which kit features work with the CLIs actually found (derived from where each
agent is used in code, not hand-waved).

Design:
- All I/O probes are module-level functions so tests patch them; the check
  builders and matrix derivation are pure.
- A check FAILs (exit 1) only when it blocks a basic run. The irreducible
  requirement of ``odin run`` is at least one agent CLI installed *and*
  authenticated; everything else degrades to a WARN so the tool stays useful.
- Auth probes reuse the credential resolution documented in
  ``harnesses/microsandbox.py`` (env → keychain → file), are cheap (no network,
  no CLI invocation that could prompt), and never raise — a missing credential
  is a WARN, not a crash.
"""

from __future__ import annotations

import json as _json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from odin.models import OdinConfig

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

# The five fable providers. ``cli`` is the default binary; config may override
# via ``agents[name].cli_command``. ``auth`` is the cheap credential probe.
_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("claude", "claude"),
    ("codex", "codex"),
    ("glm", "opencode"),
    ("minimax", "opencode"),
    ("agy", "agy"),
)


# ── data structures ───────────────────────────────────────────────────────


@dataclass
class Check:
    """One named check line."""

    category: str
    name: str
    status: str
    detail: str
    fix: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"category": self.category, "name": self.name, "status": self.status, "detail": self.detail}
        if self.fix:
            d["fix"] = self.fix
        return d


@dataclass
class AgentProbe:
    """Per-provider availability probe."""

    name: str
    cli: str
    installed: bool
    authenticated: bool
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.installed and self.authenticated

    @property
    def status(self) -> str:
        if not self.installed or not self.authenticated:
            return WARN
        return PASS

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "cli": self.cli,
            "installed": self.installed,
            "authenticated": self.authenticated,
            "available": self.available,
            "detail": self.detail,
        }


@dataclass
class FeatureRow:
    """One kit capability and whether a serving agent is available."""

    feature: str
    default_agent: str
    serving_agent: Optional[str]
    status: str
    note: str

    def to_dict(self) -> dict:
        return {
            "feature": self.feature,
            "default_agent": self.default_agent,
            "serving_agent": self.serving_agent,
            "status": self.status,
            "note": self.note,
        }


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)
    agents: list[AgentProbe] = field(default_factory=list)
    features: list[FeatureRow] = field(default_factory=list)

    @property
    def any_agent_available(self) -> bool:
        return any(a.available for a in self.agents)

    @property
    def blocks_basic_run(self) -> bool:
        """True iff ``odin run`` cannot do anything useful.

        The one hard blocker is zero agents installed+auth'd — without an agent
        you can neither plan nor execute. Missing services/sandbox are WARNs:
        they degrade specific paths but the user can still get value.
        """
        return not self.any_agent_available

    @property
    def exit_code(self) -> int:
        return 1 if self.blocks_basic_run else 0

    def to_dict(self) -> dict:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "agents": [a.to_dict() for a in self.agents],
            "features": [f.to_dict() for f in self.features],
            "exit_code": self.exit_code,
        }


# ── I/O probes (module-level → patchable) ─────────────────────────────────


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """True if a TCP connection to host:port succeeds (IPv4 then IPv6)."""
    import socket

    for fam, addr in (
        (socket.AF_INET, (host, port)),
        (socket.AF_INET6, ("::1" if host in ("127.0.0.1", "localhost") else host, port)),
    ):
        s = socket.socket(fam)
        s.settimeout(0.5)
        try:
            s.connect(addr)
            return True
        except OSError:
            pass
        finally:
            s.close()
    return False


def _celery_running(pattern: str) -> bool:
    """True if a process matching the pgrep pattern is running."""
    if not shutil.which("pgrep"):
        return False
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _cli_on_path(cli: str) -> Optional[str]:
    """Resolve a CLI binary on PATH, or None."""
    if not cli:
        return None
    return shutil.which(cli)


def _read_token_file(path: Path) -> Optional[str]:
    try:
        if path.is_file():
            tok = path.read_text().strip()
            if tok:
                return tok
    except OSError:
        pass
    return None


def _keychain_password_macos(service: str) -> Optional[str]:
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _secret_service_lookup(service: str, username: str) -> Optional[str]:
    """Linux Secret Service (freedesktop) lookup via secret-tool."""
    if not shutil.which("secret-tool"):
        return None
    try:
        proc = subprocess.run(
            ["secret-tool", "lookup", "service", service, "username", username],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _claude_authenticated() -> tuple[bool, str]:
    home = Path.home()
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True, "CLAUDE_CODE_OAUTH_TOKEN set"
    tok = _keychain_password_macos("Claude Code-credentials")
    if tok:
        return True, "macOS Keychain (Claude Code-credentials)"
    for cand in (
        home / ".claude-token",
        home / ".odin" / ".claude-token",
        Path.cwd() / ".claude-token",
    ):
        if _read_token_file(cand):
            return True, f"token file {cand}"
    cred = home / ".claude" / ".credentials.json"
    if _read_token_file(cred):
        return True, "~/.claude/.credentials.json"
    return False, "no OAuth token (run `claude setup-token` or set CLAUDE_CODE_OAUTH_TOKEN)"


def _codex_authenticated() -> tuple[bool, str]:
    auth = Path.home() / ".codex" / "auth.json"
    if auth.is_file():
        try:
            if auth.read_text().strip():
                return True, "~/.codex/auth.json present"
        except OSError:
            pass
    return False, "no ~/.codex/auth.json (run `codex login`)"


def _glm_authenticated() -> tuple[bool, str]:
    if os.environ.get("ZAI_API_KEY"):
        return True, "ZAI_API_KEY set"
    auth = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    if auth.is_file():
        return True, "opencode auth.json present"
    return False, "no ZAI_API_KEY and no opencode auth (run `opencode login`)"


def _minimax_authenticated() -> tuple[bool, str]:
    if os.environ.get("MINIMAX_API_KEY"):
        return True, "MINIMAX_API_KEY set"
    for cand in (
        Path.home() / ".local" / "share" / "kilo" / "auth.json",
        Path.home() / ".local" / "share" / "opencode" / "auth.json",
    ):
        if cand.is_file():
            return True, f"{cand.name} present"
    return False, "no MINIMAX_API_KEY and no kilo/opencode auth"


def _agy_authenticated() -> tuple[bool, str]:
    if os.environ.get("AGY_SECRET"):
        return True, "AGY_SECRET set"
    if sys.platform == "darwin":
        if _keychain_password_macos("gemini"):
            return True, "macOS Keychain (gemini service)"
    else:
        if _secret_service_lookup("gemini", "antigravity"):
            return True, "Secret Service (gemini/antigravity)"
    return False, "no agy credential (run `agy login` or `agy models` to refresh)"


_AUTH_PROBES: dict[str, Callable[[], tuple[bool, str]]] = {
    "claude": _claude_authenticated,
    "codex": _codex_authenticated,
    "glm": _glm_authenticated,
    "minimax": _minimax_authenticated,
    "agy": _agy_authenticated,
}


def _agent_authenticated(name: str) -> tuple[bool, str]:
    """Cheap credential probe for one provider. Never raises."""
    probe = _AUTH_PROBES.get(name)
    if probe is None:
        return False, f"unknown provider {name!r}"
    try:
        return probe()
    except Exception as exc:  # defensive: a probe must never crash doctor
        return False, f"auth probe error: {exc}"


def _claude_keychain_access_token() -> Optional[str]:
    """Live OAuth access token of the host's logged-in ``claude`` CLI, parsed
    out of the macOS Keychain ``Claude Code-credentials`` JSON blob.

    Returns the ``claudeAiOauth.accessToken`` value (the same string ``claude
    setup-token`` writes to ``.claude-token``) so doctor can compare the two.
    Returns None off-macOS, when the keychain is unreadable, or when the
    stored payload is missing the access token — never raises.
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
        creds = _json.loads(proc.stdout.strip()).get("claudeAiOauth") or {}
        tok = creds.get("accessToken")
        return tok if isinstance(tok, str) and tok else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _claude_live_token() -> tuple:
    """Return ``(token, source_label)`` for the live claude credential.

    Live source priority mirrors the harness: env var first
    (``CLAUDE_CODE_OAUTH_TOKEN`` is the per-process override), then the
    macOS Keychain parsed access token. Returns ``(None, None)`` when
    neither is available — used by the staleness check to avoid false
    positives when there's nothing to compare against.
    """
    env_tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip() or None
    if env_tok:
        return env_tok, "CLAUDE_CODE_OAUTH_TOKEN env var"
    if sys.platform == "darwin":
        kt = _claude_keychain_access_token()
        if kt:
            return kt, "macOS Keychain (Claude Code-credentials)"
    return None, None


def _check_claude_token_staleness(
    cfg: OdinConfig, *, working_dir: Optional[str] = None,
) -> list:
    """Probe every ``.claude-token`` candidate for staleness.

    The harness prefers the macOS Keychain live token but still falls
    through to ``.claude-token`` files (see
    ``harnesses/microsandbox.py::_claude_token``). When a stale file
    exists, the harness can silently pick it if the live source becomes
    momentarily unreadable — the trap that confuses doctor diagnoses
    during token incidents. For each existing file, compare against the
    live source; on mismatch emit a WARN naming the file and the fix.

    File existence is the trigger; a missing or empty file is not a
    candidate. The live source is the env var first, then the macOS
    Keychain — without at least one of those we have nothing to compare
    against, so the function returns no checks (no false positives on
    boxes without claude auth).
    """
    try:
        from odin.harnesses.microsandbox import claude_token_candidate_paths
    except ImportError:
        return []

    explicit_file: Optional[str] = None
    for agent_cfg in cfg.agents.values():
        if agent_cfg and getattr(agent_cfg, "microsandbox_claude_token_file", None):
            explicit_file = agent_cfg.microsandbox_claude_token_file
            break

    candidates = claude_token_candidate_paths(
        working_dir=working_dir or str(Path.cwd()),
        explicit_file=explicit_file,
    )

    # De-dupe preserving order — the path list can include duplicates
    # (cwd appears both as the always-added fallback and the walking
    # root), and even if it didn't, the same canonical path surfaced via
    # two different routes should yield exactly one WARN.
    seen: set = set()
    unique: List[Path] = []
    for p in candidates:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    live_token, live_source = _claude_live_token()
    if not live_token:
        return []  # no live source → cannot verify → no false positives

    checks: List[Check] = []
    for p in unique:
        try:
            if not p.is_file():
                continue
            file_token = p.read_text().strip()
        except OSError:
            continue
        if not file_token or file_token == live_token:
            continue
        checks.append(Check(
            "agents",
            f"stale .claude-token: {p}",
            WARN,
            (
                f"file token differs from live {live_source} — harness "
                f"may pick the stale value if the live source becomes "
                f"unreadable"
            ),
            fix=(
                f"`claude setup-token > {p}` to refresh in place, or "
                f"`rm {p}` to drop the stale fallback"
            ),
        ))
    return checks


def _msb_bin() -> Optional[str]:
    """Resolve the msb binary, or None."""
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


def _msb_image_exists(bin_path: str, image: str) -> bool:
    """True if the named sandbox/snapshot image is present."""
    if not bin_path:
        return False
    try:
        proc = subprocess.run(
            [bin_path, "sandbox", "list"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    return image in (proc.stdout or "")


def _msb_boot_test(bin_path: str, image: str) -> tuple[bool, str]:
    """Boot a throwaway VM and run `echo OK`. Returns (ok, detail)."""
    if not bin_path:
        return False, "msb binary not found"
    import time

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                bin_path, "run", "--no-tty", "--timeout", "30s",
                "--name", "odin-doctor-probe", "--", "sh", "-lc", "echo OK",
            ],
            capture_output=True, text=True, timeout=45,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, "VM boot timed out (>45s)"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"boot error: {exc}"
    finally:
        # Best-effort cleanup of the throwaway sandbox. Must never raise —
        # a cleanup failure would mask the real result above.
        if bin_path:
            try:
                subprocess.run(
                    [bin_path, "sandbox", "remove", "--name", "odin-doctor-probe"],
                    capture_output=True, timeout=15, stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                pass
    elapsed = time.monotonic() - start
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and "OK" in (proc.stdout or ""):
        return True, f"booted in {elapsed:.1f}s"
    snippet = (proc.stderr or proc.stdout or out).strip().splitlines()
    tail = snippet[-1] if snippet else f"rc={proc.returncode}"
    return False, f"boot failed: {tail[:120]}"


def _host_memory_mib() -> tuple[Optional[int], Optional[int]]:
    """Return (total, available) RAM in MiB, or (None, None) if unreadable."""
    try:
        import psutil
        return int(psutil.virtual_memory().total / (1024 * 1024)), int(psutil.virtual_memory().available / (1024 * 1024))
    except ImportError:
        pass
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
            # macOS vm_stat is page-based; fall through to /proc fallback below
            # is not available — return None rather than guess.
            if proc.returncode != 0:
                return None, None
        except (OSError, subprocess.SubprocessError):
            return None, None
        return None, None
    meminfo = Path("/proc/meminfo")
    if not meminfo.is_file():
        return None, None
    total = avail = None
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) // 1024
    except (OSError, ValueError):
        return None, None
    return total, avail


def _disk_free_mib(path: str = ".") -> Optional[int]:
    """Free disk space (MiB) at path, or None if unreadable."""
    try:
        usage = shutil.disk_usage(path)
        return int(usage.free / (1024 * 1024))
    except OSError:
        return None


# ── check builders ────────────────────────────────────────────────────────


def _check_service_port(category: str, label: str, port: int, start_hint: str) -> Check:
    name = f"{label} :{port}"
    if _port_open(port):
        return Check(category, name, PASS, f"port {port} accepting connections")
    return Check(
        category, name, WARN, f"port {port} not listening",
        fix=start_hint,
    )


def _check_celery_default() -> Check:
    name = "celery worker (default)"
    if _celery_running(r"celery.*-A config worker"):
        return Check("services", name, PASS, "default celery worker process running")
    return Check(
        "services", name, WARN, "no 'celery -A config worker' process",
        fix="cd taskit/taskit-backend && celery -A config worker --beat --loglevel=info --concurrency=3",
    )


def _check_celery_merges(queue: str) -> Check:
    name = f"celery worker (queue={queue})"
    if _celery_running(rf"celery.*-Q {queue}\b"):
        return Check("services", name, PASS, f"merges-queue worker running (-Q {queue})")
    return Check(
        "services", name, WARN, f"no worker consuming -Q {queue}",
        fix=f"celery -A config worker -Q {queue} --pool=threads --concurrency=2",
    )


def _probe_agent(name: str, cli: str) -> AgentProbe:
    installed_path = _cli_on_path(cli)
    installed = installed_path is not None
    if not installed:
        return AgentProbe(name, cli, installed=False, authenticated=False,
                          detail=f"CLI '{cli}' not on PATH")
    authed, reason = _agent_authenticated(name)
    return AgentProbe(name, cli, installed=True, authenticated=authed, detail=reason)


def _check_msb_runtime(image: str) -> tuple[Check, Optional[str]]:
    """Returns (check, bin_path_or_None)."""
    bin_path = _msb_bin()
    if not bin_path:
        return Check(
            "sandbox", "microsandbox runtime", WARN,
            "'msb' binary not found on PATH",
            fix="curl -fsSL https://raw.githubusercontent.com/nicholasgasior/microsandbox/main/install.sh | bash",
        ), None
    return Check("sandbox", "microsandbox runtime", PASS, f"msb at {bin_path}"), bin_path


def _check_msb_image(bin_path: str, image: str) -> Check:
    if _msb_image_exists(bin_path, image):
        return Check("sandbox", "sandbox image", PASS, f"image '{image}' present")
    return Check(
        "sandbox", "sandbox image", WARN, f"image '{image}' not found",
        fix=f"msb build --name {image}  # then msb snapshot",
    )


def _check_vm_boot(bin_path: str, image: str) -> Check:
    ok, detail = _msb_boot_test(bin_path, image)
    status = PASS if ok else FAIL
    return Check("sandbox", "throwaway VM boot", status, detail)


def _check_memory(budget_mib: int) -> Check:
    total, avail = _host_memory_mib()
    if avail is None:
        return Check("host", "free memory", WARN, "could not read host memory")
    if avail >= budget_mib:
        return Check(
            "host", "free memory", PASS,
            f"{avail} MiB free (≥ {budget_mib} MiB VM budget)",
        )
    return Check(
        "host", "free memory", WARN,
        f"{avail} MiB free (< {budget_mib} MiB VM budget) — concurrent VMs may OOM",
        fix="free memory or lower SANDBOX_MEMORY_BUDGET_MIB / microsandbox_mem_size_mib",
    )


def _check_disk(threshold_mib: int = 5120) -> Check:
    free = _disk_free_mib(".")
    if free is None:
        return Check("host", "disk headroom", WARN, "could not read free disk")
    if free >= threshold_mib:
        return Check("host", "disk headroom", PASS, f"{free} MiB free on worktree volume")
    return Check(
        "host", "disk headroom", WARN,
        f"{free} MiB free (< {threshold_mib} MiB) — sandbox images need GBs",
        fix="free disk space (sandbox snapshots are ~3 GB each)",
    )


def _check_base_agent_available(
    cfg: OdinConfig, available: Iterable[str],
) -> Check:
    """Named check: is the configured ``base_agent`` CLI installed+auth'd?

    The single most common getting-started trap for a new user: the default
    ``base_agent="claude"`` points at a CLI they don't have.  Without this
    check the failure surfaces as a deep subprocess crash inside ``odin
    plan``; with it the user sees a plain WARN naming the fix before any
    run starts.
    """
    avail_set = set(available)
    base = cfg.base_agent or "claude"
    if base in avail_set:
        return Check(
            "agents", f"base_agent CLI ({base})", PASS,
            f"default planning/execution agent '{base}' is available",
        )
    # Build the fix: name an available alternative, or point to the config.
    alternatives = sorted(avail_set)
    if alternatives:
        alt = alternatives[0]
        return Check(
            "agents", f"base_agent CLI ({base})", WARN,
            f"base_agent '{base}' is not installed+authenticated — "
            f"`odin plan` will crash. Available: {', '.join(alternatives)}.",
            fix=(
                f"set base_agent={alt} in .odin/config.yaml "
                f"(or install+auth {base})"
            ),
        )
    return Check(
        "agents", f"base_agent CLI ({base})", WARN,
        f"base_agent '{base}' is not installed+authenticated and no "
        f"alternative agent is available either.",
        fix="install+auth at least one agent CLI (claude, codex, gemini, glm, minimax)",
    )


# ── feature matrix ────────────────────────────────────────────────────────


def derive_feature_matrix(cfg: OdinConfig, available: Iterable[str]) -> list[FeatureRow]:
    """Derive which kit features work given the available agent set.

    Each feature's default agent is read from the code's own defaults so the
    matrix reflects where each agent is actually used:
      - planning      → OdinConfig.base_agent        (default "claude")
      - execution     → any registered harness        (the task's assigned agent)
      - reflection    → reflect_task(agent=...)       (default "claude")
      - merge-resolve → MergeAgentConfig.agent        (default "claude")
      - summarize     → the task's assigned agent     (any agent)
    """
    avail = set(available)
    base = cfg.base_agent or "claude"
    merge_agent = "claude"
    if cfg.merge_agent and cfg.merge_agent.agent:
        merge_agent = cfg.merge_agent.agent
    reviewer = "claude"  # reflect_task() default; CLI --agent overrides per-run

    rows: list[FeatureRow] = []

    def _any_agent_note() -> str:
        if avail:
            return "served by: " + ", ".join(sorted(avail))
        return "no agent available"

    # planning: served by base_agent; fallback to any agent present
    if base in avail:
        rows.append(FeatureRow("planning", base, base, PASS, f"default base_agent {base} available"))
    elif avail:
        fb = sorted(avail)[0]
        rows.append(FeatureRow(
            "planning", base, fb, WARN,
            f"default {base} missing — set base_agent={fb} or install {base}",
        ))
    else:
        rows.append(FeatureRow("planning", base, None, FAIL, f"default {base} missing and no fallback"))

    # execution: any agent
    if avail:
        rows.append(FeatureRow("execution", "any", sorted(avail)[0], PASS, _any_agent_note()))
    else:
        rows.append(FeatureRow("execution", "any", None, FAIL, "no agent CLI installed+auth'd"))

    # reflection: default reviewer
    if reviewer in avail:
        rows.append(FeatureRow("reflection", reviewer, reviewer, PASS, f"reviewer {reviewer} available"))
    elif avail:
        rows.append(FeatureRow(
            "reflection", reviewer, None, WARN,
            f"reviewer {reviewer} missing — pass --agent {sorted(avail)[0]} to odin reflect",
        ))
    else:
        rows.append(FeatureRow("reflection", reviewer, None, FAIL, f"reviewer {reviewer} missing"))

    # merge-resolve
    if merge_agent in avail:
        rows.append(FeatureRow("merge-resolve", merge_agent, merge_agent, PASS,
                               f"merge agent {merge_agent} available"))
    elif avail:
        rows.append(FeatureRow(
            "merge-resolve", merge_agent, None, WARN,
            f"merge agent {merge_agent} missing — set merge_agent.agent={sorted(avail)[0]}",
        ))
    else:
        rows.append(FeatureRow("merge-resolve", merge_agent, None, FAIL,
                               f"merge agent {merge_agent} missing"))

    # summarize: any agent
    if avail:
        rows.append(FeatureRow("summarize", "any", sorted(avail)[0], PASS, _any_agent_note()))
    else:
        rows.append(FeatureRow("summarize", "any", None, FAIL, "no agent available"))

    return rows


# ── orchestrator ──────────────────────────────────────────────────────────


def run_doctor(
    cfg: OdinConfig,
    *,
    fast: bool = False,
    backend_port: int = 9100,
    frontend_port: int = 9200,
    merge_queue_name: Optional[str] = None,
    sandbox_image: str = "odin-agents",
    vm_mem_budget_mib: int = 4096,
) -> DoctorReport:
    """Run all checks and return a DoctorReport.

    Args:
        cfg: loaded OdinConfig (for base_agent / merge_agent / agent CLIs).
        fast: skip the throwaway-VM boot check (the only slow probe).
        backend_port / frontend_port: service ports to probe.
        merge_queue_name: if set, also check for a -Q <name> celery worker.
        sandbox_image: msb image/snapshot name to verify.
        vm_mem_budget_mib: per-VM memory budget to compare free RAM against.
    """
    report = DoctorReport()

    # --- services ---
    report.checks.append(_check_service_port(
        "services", "backend", backend_port,
        "sh docs/fable_roadmap/bootstrap/start_services.sh start",
    ))
    report.checks.append(_check_service_port(
        "services", "frontend", frontend_port,
        "sh docs/fable_roadmap/bootstrap/start_services.sh start",
    ))
    report.checks.append(_check_celery_default())
    if merge_queue_name:
        report.checks.append(_check_celery_merges(merge_queue_name))

    # --- agents ---
    for name, default_cli in _PROVIDERS:
        agent_cfg = cfg.agents.get(name)
        cli = (agent_cfg.cli_command if agent_cfg and agent_cfg.cli_command else default_cli)
        report.agents.append(_probe_agent(name, cli))
    available = {a.name for a in report.agents if a.available}

    # --- base_agent CLI probe ---
    # The most common getting-started trap: base_agent defaults to "claude"
    # but a single-provider user (e.g. codex-only) doesn't have it. Surface
    # the mismatch as a named check with a fix so it's obvious before any
    # run crashes deep in a subprocess. (task #252)
    report.checks.append(_check_base_agent_available(cfg, available))

    # --- claude-token staleness probes ---
    # One WARN per stale fallback file the harness could pick if the live
    # source (env var / macOS Keychain) becomes unreadable. See
    # `harnesses/microsandbox.py::_claude_token` for the resolution order.
    report.checks.extend(_check_claude_token_staleness(cfg))

    # --- sandbox ---
    msb_check, bin_path = _check_msb_runtime(sandbox_image)
    report.checks.append(msb_check)
    if bin_path:
        image_check = _check_msb_image(bin_path, sandbox_image)
        report.checks.append(image_check)
        if fast:
            report.checks.append(Check(
                "sandbox", "throwaway VM boot", SKIP, "skipped (--fast)",
            ))
        elif image_check.status == PASS:
            # Only attempt a boot when there is an image to boot from — booting
            # from a missing image just re-reports the missing image.
            report.checks.append(_check_vm_boot(bin_path, sandbox_image))
        else:
            report.checks.append(Check(
                "sandbox", "throwaway VM boot", SKIP,
                "skipped (sandbox image not found)",
            ))
    else:
        report.checks.append(Check(
            "sandbox", "sandbox image", SKIP, "skipped (msb not installed)",
        ))
        report.checks.append(Check(
            "sandbox", "throwaway VM boot", SKIP, "skipped (msb not installed)",
        ))

    # --- host ---
    report.checks.append(_check_memory(vm_mem_budget_mib))
    report.checks.append(_check_disk())

    # --- feature matrix (partial-provider honesty) ---
    report.features = derive_feature_matrix(cfg, available)

    return report


# ── formatters ────────────────────────────────────────────────────────────


_STATUS_SYMBOL = {PASS: "✓", WARN: "!", FAIL: "✗", SKIP: "·"}


def format_text(report: DoctorReport) -> str:
    """Human-readable table output (the default)."""
    lines: list[str] = []
    lines.append("Odin Doctor\n")

    # group checks by category
    by_cat: dict[str, list[Check]] = {}
    for c in report.checks:
        by_cat.setdefault(c.category, []).append(c)
    for cat in ("services", "agents", "sandbox", "host"):
        if cat not in by_cat:
            continue
        lines.append(f"[{cat}]")
        for c in by_cat[cat]:
            sym = _STATUS_SYMBOL.get(c.status, "?")
            lines.append(f"  {sym} {c.status:<4} {c.name} — {c.detail}")
            if c.fix:
                lines.append(f"         fix: {c.fix}")
        lines.append("")

    # agents table
    lines.append("[agents]")
    for a in report.agents:
        sym = _STATUS_SYMBOL.get(a.status, "?")
        bits = []
        if not a.installed:
            bits.append("CLI missing")
        elif not a.authenticated:
            bits.append("not authenticated")
        detail = a.detail or ("available" if a.available else "unavailable")
        lines.append(f"  {sym} {a.status:<4} {a.name:<8} ({a.cli}) — {detail}")
    lines.append("")

    # feature matrix
    lines.append("[capability matrix]")
    for f in report.features:
        sym = _STATUS_SYMBOL.get(f.status, "?")
        lines.append(
            f"  {sym} {f.status:<4} {f.feature:<14} default={f.default_agent} — {f.note}"
        )
    lines.append("")

    # summary
    if report.blocks_basic_run:
        lines.append("RESULT: FAIL — no agent CLI is installed AND authenticated.")
        lines.append("        Install+auth at least one of: claude, codex, glm, minimax, agy.")
    else:
        lines.append("RESULT: OK — a basic run is serviceable. Warnings above are advisory.")
    return "\n".join(lines) + "\n"


def format_json(report: DoctorReport) -> str:
    """JSON output for scripts (--json)."""
    return _json.dumps(report.to_dict(), indent=2, sort_keys=True)
