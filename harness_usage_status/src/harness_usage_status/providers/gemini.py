"""Gemini CLI subscription provider.

Fetches quota data from the Google Cloud Code Assist API — the same
endpoint the Gemini CLI's ``/stats`` command uses internally:

    POST https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota

Auth: Google OAuth token stored by the Gemini CLI in
``~/.gemini/oauth_creds.json`` (or macOS Keychain service ``gemini-cli-oauth``).

The ``projectId`` required by the endpoint is obtained by calling
``loadCodeAssist`` (or read from config / env vars).

Response contains per-model ``BucketInfo`` entries with:
  - ``remainingAmount``  (string, absolute remaining count)
  - ``remainingFraction`` (float, 0-1)
  - ``resetTime``        (ISO-8601 timestamp)
  - ``modelId``          (e.g. ``gemini-2.5-pro``)

Limit is derived as ``round(remainingAmount / remainingFraction)``.

Refs:
  - https://github.com/google-gemini/gemini-cli (packages/core/src/code_assist/)
"""

import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from math import inf
from typing import Optional

import httpx

from harness_usage_status.cli_runner import find_cli, run_cli
from harness_usage_status.models import ProviderState, StatusInfo, UsageInfo
from harness_usage_status.providers.base import BaseProvider
from harness_usage_status.providers.registry import register_provider

