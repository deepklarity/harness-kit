"""Generic, dependency-free security sweep for hk-security-audit.

Reads source and config under one or more target paths and flags six kinds
of finding, each verifiable by reading the quoted lines:

  1. secret-leak       — a hardcoded API key, token or private key in a
                          tracked file
  2. auth-missing      — a framework default that leaves an endpoint or the
                          whole app unauthenticated (CSRF disabled, JWT
                          signature check off, wildcard CORS or hosts, auth
                          explicitly turned off)
  3. credential-mount   — a container/sandbox mount or volume that maps a
                          credential directory (SSH keys, cloud creds,
                          npm/git tokens) with a wildcard or into the guest
                          root
  4. token-into-sandbox — an OAuth/API token or secret passed as an
                          environment variable into a subprocess, container
                          or sandbox invocation
  5. egress-default-allow — sandbox or network config whose default posture
                          is to allow outbound traffic
  6. shell-true         — subprocess.* called with shell=True

No third-party scanner is shelled out to and nothing is installed; every
check is a plain read of the files under the given paths. Stdlib only, so
it runs anywhere without provisioning.

Output JSON (one object) to stdout or --output:

    {
      "version": 1,
      "findings": [
        {"id": "SEC-001", "severity": "P1", "category": "shell-true",
         "location": "path/to/file.py:42", "summary": "...", "evidence": "..."}
      ],
      "counts": {"P0": N, "P1": N, "P2": N, "P3": N}
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VERSION = 1

EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", "node_modules", "dist", "build", "out",
    "coverage", ".git", ".proof", ".tox", "vendor",
}

CODE_EXTS = ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.json", "*.env*",
             "*.yml", "*.yaml", "*.toml", "*.cfg", "*.ini")


def _make_id(n: int) -> str:
    return f"SEC-{n:03d}"


def _iter_files(target_dir: Path, patterns):
    seen = set()
    for pattern in patterns:
        for f in target_dir.rglob(pattern):
            if f in seen or not f.is_file():
                continue
            if set(f.parts) & EXCLUDE_DIRS:
                continue
            seen.add(f)
            yield f


def _rel(f: Path, target_dir: Path) -> str:
    try:
        return f.relative_to(target_dir).as_posix()
    except ValueError:
        return f.name


def _scan_secrets(findings: list[dict], target_dir: Path) -> None:
    """Secrets in tracked files: hardcoded keys, tokens, private keys."""
    patterns = [
        ("OpenAI-style key", re.compile(r"sk-[a-zA-Z0-9]{32,}")),
        ("Anthropic-style key", re.compile(r"sk-ant-[a-zA-Z0-9]{32,}")),
        ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
        ("JWT", re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+")),
        ("private key block", re.compile(r"-----BEGIN (RSA|OPENSSH|EC|PGP) PRIVATE KEY-----")),
    ]
    for f in _iter_files(target_dir, CODE_EXTS):
        if f.name.endswith((".min.js", ".bundle.js")):
            continue
        if "/tests/" in f.as_posix() or f.name.startswith("test_"):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            upper = line.upper()
            if "EXAMPLE" in upper or "MOCK" in upper or "DUMMY" in upper or "PLACEHOLDER" in upper:
                continue
            for label, pat in patterns:
                if pat.search(line):
                    findings.append({
                        "id": _make_id(len(findings) + 1),
                        "severity": "P0",
                        "category": "secret-leak",
                        "location": f"{_rel(f, target_dir)}:{lineno}",
                        "summary": f"Hardcoded {label} in source",
                        "evidence": line.strip()[:120],
                    })


def _scan_auth_missing(findings: list[dict], target_dir: Path) -> None:
    """Endpoints/apps left unauthenticated by a framework default."""
    checks = [
        (re.compile(r"@csrf_exempt"), "csrf-disabled", "P2",
         "CSRF protection explicitly disabled"),
        (re.compile(r"verify_signature\s*=\s*False"), "insecure-jwt", "P1",
         "JWT signature verification disabled"),
        (re.compile(r"CORS_ALLOW_ALL_ORIGINS\s*=\s*True"), "permissive-cors", "P2",
         "CORS configured to allow all origins"),
        (re.compile(r"ALLOWED_HOSTS\s*=\s*\[\s*['\"]\*['\"]\s*\]"), "allowed-hosts-wildcard", "P1",
         "ALLOWED_HOSTS accepts any host (*)"),
        (re.compile(r"(AUTH_ENABLED|ENABLE_AUTH)\s*=\s*False", re.IGNORECASE), "auth-disabled", "P1",
         "Authentication explicitly disabled, leaving endpoints unauthenticated"),
    ]
    for f in _iter_files(target_dir, ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat, category, severity, summary in checks:
                if pat.search(line):
                    findings.append({
                        "id": _make_id(len(findings) + 1),
                        "severity": severity,
                        "category": "auth-missing",
                        "location": f"{_rel(f, target_dir)}:{lineno}",
                        "summary": summary,
                        "evidence": line.strip()[:120],
                    })


CREDENTIAL_PATH_HINTS = (".ssh", ".aws", ".netrc", ".npmrc", ".git-credentials",
                          "id_rsa", ".docker/config.json", ".kube/config")


def _scan_credential_mounts(findings: list[dict], target_dir: Path) -> None:
    """Container/sandbox mounts that map a credential path with a wildcard
    or into the guest root."""
    mount_keyword = re.compile(r"\b(mount|volume|volumes|bind)\b", re.IGNORECASE)
    for f in _iter_files(target_dir, ("*.py", "*.yml", "*.yaml", "*.json", "Dockerfile*")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not any(hint in line for hint in CREDENTIAL_PATH_HINTS):
                continue
            if not mount_keyword.search(line) and ":" not in line.split("=", 1)[-1]:
                continue
            if "*" not in line and ":/" not in line and "->" not in line:
                continue
            findings.append({
                "id": _make_id(len(findings) + 1),
                "severity": "P1",
                "category": "credential-mount",
                "location": f"{_rel(f, target_dir)}:{lineno}",
                "summary": "Credential path mounted into a container or sandbox with a wildcard or full bind",
                "evidence": line.strip()[:160],
            })


TOKEN_NAME = re.compile(r"[A-Z0-9_]*(TOKEN|API_KEY|SECRET|PASSWORD)[A-Z0-9_]*")


def _scan_token_into_sandbox(findings: list[dict], target_dir: Path) -> None:
    """A token/secret passed as an env var into a subprocess, container or
    sandbox invocation (env-var exfil surface)."""
    env_flag = re.compile(r"[\"']?(-e|--env)[\"']?")
    var_assign = re.compile(r"([A-Za-z0-9_]+)\s*=\s*[{f\"']")
    for f in _iter_files(target_dir, ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.sh")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not env_flag.search(line):
                continue
            for m in var_assign.finditer(line):
                var_name = m.group(1)
                if TOKEN_NAME.search(var_name):
                    findings.append({
                        "id": _make_id(len(findings) + 1),
                        "severity": "P3",
                        "category": "token-into-sandbox",
                        "location": f"{_rel(f, target_dir)}:{lineno}",
                        "summary": f"Token/secret env var {var_name!r} injected into a subprocess or sandbox invocation",
                        "evidence": line.strip()[:160],
                    })
                    break


def _scan_egress_default_allow(findings: list[dict], target_dir: Path) -> None:
    """Sandbox/network config whose default posture is to allow egress."""
    patterns = [
        re.compile(r"net[-_]default[-_]egress[\"']?\s*[:=,]\s*[\"']?allow", re.IGNORECASE),
        re.compile(r"network_mode\s*[:=]\s*[\"']?host", re.IGNORECASE),
        re.compile(r"egress\s*[:=]\s*[\"']?allow", re.IGNORECASE),
    ]
    for f in _iter_files(target_dir, ("*.py", "*.yml", "*.yaml", "*.json", "*.toml")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(pat.search(line) for pat in patterns):
                findings.append({
                    "id": _make_id(len(findings) + 1),
                    "severity": "P2",
                    "category": "egress-default-allow",
                    "location": f"{_rel(f, target_dir)}:{lineno}",
                    "summary": "Sandbox or network config defaults outbound egress to allow",
                    "evidence": line.strip()[:160],
                })


def _scan_shell_true(findings: list[dict], target_dir: Path) -> None:
    """subprocess.* called with shell=True."""
    call_pat = re.compile(r"subprocess\.(run|call|Popen|check_output|check_call)\(")
    shell_pat = re.compile(r"shell\s*=\s*True")
    for f in _iter_files(target_dir, ("*.py",)):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not call_pat.search(line):
                continue
            window = "\n".join(lines[i:min(i + 8, len(lines))])
            if not shell_pat.search(window):
                continue
            findings.append({
                "id": _make_id(len(findings) + 1),
                "severity": "P2",
                "category": "shell-true",
                "location": f"{_rel(f, target_dir)}:{i + 1}",
                "summary": "subprocess.* called with shell=True",
                "evidence": line.strip()[:160],
            })


def _counts(findings: list[dict]) -> dict[str, int]:
    out = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for f in findings:
        sev = f.get("severity", "P3")
        out[sev] = out.get(sev, 0) + 1
    return out


def run_sweep(target_dirs: list[Path]) -> dict:
    findings: list[dict] = []
    for target in target_dirs:
        _scan_secrets(findings, target)
        _scan_auth_missing(findings, target)
        _scan_credential_mounts(findings, target)
        _scan_token_into_sandbox(findings, target)
        _scan_egress_default_allow(findings, target)
        _scan_shell_true(findings, target)
    return {"version": VERSION, "findings": findings, "counts": _counts(findings)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets", nargs="*", default=["."],
        help="Paths to scan (default: current directory).",
    )
    parser.add_argument(
        "--output", "-o", default="-",
        help="Write JSON here (default: stdout).",
    )
    args = parser.parse_args(argv)

    target_paths = [Path(t).resolve() for t in args.targets]
    result = run_sweep(target_paths)

    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output == "-":
        sys.stdout.write(payload + "\n")
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n")

    return 1 if result["counts"]["P0"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
