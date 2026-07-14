"""Harness-risk-surface security sweep (task 245).

The bandit tool covers generic Python AST patterns; this sweep covers the
four surface areas where the harness IS the security variable
(docs/wiki/security/endorlabs-gpt55-cursor-code-security.md):

  1. Credential mounts (microsandbox.py::_CREDENTIAL_PATHS)
  2. Token handling (env-var injection of OAuth tokens into the guest)
  3. Sandbox egress flags (microsandbox.py::_net_flags auto-promote to allow)
  4. Subprocess invocations with shell=True

Stdlib only — no pip install required. The script is dependency-free so
the security-audit preset can run it from any environment (CI, fresh
worktree, prod host) without provisioning.

Outputs JSON to stdout (one object per run) with shape:

    {
      "version": 1,
      "findings": [
        {
          "id": "SEC-001",
          "severity": "P2",
          "category": "subprocess",
          "location": "odin/src/odin/worktree.py:864",
          "summary": "...",
          "evidence": "..."
        }
      ],
      "counts": {"P0": N, "P1": N, "P2": N, "P3": N},
      "source": "task-245"
    }

The security-audit preset's runner reads this JSON, computes the
delta-from-baseline via scripts/security_baseline.py, and renders the
markdown report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# The repo root is two parents up from this file (scripts/security_audit.py).
# Resolve at runtime so the script works from any cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO_ROOT / "odin" / "src" / "odin" / "harnesses"
PY_SURFACES = [
    REPO_ROOT / "odin" / "src",
    REPO_ROOT / "taskit" / "taskit-backend",
    REPO_ROOT / "harness_usage_status",
]

VERSION = 1


def _make_id(n: int) -> str:
    return f"SEC-{n:03d}"


def _scan_credential_mounts(findings: list[dict]) -> None:
    """Check microsandbox.py::_CREDENTIAL_PATHS for wildcards / loose globs."""
    path = HARNESS_DIR / "microsandbox.py"
    if not path.exists():
        return

    text = path.read_text()
    # Anything that ends with * or contains unanchored wildcards is a finding.
    # We grep the _CREDENTIAL_PATHS block specifically.
    in_block = False
    saw_block = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "_CREDENTIAL_PATHS" in line and "{" in line:
            in_block = True
            saw_block = True
            continue
        if in_block:
            if line.strip().startswith("}"):
                in_block = False
                continue
            # Wildcard-style credential paths are a P1 — every mounted file is
            # a candidate exfiltration target if the guest is compromised.
            if "*" in line and ".py" not in line:
                findings.append(
                    {
                        "id": _make_id(len(findings) + 1),
                        "severity": "P1",
                        "category": "credential-mount",
                        "location": f"{path.relative_to(REPO_ROOT)}:{lineno}",
                        "summary": "Credential mount path contains a wildcard",
                        "evidence": line.strip(),
                    }
                )
    if not saw_block:
        findings.append(
            {
                "id": _make_id(len(findings) + 1),
                "severity": "P3",
                "category": "credential-mount",
                "location": str(path.relative_to(REPO_ROOT)),
                "summary": "_CREDENTIAL_PATHS block not found; harness may have refactored",
                "evidence": "expected _CREDENTIAL_PATHS dict in microsandbox.py",
            }
        )


def _scan_token_handling(findings: list[dict]) -> None:
    """Check whether OAuth tokens land in guest env vars (env-var exfil surface)."""
    path = HARNESS_DIR / "microsandbox.py"
    if not path.exists():
        return
    text = path.read_text()

    # The harness intentionally passes claude OAuth token via
    # CLAUDE_CODE_OAUTH_TOKEN env into the guest, and agy via AGY_SECRET.
    # This is necessary today (no other way to authenticate) but worth
    # surfacing as P3 — anyone with egress from the guest can read it.
    # A future microVM could pass via a sealed unix socket instead.
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "CLAUDE_CODE_OAUTH_TOKEN" in line and "-e" in line:
            findings.append(
                {
                    "id": _make_id(len(findings) + 1),
                    "severity": "P3",
                    "category": "token-handling",
                    "location": f"{path.relative_to(REPO_ROOT)}:{lineno}",
                    "summary": (
                        "Claude OAuth token injected into guest env "
                        "(-e CLAUDE_CODE_OAUTH_TOKEN=…). Exfil surface if "
                        "guest egress is open."
                    ),
                    "evidence": line.strip(),
                }
            )
            break
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "AGY_SECRET" in line and "-e" in line:
            findings.append(
                {
                    "id": _make_id(len(findings) + 1),
                    "severity": "P3",
                    "category": "token-handling",
                    "location": f"{path.relative_to(REPO_ROOT)}:{lineno}",
                    "summary": (
                        "Antigravity (agy) OAuth token injected into guest env. "
                        "Exfil surface if guest egress is open."
                    ),
                    "evidence": line.strip(),
                }
            )
            break


def _scan_sandbox_egress(findings: list[dict]) -> None:
    """Find the auto-promote-to-allow-egress code path."""
    path = HARNESS_DIR / "microsandbox.py"
    if not path.exists():
        return
    text = path.read_text()

    # Look for:  if needs_host and not rules and not net_default: return ["--net-default-egress", "allow"]
    # This is a real finding — when MCP config is in play and no rules are
    # configured, the harness silently widens egress to allow. Default-First
    # is appropriate for dev, but a P2 audit finding for any deployment that
    # wants to track this.
    pattern = re.compile(
        r'if\s+needs_host\s+and\s+not\s+rules\s+and\s+not\s+net_default:.*?'
        r'return\s+\["--net-default-egress"\s*,\s*"allow"\]',
        re.DOTALL,
    )
    if pattern.search(text):
        # Find the actual line number for the report.
        for lineno, line in enumerate(text.splitlines(), start=1):
            if '"--net-default-egress"' in line and '"allow"' in line:
                findings.append(
                    {
                        "id": _make_id(len(findings) + 1),
                        "severity": "P2",
                        "category": "sandbox-egress",
                        "location": f"{path.relative_to(REPO_ROOT)}:{lineno}",
                        "summary": (
                            "Harness auto-promotes egress to `allow` when MCP "
                            "config is in play and no net rules are configured. "
                            "This is the Endor Labs 'harness is a security "
                            "variable' pattern in microcosm — the default widens "
                            "silently."
                        ),
                        "evidence": line.strip(),
                    }
                )
                break


def _scan_subprocess_shell(findings: list[dict]) -> None:
    """Grep for subprocess.* with shell=True (CWE-78 surface)."""
    # We re-implement the grep in Python because git grep may not be on PATH
    # in CI containers and the security audit must not depend on it.
    pattern = re.compile(r"subprocess\.(run|call|Popen|check_output|check_call)\(")
    shell_pattern = re.compile(r"shell\s*=\s*True")

    for surface in PY_SURFACES:
        if not surface.exists():
            continue
        for py_file in surface.rglob("*.py"):
            # Skip virtualenvs / __pycache__ — they never have shell=True that
            # we wrote.
            parts = py_file.parts
            if ".venv" in parts or "__pycache__" in parts or "node_modules" in parts:
                continue
            try:
                text = py_file.read_text(errors="replace")
            except OSError:
                continue

            # Find every subprocess.* call, then look at the surrounding
            # ~6 lines for shell=True. A bare call without shell=True is fine.
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if not pattern.search(line):
                    continue
                # Scan the call block (next ~6 non-empty lines) for shell=True.
                window_end = min(i + 8, len(lines))
                window = "\n".join(lines[i:window_end])
                if not shell_pattern.search(window):
                    continue

                # Disambiguate: shell=True driven by operator config (post-create
                # hooks) is P3 (config-controlled, not agent-controlled).
                # shell=True driven by anything else (CLI flag, env, etc.) is P2.
                severity = "P3" if "post_hooks" in window or "hook_cmd" in window else "P2"
                cat = "subprocess"
                findings.append(
                    {
                        "id": _make_id(len(findings) + 1),
                        "severity": severity,
                        "category": cat,
                        "location": f"{py_file.relative_to(REPO_ROOT)}:{i + 1}",
                        "summary": "subprocess.* with shell=True",
                        "evidence": line.strip(),
                    }
                )


def _scan_hardcoded_secrets(findings: list[dict]) -> None:
    """Find literal-looking secret assignments (api_key = '...', token = '...').

    Two intentional skips:
    - Test files (`/tests/`, `test_*.py`, `*_test.py`) — they routinely
      embed fake secrets as fixtures. A real production scanner (gitleaks,
      trufflehog) has a richer allowlist; ours keeps it simple.
    - Public OAuth client_id / client_secret constants whose surrounding
      comment marks them as published credentials ("public installed-app",
      "safe to embed", etc.). Google publishes these for the Gemini CLI
      installed-app flow; embedding them is the documented pattern, not
      a leak.
    """
    patterns = [
        re.compile(r'(?i)(api[_-]?key|token|password|secret)\s*=\s*["\'][A-Za-z0-9_\-]{16,}["\']'),
    ]
    public_markers = (
        "public installed-app",
        "safe to embed",
        "public oauth client",
        "oauth client id",
        "oauth client secret",
    )
    for surface in PY_SURFACES:
        if not surface.exists():
            continue
        for py_file in surface.rglob("*.py"):
            parts = py_file.parts
            if ".venv" in parts or "__pycache__" in parts or "node_modules" in parts:
                continue
            # Test files are a known source of fake-secret false positives.
            if "/tests/" in parts or py_file.name.startswith("test_"):
                continue
            try:
                text = py_file.read_text(errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            for lineno, line in enumerate(lines):
                # Skip obvious non-secret occurrences.
                if "os.environ" in line or "config.get" in line:
                    continue
                if "TODO" in line or "example" in line.lower():
                    continue
                matched = False
                for pat in patterns:
                    if pat.search(line):
                        matched = True
                        break
                if not matched:
                    continue

                # Window of 5 lines above for "safe to embed" markers.
                window_start = max(0, lineno - 5)
                window = "\n".join(lines[window_start:lineno + 1]).lower()
                if any(marker in window for marker in public_markers):
                    # Downgrade to P3 — known-public credentials.
                    findings.append(
                        {
                            "id": _make_id(len(findings) + 1),
                            "severity": "P3",
                            "category": "hardcoded-secret",
                            "location": f"{py_file.relative_to(REPO_ROOT)}:{lineno}",
                            "summary": (
                                "Public OAuth client constant (downgraded "
                                "from P0; comment marks as published credential)"
                            ),
                            "evidence": line.strip()[:200],
                        }
                    )
                    continue

                findings.append(
                    {
                        "id": _make_id(len(findings) + 1),
                        "severity": "P0",
                        "category": "hardcoded-secret",
                        "location": f"{py_file.relative_to(REPO_ROOT)}:{lineno}",
                        "summary": "Possible hardcoded credential in source",
                        "evidence": line.strip()[:200],
                    }
                )


def _scan_yaml_egress(findings: list[dict]) -> None:
    """Check .odin/config.yaml for sandbox egress posture."""
    config_path = REPO_ROOT / ".odin" / "config.yaml"
    if not config_path.exists():
        # No config is fine — default-first. Not a finding.
        return
    try:
        import yaml  # type: ignore[import-not-found]

        cfg = yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        # Don't gate the audit on yaml parsing — surface the parse issue.
        findings.append(
            {
                "id": _make_id(len(findings) + 1),
                "severity": "P3",
                "category": "sandbox-egress",
                "location": str(config_path.relative_to(REPO_ROOT)),
                "summary": "Could not parse .odin/config.yaml; egress posture unknown",
                "evidence": "yaml.safe_load failed",
            }
        )
        return

    agents = (cfg or {}).get("agents") or {}
    for name, agent_cfg in agents.items():
        if not isinstance(agent_cfg, dict):
            continue
        # microsandbox agents with no net config = silent allow@public default.
        if agent_cfg.get("sandbox_mode") == "microsandbox":
            net_default = agent_cfg.get("microsandbox_net_default")
            net_rules = agent_cfg.get("microsandbox_net_rules") or []
            if not net_default and not net_rules:
                findings.append(
                    {
                        "id": _make_id(len(findings) + 1),
                        "severity": "P2",
                        "category": "sandbox-egress",
                        "location": f".odin/config.yaml:agents.{name}",
                        "summary": (
                            f"Agent {name!r} uses microsandbox with no "
                            f"microsandbox_net_default and no "
                            f"microsandbox_net_rules — egress is "
                            f"implicitly allow@public."
                        ),
                        "evidence": f"agents.{name}.sandbox_mode = microsandbox",
                    }
                )


def _counts(findings: list[dict]) -> dict[str, int]:
    out = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for f in findings:
        sev = f.get("severity", "P3")
        out[sev] = out.get(sev, 0) + 1
    return out


def run_sweep() -> dict:
    findings: list[dict] = []
    _scan_credential_mounts(findings)
    _scan_token_handling(findings)
    _scan_sandbox_egress(findings)
    _scan_subprocess_shell(findings)
    _scan_hardcoded_secrets(findings)
    _scan_yaml_egress(findings)
    return {
        "version": VERSION,
        "findings": findings,
        "counts": _counts(findings),
        "source": "scripts/security_audit.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="Write JSON here (default: stdout).",
    )
    parser.add_argument(
        "--source-label",
        default="task-245",
        help="Provenance label stored in the JSON (default: task-245).",
    )
    args = parser.parse_args(argv)

    result = run_sweep()
    result["source"] = args.source_label

    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n")

    # Exit nonzero ONLY for P0 (hardcoded secrets). Everything else is
    # reported but does not fail the run — the audit is a read.
    return 1 if result["counts"]["P0"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())