# ---------------------------------------------------------------------------
# Gemini CLI OAuth constants (public installed-app credentials, safe to embed)
# ---------------------------------------------------------------------------
OAUTH_CLIENT_ID = (
    "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)
OAUTH_CLIENT_SECRET = "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"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
CODE_ASSIST_API_VERSION = "v1internal"

OAUTH_CREDS_PATH = os.path.expanduser("~/.gemini/oauth_creds.json")


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _load_oauth_creds() -> Optional[dict]:
    """Load cached Gemini CLI OAuth credentials.

    Search order:
      1. GEMINI_OAUTH_TOKEN env var (access token only)
      2. macOS Keychain — service ``gemini-cli-oauth``
      3. ``~/.gemini/oauth_creds.json``
    """
    # 1. Env var shortcut
    token = os.environ.get("GEMINI_OAUTH_TOKEN")
    if token:
        return {"access_token": token}

    # 2. macOS Keychain
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    "gemini-cli-oauth",
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
        except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 3. File on disk
    try:
        with open(OAUTH_CREDS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return None


async def _refresh_access_token(refresh_token: str) -> Optional[str]:
    """Exchange a refresh token for a new access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OAUTH_TOKEN_URL,
            data={
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
    return None


async def _get_access_token(creds: dict) -> Optional[str]:
    """Return a valid access token, refreshing if necessary."""
    access_token = creds.get("access_token")

    # Check expiry (field may be epoch-ms or epoch-s)
    expiry = creds.get("expiry_date") or creds.get("expiry")
    if expiry and access_token:
        expiry_s = expiry / 1000 if expiry > 1e12 else expiry
        if time.time() > expiry_s:
            access_token = None  # force refresh

    if access_token:
        return access_token

    refresh_token = creds.get("refresh_token")
    if refresh_token:
        return await _refresh_access_token(refresh_token)

    return None


# ---------------------------------------------------------------------------
# Code Assist API helpers
# ---------------------------------------------------------------------------

def _api_url(method: str) -> str:
    endpoint = os.environ.get("CODE_ASSIST_ENDPOINT", CODE_ASSIST_ENDPOINT)
    version = os.environ.get("CODE_ASSIST_API_VERSION", CODE_ASSIST_API_VERSION)
    return f"{endpoint}/{version}:{method}"


async def _fetch_project_id(token: str) -> Optional[str]:
    """Call ``loadCodeAssist`` to obtain the server-assigned project ID."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api_url("loadCodeAssist"),
            json={},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            project = data.get("cloudaicompanionProject")
            if isinstance(project, dict):
                return project.get("id") or project.get("name")
            if isinstance(project, str):
                return project
    return None


async def _retrieve_user_quota(token: str, project_id: str) -> dict:
    """Call ``retrieveUserQuota`` and return the raw response dict."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api_url("retrieveUserQuota"),
            json={"project": project_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return {"error": f"API returned {resp.status_code}", "body": resp.text[:500]}
        return resp.json()


def _parse_buckets(data: dict) -> dict:
    """Parse ``retrieveUserQuota`` response into structured per-model info.

    Returns a dict with:
      - ``models``: {model_id: {remaining_pct, used_pct, remaining, limit, reset_time}}
      - ``worst_remaining_pct``: lowest remaining % across all models
      - ``worst_model``: model_id with the highest usage
    """
    buckets = data.get("buckets") or []
    models: dict = {}
    worst_remaining_pct = 100.0
    worst_model = None

    for b in buckets:
        model_id = b.get("modelId")
        if not model_id:
            continue
        # Skip _vertex duplicates (same quota, different routing)
        if model_id.endswith("_vertex"):
            continue

        remaining_fraction = b.get("remainingFraction")
        if remaining_fraction is None:
            continue

        remaining_amount = b.get("remainingAmount")

        # Derive limit and remaining from whichever fields are available
        if remaining_amount is not None:
            remaining = int(remaining_amount)
            limit = round(remaining / remaining_fraction) if remaining_fraction > 0 else 0
        else:
            # Only fraction available — report as percentage
            remaining = None
            limit = None

        remaining_pct = round(remaining_fraction * 100, 1)
        used_pct = round(100 - remaining_pct, 1)

        models[model_id] = {
            "remaining_pct": remaining_pct,
            "used_pct": used_pct,
            "remaining": remaining,
            "limit": limit,
            "reset_time": b.get("resetTime"),
        }

        if remaining_pct < worst_remaining_pct:
            worst_remaining_pct = remaining_pct
            worst_model = model_id

    return {
        "models": models,
        "worst_remaining_pct": worst_remaining_pct,
        "worst_model": worst_model,
    }


def _format_reset(iso_str: Optional[str]) -> str:
    """Format an ISO-8601 reset time as a human-readable duration."""
    if not iso_str:
        return ""
    try:
        reset_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = reset_dt - datetime.now(timezone.utc)
        total_secs = max(int(delta.total_seconds()), 0)
        hours, remainder = divmod(total_secs, 3600)
        minutes = remainder // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

@register_provider("gemini")
class GeminiProvider(BaseProvider):
    """Gemini CLI subscription provider.

    Fetches per-model quota from the Google Code Assist API
    (the same backend ``gemini /stats`` uses).

    Config extras:
      cli_path:    path to gemini binary (for status checks)
      cli_command: command name (default: ``gemini``)
      project_id:  Cloud AI Companion project ID (auto-detected if omitted)
    """

    @property
    def name(self) -> str:
        return "Gemini"

    def _cli(self) -> str:
        extras = self.config.get("extras", {})
        return extras.get("cli_path") or extras.get("cli_command") or "gemini"

    def _project_id(self) -> Optional[str]:
        extras = self.config.get("extras", {})
        return (
            extras.get("project_id")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        )

    async def get_usage(self) -> UsageInfo:
        # --- Load OAuth credentials ---
        creds = _load_oauth_creds()
        if not creds:
            return UsageInfo(
                provider=self.name,
                raw={
                    "error": "No Gemini OAuth credentials found "
                    "(run `gemini` CLI once to authenticate, "
                    "or set GEMINI_OAUTH_TOKEN)"
                },
            )

        token = await _get_access_token(creds)
        if not token:
            return UsageInfo(
                provider=self.name,
                raw={"error": "Could not obtain access token (refresh may have failed)"},
            )

        # --- Resolve project ID ---
        project_id = self._project_id()
        if not project_id:
            project_id = await _fetch_project_id(token)
        if not project_id:
            return UsageInfo(
                provider=self.name,
                raw={
                    "error": "Could not determine project ID. "
                    "Set project_id in config extras or "
                    "GOOGLE_CLOUD_PROJECT env var."
                },
            )

        # --- Fetch quota ---
        data = await _retrieve_user_quota(token, project_id)
        if "error" in data:
            return UsageInfo(provider=self.name, raw=data)

        parsed = _parse_buckets(data)
        models = parsed["models"]

        if not models:
            return UsageInfo(
                provider=self.name,
                plan="Subscription",
                raw={"note": "No quota buckets returned", **data},
            )

        worst_remaining = parsed["worst_remaining_pct"]
        usage_pct = round(100 - worst_remaining, 1)

        return UsageInfo(
            provider=self.name,
            plan="Subscription",
            usage_pct=usage_pct,
            used=usage_pct,
            remaining=worst_remaining,
            quota_limit=100,
            unit="%",
            raw={
                "models": models,
                "worst_model": parsed["worst_model"],
            },
        )

    async def get_status(self) -> StatusInfo:
        cli = self._cli()
        start = time.monotonic()
        if not find_cli(cli):
            return StatusInfo(
                provider=self.name,
                state=ProviderState.OFFLINE,
                last_checked=datetime.now(),
                message=f"CLI '{cli}' not found on PATH",
            )

        result = await run_cli([cli, "--version"], timeout=10)
        latency = (time.monotonic() - start) * 1000
        state = ProviderState.ONLINE if result.ok else ProviderState.DEGRADED

        return StatusInfo(
            provider=self.name,
            state=state,
            latency_ms=round(latency, 1),
            last_checked=datetime.now(),
            message=result.stdout.strip() if result.ok else result.stderr[:200],
        )
