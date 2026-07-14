"""forkd-backed harness wrapper."""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import time

import httpx
from filelock import FileLock
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler, BaseHTTPRequestHandler
from pathlib import Path
from typing import AsyncIterator

from odin.harnesses.base import BaseHarness, extract_text_from_line, extract_text_from_stream, validate_odin_status, validate_odin_status_full
from odin.models import AgentConfig, TaskResult


_GUEST_WORKSPACE = "/tmp/odin-workspace"
_TASKIT_URL_PLACEHOLDER = "__ODIN_FORKD_TASKIT_URL__"


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."

_CHROME_PREFLIGHT_SCRIPT = r"""
CHROME_BIN="$(command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null || command -v google-chrome 2>/dev/null || true)"
if [ -z "$CHROME_BIN" ]; then
  cat >&2 <<'ODIN_FORKD_CHROME_MISSING'
forkd chrome preflight failed: chrome-devtools MCP is enabled, but the selected forkd guest snapshot/rootfs does not contain Chromium.

Chromium must be provisioned once into the reusable forkd rootfs/snapshot. Installing Chromium inside every disposable sandbox is intentionally disabled because it is slow, non-persistent, and can exhaust the guest disk.
ODIN_FORKD_CHROME_MISSING
  exit 127
fi
export CHROME_PATH="$CHROME_BIN"
export PUPPETEER_EXECUTABLE_PATH="$CHROME_BIN"
"""

_TASKIT_MCP_SHIM = r"""#!/usr/bin/env python3
import json, os, re, sys, urllib.request, urllib.error, uuid

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def url(path):
    base = os.environ.get("TASKIT_URL", "http://localhost:9100").rstrip("/")
    task = os.environ.get("TASKIT_TASK_ID", "")
    return f"{base}/tasks/{task}/{path.lstrip('/')}"

def headers(extra=None):
    h = {"Content-Type": "application/json"}
    tok = os.environ.get("TASKIT_AUTH_TOKEN", "")
    if tok:
        h["Authorization"] = "Bearer " + tok
    if extra:
        h.update(extra)
    return h

def post_json(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url(path), data=data, headers=headers(), method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}

def upload(paths):
    out = []
    for p in paths or []:
        if not os.path.isfile(p):
            raise FileNotFoundError(p)
        boundary = "----odin" + uuid.uuid4().hex
        name = os.path.basename(p)
        body = []
        body.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode())
        body.append(open(p, "rb").read())
        body.append(f"\r\n--{boundary}--\r\n".encode())
        data = b"".join(body)
        req = urllib.request.Request(url("screenshots/"), data=data, headers=headers({"Content-Type": "multipart/form-data; boundary=" + boundary}), method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            parsed = json.loads(resp.read().decode() or "[]")
            out.extend(parsed if isinstance(parsed, list) else [parsed])
    return out

def listify(v):
    if v is None or v == "": return []
    if isinstance(v, list): return [str(x) for x in v]
    if isinstance(v, str):
        try:
            j=json.loads(v)
            if isinstance(j, list): return [str(x) for x in j]
        except Exception: pass
        return [v]
    return [str(v)]

def tool_call(name, args):
    if name == "taskit_add_comment":
        ctype = str(args.get("comment_type") or "status_update")
        content = str(args.get("content") or "")
        if ctype == "question":
            res = post_json("question/", {"author_email":os.environ.get("TASKIT_AUTHOR_EMAIL", "agent@odin.agent"), "author_label":os.environ.get("TASKIT_AUTHOR_LABEL", ""), "content": content})
            return {"comment_id": res.get("id"), "reply": res.get("reply")}
        if ctype == "proof":
            shots = listify(args.get("screenshot_paths"))
            if not shots:
                shots = [m for m in re.findall(r"(/(?:tmp|var)\S+\.(?:png|jpg|jpeg|webp))", content) if os.path.isfile(m)]
            uploaded = upload(shots) if shots else []
            files = listify(args.get("file_paths"))
            screenshot_urls = [x.get("url") for x in uploaded if x.get("url")]
            proof_attachment = {"type":"proof", "summary":content}
            if files: proof_attachment["files"] = files
            if screenshot_urls: proof_attachment["screenshots"] = screenshot_urls
            payload = {"author_email":os.environ.get("TASKIT_AUTHOR_EMAIL", "agent@odin.agent"), "author_label":os.environ.get("TASKIT_AUTHOR_LABEL", ""), "comment_type":"proof", "content":"Proof: " + content, "attachments":[proof_attachment]}
            if uploaded:
                payload["attachment_ids"] = [x.get("id") for x in uploaded if x.get("id") is not None]
            res = post_json("comments/", payload)
            return {"comment_id": res.get("id"), "screenshots_attached": len(uploaded)}
        res = post_json("comments/", {"author_email":os.environ.get("TASKIT_AUTHOR_EMAIL", "agent@odin.agent"), "author_label":os.environ.get("TASKIT_AUTHOR_LABEL", ""), "comment_type": ctype, "content": content})
        return {"comment_id": res.get("id")}
    if name == "taskit_add_attachment":
        content = str(args.get("content") or "")
        files = listify(args.get("file_paths"))
        proof_attachment = {"type":"proof", "summary":content}
        if files: proof_attachment["files"] = files
        res = post_json("comments/", {"author_email":os.environ.get("TASKIT_AUTHOR_EMAIL", "agent@odin.agent"), "author_label":os.environ.get("TASKIT_AUTHOR_LABEL", ""), "comment_type":"proof", "content":"Proof: " + content, "attachments":[proof_attachment]})
        return {"comment_id": res.get("id")}
    return {"error": "unknown tool " + name}

tools = [{"name":"taskit_add_comment","description":"Post TaskIt status/proof comments, optionally uploading screenshots.","inputSchema":{"type":"object","properties":{"content":{"type":"string"},"comment_type":{"type":"string"},"file_paths":{},"screenshot_paths":{},"metadata":{}},"required":["content"]}},{"name":"taskit_add_attachment","description":"Attach proof/file metadata to TaskIt.","inputSchema":{"type":"object","properties":{"content":{"type":"string"},"file_paths":{},"attachment_type":{}},"required":["content"]}}]
for line in sys.stdin:
    try:
        msg=json.loads(line)
        mid=msg.get("id")
        method=msg.get("method")
        if method == "initialize": send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"taskit","version":"odin-forkd"}}})
        elif method == "notifications/initialized": pass
        elif method == "tools/list": send({"jsonrpc":"2.0","id":mid,"result":{"tools":tools}})
        elif method == "tools/call":
            p=msg.get("params") or {}; result=tool_call(p.get("name"), p.get("arguments") or {})
            send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":json.dumps(result)}],"isError": bool(result.get("error"))}})
        elif mid is not None: send({"jsonrpc":"2.0","id":mid,"result":{}})
    except Exception as e:
        try: send({"jsonrpc":"2.0","id":msg.get("id"),"error":{"code":-32000,"message":str(e)}})
        except Exception: pass
"""


class ForkdHarness(BaseHarness):
    """Run an existing CLI harness inside a forkd microVM."""

    def __init__(self, name: str, inner: BaseHarness, config: AgentConfig):
        super().__init__(config)
        self.harness_name = name
        self.inner = inner

    @property
    def name(self) -> str:
        return f"{self.inner.name} (forkd)"

    async def execute(self, prompt: str, context: dict) -> TaskResult:
        start = time.monotonic()
        try:
            prompt = self._prepare_prompt(prompt)
            output = await asyncio.to_thread(self._execute_sync, prompt, context)
            stdout_text = extract_text_from_stream(output)
            duration = (time.monotonic() - start) * 1000
            metadata = {"sandbox": "forkd"}
            status_obj = None
            if context.get("validate_status", True):
                status_obj = validate_odin_status_full(
                    stdout_text,
                    worktree_path=context.get("working_dir"),
                )
                success, error = status_obj.as_legacy_tuple()
                if not success and self.harness_name == "glm":
                    synthetic_status = self._glm_synthetic_status(output)
                    if synthetic_status:
                        output = self._append_json_text_event(output, synthetic_status)
                        stdout_text = f"{stdout_text.rstrip()}\n\n{synthetic_status}".lstrip()
                        metadata["glm_status_synthesized"] = True
                        status_obj = validate_odin_status_full(
                            stdout_text,
                            worktree_path=context.get("working_dir"),
                        )
                        success, error = status_obj.as_legacy_tuple()
                    if not success:
                        error = self._glm_status_error(error, output, stdout_text)
            else:
                success, error = True, None
            if status_obj is not None and status_obj.raw_block is not None:
                metadata["malformed_status"] = {
                    "raw_block": status_obj.raw_block,
                    "inferred": status_obj.inferred,
                    "inference_reason": status_obj.inference_reason,
                }
            self._write_trace_files(context, raw_output=output, extracted_output=stdout_text)
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
                metadata={"sandbox": "forkd"},
            )

    async def execute_streaming(self, prompt: str, context: dict) -> AsyncIterator[str]:
        result = await self.execute(prompt, context)
        if result.output:
            yield result.output

    def build_execute_command(self, prompt: str, context: dict) -> list[str] | None:
        # Force orchestrator down harness.execute(); tmux cannot wrap a VM flow.
        return None

    @property
    def supports_system_prompt_flag(self) -> bool:
        return bool(getattr(self.inner, "supports_system_prompt_flag", False))

    def _prepare_prompt(self, prompt: str) -> str:
        if self.harness_name != "glm":
            return prompt
        return (
            f"{prompt.rstrip()}\n\n"
            "GLM FORKD COMPLETION REQUIREMENT:\n"
            "After your final TaskIt proof tool call returns, output exactly the ODIN-STATUS block. "
            "Keep it under 40 words. Do not call another tool after the block."
        )

    @staticmethod
    def _glm_status_error(base_error: str | None, raw_output: str, extracted_output: str) -> str:
        details = ForkdHarness._glm_trace_diagnostics(raw_output, extracted_output)
        if not details:
            return base_error or "Agent did not emit an ODIN-STATUS block."
        return f"{base_error or 'Agent did not emit an ODIN-STATUS block.'}\n\nGLM raw output tail:\n{details}"

    @staticmethod
    def _append_json_text_event(raw_output: str, text: str) -> str:
        prefix = raw_output if raw_output.endswith("\n") or not raw_output else raw_output + "\n"
        return prefix + json.dumps({"type": "text", "text": text}) + "\n"

    @staticmethod
    def _glm_synthetic_status(raw_output: str) -> str | None:
        proof_summary = ForkdHarness._glm_successful_proof_summary(raw_output)
        if not proof_summary:
            return None
        return (
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            f"{proof_summary}"
        )

    @staticmethod
    def _glm_successful_proof_summary(raw_output: str) -> str | None:
        saw_proof_call = False
        saw_comment_id = False
        summary = "GLM submitted TaskIt proof successfully; forkd synthesized the missing final status block."

        for line in raw_output.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                obj = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue

            proof_text = ForkdHarness._find_proof_text(obj)
            if proof_text is not None:
                saw_proof_call = True
                if proof_text.strip():
                    summary = _clip(" ".join(proof_text.split()), 220)
            if ForkdHarness._contains_key(obj, "comment_id"):
                saw_comment_id = True

        if saw_proof_call and saw_comment_id:
            return summary
        return None

    @staticmethod
    def _find_proof_text(value: object) -> str | None:
        if isinstance(value, dict):
            if value.get("comment_type") == "proof":
                content = value.get("content") or value.get("summary")
                return str(content or "")
            if value.get("type") == "proof":
                summary = value.get("summary") or value.get("content")
                return str(summary or "")
            for nested in value.values():
                found = ForkdHarness._find_proof_text(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = ForkdHarness._find_proof_text(nested)
                if found is not None:
                    return found
        elif isinstance(value, str):
            compact = value.replace(" ", "")
            if '"comment_type":"proof"' in compact or '"type":"proof"' in compact:
                try:
                    parsed = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return ""
                return ForkdHarness._find_proof_text(parsed) or ""
        return None

    @staticmethod
    def _contains_key(value: object, key: str) -> bool:
        if isinstance(value, dict):
            if key in value:
                return True
            return any(ForkdHarness._contains_key(nested, key) for nested in value.values())
        if isinstance(value, list):
            return any(ForkdHarness._contains_key(nested, key) for nested in value)
        if isinstance(value, str) and key in value:
            try:
                return ForkdHarness._contains_key(json.loads(value), key)
            except (json.JSONDecodeError, TypeError):
                return True
        return False

    @staticmethod
    def _glm_trace_diagnostics(raw_output: str, extracted_output: str, limit: int = 1600) -> str:
        last_text = ""
        last_step: dict | None = None
        for line in raw_output.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                obj = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            text = extract_text_from_line(stripped)
            if text.strip():
                last_text = text.strip()
            if obj.get("type") == "step_finish":
                part = obj.get("part")
                if isinstance(part, dict):
                    last_step = part

        parts: list[str] = []
        if last_text:
            parts.append("last assistant text:\n" + _tail(last_text, 900))
        elif extracted_output.strip():
            parts.append("extracted output tail:\n" + _tail(extracted_output.strip(), 900))
        if last_step:
            tokens = last_step.get("tokens") if isinstance(last_step.get("tokens"), dict) else {}
            token_bits = []
            for key in ("input", "output", "reasoning", "total"):
                if key in tokens:
                    token_bits.append(f"{key}={tokens[key]}")
            reason = last_step.get("reason") or "unknown"
            suffix = f", tokens: {', '.join(token_bits)}" if token_bits else ""
            parts.append(f"last step_finish: reason={reason}{suffix}")
        if not parts and raw_output.strip():
            parts.append("raw tail:\n" + _tail(raw_output.strip(), 1200))
        return _tail("\n\n".join(parts), limit)

    def build_interactive_command(self, system_prompt_file: str, context: dict) -> list[str] | None:
        # Interactive planning (odin plan) runs on the host — it decomposes
        # tasks, doesn't modify code. Only task execution goes through the VM.
        return self.inner.build_interactive_command(system_prompt_file, context)

    def _extract_output_path_mappings(self, cmd: list[str], context: dict | None = None) -> list[tuple[Path, str]]:
        """Map host output paths mentioned in prompts to sandbox-local paths.

        Planning prompts ask agents to write `.odin/plans/*.json` under the
        host project root. That absolute host path must not exist inside the VM,
        so we rewrite it to a sandbox path and copy the produced file back.
        """
        text = "\n".join(cmd)
        context = context or {}
        candidates = list(context.get("forkd_mapped_output_files") or [])
        candidates.extend(re.findall(r"/[^`'\"\s]+\.json", text))
        mappings: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for raw in candidates:
            host = Path(str(raw).rstrip(".,);]")).expanduser()
            if ".odin/plans" not in host.as_posix():
                continue
            if host in seen:
                continue
            seen.add(host)
            guest = f"/tmp/odin-forkd-mapped/{len(mappings)}/{host.name}"
            mappings.append((host, guest))
        return mappings

    def _rewrite_mapped_paths(self, cmd: list[str], mappings: list[tuple[Path, str]]) -> list[str]:
        rewritten = list(cmd)
        for host, guest in mappings:
            rewritten = [part.replace(str(host), guest) for part in rewritten]
        return rewritten

    def _inject_sandbox_bypass_flags(self, cmd: list[str]) -> list[str]:
        flags = _SANDBOX_BYPASS_FLAGS.get(self.harness_name)
        if not flags:
            return cmd
        existing = set(cmd)
        return cmd + [f for f in flags if f not in existing]

    def _restore_mapped_outputs(self, assets: Path, mappings: list[tuple[Path, str]]) -> None:
        for idx, (host, _guest) in enumerate(mappings):
            uploaded = assets / f"mapped-output-{idx}"
            if not uploaded.exists():
                continue
            host.parent.mkdir(parents=True, exist_ok=True)
            host.write_bytes(uploaded.read_bytes())

    def _write_trace_files(self, context: dict, *, raw_output: str, extracted_output: str) -> None:
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

    async def is_available(self) -> bool:
        return bool(self._forkd_cmd()) and self.inner.build_execute_command("test", {}) is not None

    def _execute_sync(self, prompt: str, context: dict) -> str:
        cmd = self.inner.build_execute_command(prompt, context)
        if not cmd:
            raise RuntimeError(f"{self.harness_name} does not expose a CLI command for forkd")

        cmd = self._inject_sandbox_bypass_flags(cmd)
        path_mappings = self._extract_output_path_mappings(cmd, context)
        cmd = self._rewrite_mapped_paths(cmd, path_mappings)

        working_dir = Path(context.get("working_dir") or os.getcwd()).resolve()
        # The host worktree path is not present inside the microVM. Keep the
        # agent contract stable by presenting the staged workspace at a fixed guest path.
        cmd = [part.replace(str(working_dir), _GUEST_WORKSPACE) for part in cmd]
        timeout = context.get("timeout_seconds", 300)
        if not timeout or timeout <= 0:
            timeout = 300

        forkd = self._forkd_cmd()
        kernel = self._kernel_path()
        scripts_dir = self.config.forkd_scripts_dir
        if not forkd:
            raise RuntimeError("forkd_bin is not configured and forkd was not found on PATH")
        if not kernel:
            raise RuntimeError("forkd_kernel is required when run_in_forkd is true")

        with tempfile.TemporaryDirectory(prefix="odin-forkd-") as td:
            tmp = Path(td)
            assets = tmp / "assets"
            assets.mkdir()
            cli_tar = assets / "cli.tgz"
            creds_tar = assets / "creds.tgz"
            workspace_tar = assets / "workspace.tgz"
            result_tar = assets / "workspace-result.tgz"
            mcp_config_asset = assets / "mcp-config.json"

            cli_name = self._cli_name(cmd[0])
            self._make_cli_tar(cli_name, cli_tar)
            self._make_creds_tar(creds_tar)

            mode = self.config.forkd_mode.lower()
            require_chrome = bool(context.get("forkd_require_chrome") or context.get("forkd_bootstrap_chrome"))
            # Per-child netns is the normal controller path: each sandbox gets
            # its own forkd-child-N namespace and tap, so parallel tasks do not
            # collide on the host's shared forkd-tap0. Shared tap is only used
            # when explicitly configured as a fallback/debug mode.
            effective_per_child_netns = bool(self.config.forkd_per_child_netns)
            asset_host = "10.43.0.1" if mode == "controller" and effective_per_child_netns else "10.42.0.1"
            # asset_host is the address the guest reaches. Do not bind host-side
            # helper servers to it: in controller/per-child-netns mode 10.43.0.1
            # is only meaningful to forkd networking, not necessarily assigned in
            # the host namespace. Binding to 0.0.0.0 avoids Errno 99 while still
            # advertising the forkd gateway IP to the guest.
            asset_bind_host = "0.0.0.0"
            taskit_proxy = None
            guest_taskit_url = None
            if context.get("forkd_taskit_base_url"):
                taskit_proxy = _TaskItProxy(str(context["forkd_taskit_base_url"]), bind_host=asset_bind_host)
                taskit_proxy.start()
                guest_taskit_url = f"http://{asset_host}:{taskit_proxy.port}"
                cmd = [part.replace(_TASKIT_URL_PLACEHOLDER, guest_taskit_url) for part in cmd]

            mcp_config_guest_path = None
            if context.get("mcp_config"):
                mcp_config_host = Path(str(context["mcp_config"])).expanduser().resolve()
                if mcp_config_host.is_file():
                    data = mcp_config_host.read_bytes()
                    if guest_taskit_url:
                        data = data.replace(_TASKIT_URL_PLACEHOLDER.encode(), guest_taskit_url.encode())
                    mcp_config_asset.write_bytes(data)
                    mcp_config_guest_path = "/tmp/odin-forkd-mcp/mcp-config.json"
                    cmd = [part.replace(str(mcp_config_host), mcp_config_guest_path) for part in cmd]

            include_paths = [Path(p).expanduser().resolve() for p in context.get("forkd_include_paths", [])]
            self._make_workspace_tar(
                working_dir, workspace_tar,
                guest_taskit_url=guest_taskit_url,
                include_paths=include_paths,
            )

            server = _AssetServer(
                assets,
                bind_host=asset_bind_host,
                trace_file=context.get("trace_file"),
                output_file=context.get("output_file"),
            )
            server.start()
            try:
                sandbox_script = self._sandbox_script(
                    cli_name=cli_name,
                    cli_cmd=cmd,
                    port=server.port,
                    timeout=int(timeout),
                    asset_host=asset_host,
                    path_mappings=path_mappings,
                    mcp_config_guest_path=mcp_config_guest_path,
                    require_chrome=require_chrome,
                    trace_upload=bool(context.get("trace_file")),
                )
                script_path = assets / "run.sh"
                script_path.write_text(sandbox_script)
                require_chrome = bool(context.get("forkd_require_chrome") or context.get("forkd_bootstrap_chrome"))
                if mode == "controller" and self._controller_snapshot_has_memory(
                    self._controller_snapshot_tag_for_memory(
                        self._controller_base_tag(require_chrome=require_chrome)
                    )
                ):
                    # Existing compatible controller snapshots are enough for runtime staging.
                    # Browser-required runs use a separate browser tag, so stale non-browser
                    # snapshots are not reused accidentally.
                    rootfs = Path("/dev/null")
                else:
                    rootfs = self._ensure_rootfs(forkd, kernel, scripts_dir)
                script_command = (
                    _download_command(server.port, "run.sh", "/tmp/odin-forkd-run.sh", host=asset_host)
                    + " && sh /tmp/odin-forkd-run.sh"
                )
                if mode == "controller":
                    proc = self._run_guest_script_controller(
                        forkd=forkd,
                        kernel=kernel,
                        scripts_dir=scripts_dir,
                        rootfs=rootfs,
                        script_command=script_command,
                        timeout=int(timeout),
                        require_chrome=require_chrome,
                        per_child_netns=effective_per_child_netns,
                    )
                elif mode == "cli":
                    proc = self._run_guest_script(
                        forkd=forkd,
                        kernel=kernel,
                        scripts_dir=scripts_dir,
                        rootfs=rootfs,
                        script_command=script_command,
                        timeout=int(timeout),
                    )
                else:
                    raise RuntimeError(f"unsupported forkd_mode: {self.config.forkd_mode!r}")
                stdout = (assets / "stdout.txt").read_text(errors="replace") if (assets / "stdout.txt").exists() else ""
                stderr = (assets / "stderr.txt").read_text(errors="replace") if (assets / "stderr.txt").exists() else ""
                if not result_tar.exists():
                    detail = "\n".join(part for part in (stdout, stderr, proc.stdout, proc.stderr) if part)
                    raise RuntimeError(detail or "forkd run did not return workspace-result.tgz")
                self._restore_workspace_tar(working_dir, result_tar)
                self._restore_mapped_outputs(assets, path_mappings)
                combined = stdout + ("\n" if stdout and stderr else "") + stderr
                if proc.returncode != 0:
                    detail = "\n".join(part for part in (combined, proc.stdout, proc.stderr) if part and part.strip()).strip()
                    if not detail:
                        detail = (
                            f"sandbox command exited with {proc.returncode} before the agent emitted stdout/stderr. "
                            "This usually means the forkd runner or CLI failed before producing a JSON trace."
                        )
                    if "-------ODIN-STATUS-------" not in detail:
                        raise RuntimeError(detail)
                return combined
            finally:
                server.stop()
                if taskit_proxy:
                    taskit_proxy.stop()

    def _forkd_cmd(self) -> list[str] | None:
        binary = self.config.forkd_bin or shutil.which("forkd")
        if not binary:
            return None
        cmd = [binary]
        if self.config.forkd_use_sudo:
            cmd = ["sudo", "-n", *cmd]
        return cmd

    def _kernel_path(self) -> Path | None:
        if self.config.forkd_kernel:
            return Path(self.config.forkd_kernel).expanduser().resolve()
        env_kernel = os.environ.get("FORKD_KERNEL")
        return Path(env_kernel).expanduser().resolve() if env_kernel else None

    def _run_forkd_command(
        self,
        forkd: list[str],
        args: list[str],
        *,
        scripts_dir: str | None,
        timeout: int,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if scripts_dir:
            env["FORKD_SCRIPTS_DIR"] = scripts_dir
        env["PATH"] = f"{Path.home() / '.local/bin'}:{env.get('PATH', '')}"
        proc = subprocess.run(
            [*forkd, *args],
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            detail = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
            raise RuntimeError(detail or f"forkd exited with {proc.returncode}")
        return proc

    def _run_guest_script(
        self,
        *,
        forkd: list[str],
        kernel: Path,
        scripts_dir: str | None,
        rootfs: Path,
        script_command: str,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        tag = f"odin-{self.harness_name}-{os.getpid()}-{int(time.time() * 1000)}"
        fork_proc: subprocess.Popen[str] | None = None
        try:
            self._run_forkd_command(
                forkd,
                [
                    "snapshot",
                    "--tag", tag,
                    "--kernel", str(kernel),
                    "--rootfs", str(rootfs),
                    "--rw",
                    "--tap", self.config.forkd_tap,
                    "--boot-wait-secs", "10",
                    "--mem-size-mib", str(self.config.forkd_mem_size_mib),
                ],
                scripts_dir=scripts_dir,
                timeout=900,
            )
            fork_proc = self._start_fork_process(forkd, tag, scripts_dir, timeout + 120)
            self._wait_for_guest_agent(forkd, scripts_dir, timeout=30)
            return self._run_forkd_command(
                forkd,
                [
                    "exec",
                    "--target", "10.42.0.2:8888",
                    "--timeout-secs", str(timeout),
                    "--",
                    "sh", "-lc", script_command,
                ],
                scripts_dir=scripts_dir,
                timeout=timeout + 60,
                check=False,
            )
        finally:
            if fork_proc and fork_proc.poll() is None:
                fork_proc.terminate()
                try:
                    fork_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    fork_proc.kill()
                    fork_proc.wait(timeout=10)
            self._run_forkd_command(
                forkd,
                ["rmi", tag],
                scripts_dir=scripts_dir,
                timeout=60,
                check=False,
            )

    def _run_guest_script_controller(
        self,
        *,
        forkd: list[str],
        kernel: Path,
        scripts_dir: str | None,
        rootfs: Path,
        script_command: str,
        timeout: int,
        require_chrome: bool = False,
        per_child_netns: bool | None = None,
    ) -> subprocess.CompletedProcess[str]:
        tag = self._ensure_controller_snapshot(forkd, kernel, scripts_dir, rootfs, require_chrome=require_chrome)
        use_per_child_netns = self.config.forkd_per_child_netns if per_child_netns is None else per_child_netns
        if use_per_child_netns:
            return self._run_guest_script_controller_unlocked(tag, script_command, timeout, use_per_child_netns=True)

        lock_name = _slug(f"{self.config.forkd_tap}-{tag}")
        lock = FileLock(f"/tmp/odin-forkd-shared-tap-{lock_name}.lock", timeout=timeout + 180)
        with lock:
            # Shared-tap Firecracker restores are exclusive. If a previous Odin
            # process died after creating a sandbox, the controller can keep the
            # VM alive and the next restore fails with forkd-tap0 Resource busy.
            self._cleanup_shared_tap_sandboxes(tag)
            try:
                return self._run_guest_script_controller_unlocked(tag, script_command, timeout, use_per_child_netns=False)
            except RuntimeError as exc:
                if self._is_tap_busy_error(str(exc)):
                    self._cleanup_shared_tap_sandboxes(tag)
                    return self._run_guest_script_controller_unlocked(tag, script_command, timeout, use_per_child_netns=False)
                raise

    def _run_guest_script_controller_unlocked(
        self,
        tag: str,
        script_command: str,
        timeout: int,
        *,
        use_per_child_netns: bool,
    ) -> subprocess.CompletedProcess[str]:
        sandbox_id: str | None = None
        try:
            sandboxes = self._controller_request(
                "POST",
                "/v1/sandboxes",
                json={
                    "snapshot_tag": tag,
                    "n": 1,
                    "per_child_netns": use_per_child_netns,
                    "live_fork": False,
                },
                timeout=120,
            )
            if not sandboxes:
                raise RuntimeError("forkd-controller returned no sandbox")
            sandbox_id = sandboxes[0]["id"]
            self._wait_for_controller_agent(sandbox_id, timeout=30)
            self._assert_controller_guest_memory(sandbox_id)
            data = self._controller_request(
                "POST",
                f"/v1/sandboxes/{sandbox_id}/exec",
                json={"args": ["sh", "-lc", script_command], "timeout_secs": timeout},
                timeout=timeout + 60,
            )
            return subprocess.CompletedProcess(
                ["forkd-controller", "exec", sandbox_id],
                int(data.get("exit_code", 1)),
                data.get("stdout", ""),
                data.get("stderr", ""),
            )
        finally:
            if sandbox_id:
                self._controller_request("DELETE", f"/v1/sandboxes/{sandbox_id}", timeout=30, check=False)

    def _cleanup_shared_tap_sandboxes(self, tag: str) -> None:
        sandboxes = self._controller_request("GET", "/v1/sandboxes", timeout=30, check=False) or []
        for sandbox in sandboxes:
            if sandbox.get("snapshot_tag") != tag:
                continue
            if sandbox.get("netns") not in (None, ""):
                continue
            sandbox_id = sandbox.get("id")
            if sandbox_id:
                self._controller_request("DELETE", f"/v1/sandboxes/{sandbox_id}", timeout=30, check=False)

    @staticmethod
    def _is_tap_busy_error(message: str) -> bool:
        lowered = message.lower()
        return "resource busy" in lowered and ("forkd-tap" in lowered or "tun/tap" in lowered or "tap device" in lowered)

    def _ensure_controller_snapshot(
        self,
        forkd: list[str],
        kernel: Path,
        scripts_dir: str | None,
        rootfs: Path,
        *,
        require_chrome: bool = False,
    ) -> str:
        base_tag = self._controller_base_tag(require_chrome=require_chrome)
        tag = self._controller_snapshot_tag_for_memory(base_tag)
        lock = FileLock(f"/tmp/odin-forkd-snapshot-{tag}.lock", timeout=900)
        with lock:
            if self._controller_snapshot_has_memory(tag):
                return tag
            self._build_controller_snapshot_with_cli(forkd, kernel, scripts_dir, rootfs, tag)
            if not self._controller_snapshot_has_memory(tag):
                raise RuntimeError(
                    f"forkd snapshot {tag!r} was created but does not expose "
                    f"{self.config.forkd_mem_size_mib} MiB to the guest"
                )
        return tag

    def _controller_base_tag(self, *, require_chrome: bool = False) -> str:
        base_tag = self.config.forkd_snapshot_tag or f"odin-{_slug(self.config.forkd_image)}"
        if require_chrome and not re.search(r"(?:chrome|chromium|browser)", base_tag, re.IGNORECASE):
            return f"{base_tag}-browser"
        return base_tag

    def _controller_snapshot_tag_for_memory(self, base_tag: str) -> str:
        if self._controller_snapshot_has_memory(base_tag):
            return base_tag
        fallback = self._find_compatible_controller_snapshot(base_tag)
        if fallback:
            return fallback
        snapshots = self._controller_request("GET", "/v1/snapshots", timeout=30)
        if any(s.get("tag") == base_tag for s in snapshots):
            return f"{base_tag}-m{self.config.forkd_mem_size_mib}"
        return base_tag

    def _find_compatible_controller_snapshot(self, base_tag: str) -> str | None:
        snapshots = self._controller_request("GET", "/v1/snapshots", timeout=30)
        family = self._snapshot_family_prefix(base_tag)
        candidates: list[tuple[int, str]] = []
        for snap in snapshots or []:
            tag = snap.get("tag")
            if not isinstance(tag, str):
                continue
            if self._snapshot_family_prefix(tag) != family:
                continue
            mem_mib = self._controller_snapshot_memory_mib(tag)
            if mem_mib is None or mem_mib < int(self.config.forkd_mem_size_mib):
                continue
            candidates.append((mem_mib, tag))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1].endswith('-cli')), reverse=True)
        return candidates[0][1]

    @staticmethod
    def _snapshot_family_prefix(tag: str) -> str:
        family = re.sub(r"-m\d+$", "", tag)
        family = re.sub(r"-(?:\d+g|\d+gb|\d+)(?:-cli)?$", "", family)
        family = re.sub(r"-cli$", "", family)
        return family

    def _controller_snapshot_memory_mib(self, tag: str) -> int | None:
        info = self._controller_request("GET", f"/v1/snapshots/{tag}/info", timeout=30, check=False)
        if not isinstance(info, dict):
            return None
        logical_bytes = info.get("memory_logical_bytes")
        if not isinstance(logical_bytes, int):
            return None
        return logical_bytes // (1024 * 1024)

    def _controller_snapshot_has_memory(self, tag: str) -> bool:
        mem_mib = self._controller_snapshot_memory_mib(tag)
        if mem_mib is None:
            return False
        return mem_mib >= int(self.config.forkd_mem_size_mib)

    def _build_controller_snapshot_with_cli(
        self,
        forkd: list[str],
        kernel: Path,
        scripts_dir: str | None,
        rootfs: Path,
        tag: str,
    ) -> None:
        # forkd-controller 0.5.2 accepts mem_size_mib but still creates
        # 512 MiB snapshots. Build the parent snapshot with the CLI, writing
        # into the controller's default snapshot root via XDG_DATA_HOME.
        forkd_bin = forkd[-1]
        path = f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"
        env_args = [
            f"PATH={path}",
            "XDG_DATA_HOME=/var/lib",
        ]
        if scripts_dir:
            env_args.append(f"FORKD_SCRIPTS_DIR={scripts_dir}")
        prefix = ["env", *env_args] if os.geteuid() == 0 else ["sudo", "-n", "env", *env_args]
        cmd = [
            *prefix,
            forkd_bin,
            "snapshot",
            "--tag", tag,
            "--kernel", str(kernel),
            "--rootfs", str(rootfs),
            "--rw",
            "--tap", self.config.forkd_tap,
            "--boot-wait-secs", "10",
            "--mem-size-mib", str(self.config.forkd_mem_size_mib),
        ]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=900)
        if proc.returncode != 0:
            detail = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
            if "a password is required" in detail or "password is required" in detail:
                detail += (
                    "\nBuilding a high-memory forkd controller snapshot requires sudo. "
                    "Either run one forkd command with sudo first, or prebuild the snapshot with: "
                    f"sudo env XDG_DATA_HOME=/var/lib FORKD_SCRIPTS_DIR={scripts_dir or ''} "
                    f"{forkd_bin} snapshot --tag {tag} --kernel {kernel} --rootfs {rootfs} "
                    f"--rw --tap {self.config.forkd_tap} --boot-wait-secs 10 "
                    f"--mem-size-mib {self.config.forkd_mem_size_mib}"
                )
            raise RuntimeError(detail or f"forkd snapshot exited with {proc.returncode}")

    def _wait_for_controller_agent(self, sandbox_id: str, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                self._controller_request("POST", f"/v1/sandboxes/{sandbox_id}/ping", json={}, timeout=5)
                return
            except RuntimeError as exc:
                last_error = str(exc)
                time.sleep(0.25)
        raise RuntimeError(last_error or f"forkd sandbox {sandbox_id} agent did not become ready")


    def _assert_controller_guest_memory(self, sandbox_id: str) -> None:
        data = self._controller_request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/exec",
            json={"args": ["sh", "-lc", "grep MemTotal /proc/meminfo"], "timeout_secs": 10},
            timeout=20,
        )
        stdout = str((data or {}).get("stdout", ""))
        match = re.search(r"MemTotal:\s+(\d+)\s+kB", stdout)
        if not match:
            raise RuntimeError(f"could not verify forkd guest memory for sandbox {sandbox_id}: {stdout.strip()}")
        guest_mib = int(match.group(1)) // 1024
        required_mib = int(self.config.forkd_mem_size_mib)
        # MemTotal excludes some reserved kernel/boot overhead, so allow a small
        # gap while still rejecting clearly wrong snapshots like the stale 512 MiB ones.
        min_expected_mib = max(required_mib - 256, int(required_mib * 0.85))
        if guest_mib < min_expected_mib:
            raise RuntimeError(
                f"forkd guest booted with only {guest_mib} MiB but {required_mib} MiB was requested; "
                "selected snapshot is undersized or stale"
            )

    def _controller_request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        timeout: int,
        check: bool = True,
    ):
        try:
            with httpx.Client(base_url=self.config.forkd_controller_url.rstrip("/"), timeout=timeout) as client:
                resp = client.request(method, path, json=json)
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"forkd controller is not reachable at {self.config.forkd_controller_url}; "
                "start forkd-controller serve before using forkd_mode=controller"
            ) from exc
        if check and resp.status_code >= 400:
            raise RuntimeError(f"forkd-controller {method} {path} failed {resp.status_code}: {resp.text}")
        if not resp.content:
            return None
        return resp.json()

    def _start_fork_process(
        self,
        forkd: list[str],
        tag: str,
        scripts_dir: str | None,
        settle_secs: int,
    ) -> subprocess.Popen[str]:
        env = os.environ.copy()
        if scripts_dir:
            env["FORKD_SCRIPTS_DIR"] = scripts_dir
        env["PATH"] = f"{Path.home() / '.local/bin'}:{env.get('PATH', '')}"
        return subprocess.Popen(
            [*forkd, "fork", "--tag", tag, "-n", "1", "--settle-secs", str(settle_secs)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def _wait_for_guest_agent(self, forkd: list[str], scripts_dir: str | None, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            proc = self._run_forkd_command(
                forkd,
                ["ping", "--target", "10.42.0.2:8888"],
                scripts_dir=scripts_dir,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0:
                return
            last_error = proc.stderr or proc.stdout
            time.sleep(0.25)
        raise RuntimeError(last_error or "forkd child agent did not become ready")

    def _ensure_rootfs(self, forkd: list[str], kernel: Path, scripts_dir: str | None) -> Path:
        image_slug = _slug(self.config.forkd_image)
        extras_slug = _slug("-".join(self.config.forkd_extra)) if self.config.forkd_extra else "base"
        rootfs_size_mib = self._rootfs_size_mib()
        rootfs = Path(self.config.forkd_cache_dir).expanduser() / f"{image_slug}-{extras_slug}-s{rootfs_size_mib}.ext4"
        if rootfs.exists():
            return rootfs
        rootfs.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "parent", "build", self.config.forkd_image,
            "--output", str(rootfs),
            "--size-mib", str(rootfs_size_mib),
        ]
        for extra in self.config.forkd_extra:
            args.extend(["--extra", extra])
        try:
            self._run_forkd_command(forkd, args, scripts_dir=scripts_dir, timeout=900)
        except RuntimeError as exc:
            detail = str(exc)
            if any(re.search(r"(?:chrome|chromium|browser)", extra, re.IGNORECASE) for extra in self.config.forkd_extra):
                build_cmd = " ".join(shlex.quote(part) for part in [*forkd, *args])
                raise RuntimeError(
                    "forkd browser snapshot/rootfs is not provisioned yet, and automatic browser rootfs build failed. "
                    "Browser proof requires Chromium to be baked once into the reusable forkd rootfs/snapshot; "
                    "Odin will not install Chromium inside each disposable task sandbox.\n\n"
                    f"Rootfs target: {rootfs}\n"
                    f"Provision command: sudo -E {build_cmd}\n\n"
                    f"Original error:\n{detail}"
                ) from exc
            raise
        return rootfs

    def _rootfs_size_mib(self) -> int:
        configured = int(self.config.forkd_rootfs_size_mib)
        if any(re.search(r"(?:chrome|chromium|browser)", extra, re.IGNORECASE) for extra in self.config.forkd_extra):
            return max(configured, 8192)
        return configured

    def _make_cli_tar(self, cli_name: str, out: Path) -> None:
        specs = _CLI_PACKAGES.get(cli_name)
        if not specs:
            raise RuntimeError(f"forkd CLI staging is not configured for {cli_name!r}")
        npm_global = self._host_home() / ".npm-global"
        with tarfile.open(out, "w:gz") as tf:
            for rel in specs:
                path = npm_global / rel
                if not path.exists():
                    raise RuntimeError(f"required CLI path missing: {path}")
                tf.add(path, arcname=rel)

    def _make_creds_tar(self, out: Path) -> None:
        self._prepare_credentials_for_copy()
        paths = _CREDENTIAL_PATHS.get(self.harness_name, [])
        with tarfile.open(out, "w:gz") as tf:
            added = False
            for rel in paths:
                path = self._host_home() / rel
                if path.exists():
                    tf.add(path, arcname=rel)
                    added = True
            if not added and not self._api_key_env_block():
                raise RuntimeError(f"no credential files found for {self.harness_name}")

    @staticmethod
    def _host_home() -> Path:
        """Return the real host user home, not a sandbox/sudo HOME override."""
        explicit = os.environ.get("ODIN_HOST_HOME")
        if explicit:
            return Path(explicit).expanduser().resolve()

        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            try:
                return Path(pwd.getpwnam(sudo_user).pw_dir).resolve()
            except KeyError:
                pass

        try:
            return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        except Exception:
            return Path.home().resolve()

    def _prepare_credentials_for_copy(self) -> None:
        if self.harness_name == "gemini" and not self.config.api_key:
            self._refresh_gemini_oauth_if_needed()

    def _refresh_gemini_oauth_if_needed(self) -> None:
        """Ensure forkd receives a Gemini OAuth token usable in non-interactive mode."""
        host_home = self._host_home()
        oauth = host_home / ".gemini" / "oauth_creds.json"
        if not oauth.exists():
            raise RuntimeError(
                "Gemini OAuth credentials are missing from the host. Run `gemini` once on the host "
                "in an interactive terminal, then retry the forkd task."
            )
        ttl = self._gemini_oauth_ttl_seconds(oauth)
        if ttl is not None and ttl > 600:
            return

        cli = self.config.cli_command or "gemini"
        cmd = [
            cli,
            "-p",
            "print exactly: ODIN_GEMINI_AUTH_REFRESH_OK",
            "--output-format",
            "stream-json",
            "--yolo",
        ]
        if self.config.default_model:
            cmd.extend(["--model", self.config.default_model])
        env = os.environ.copy()
        env["HOME"] = str(host_home)
        env["GEMINI_CLI_HOME"] = str(host_home)
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=90, env=env)
        refreshed_ttl = self._gemini_oauth_ttl_seconds(oauth)
        if proc.returncode != 0 or refreshed_ttl is None or refreshed_ttl <= 600:
            detail = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
            raise RuntimeError(
                "Gemini OAuth credentials are missing or expiring, and Odin could not refresh them "
                "before copying them into forkd. Run `gemini` once on the host in an interactive "
                "terminal, then retry the forkd task."
                + (f"\n\nGemini refresh output:\n{detail}" if detail else "")
            )

    @staticmethod
    def _gemini_oauth_ttl_seconds(path: Path) -> int | None:
        try:
            data = json.loads(path.read_text())
            expiry = data.get("expiry_date") or data.get("expiryDate")
            return int((int(expiry) - int(time.time() * 1000)) / 1000)
        except Exception:
            return None

    def _is_workspace_excluded(self, rel_s: str) -> bool:
        rel_s = rel_s[2:] if rel_s.startswith("./") else rel_s
        rel_s = rel_s.strip("/")
        if not rel_s or rel_s == ".":
            return False
        for ex in self.config.forkd_workspace_excludes:
            ex = ex.rstrip("/")
            if rel_s == ex or rel_s.startswith(f"{ex}/"):
                return True
        return False

    def _make_workspace_tar(
        self,
        working_dir: Path,
        out: Path,
        *,
        guest_taskit_url: str | None = None,
        include_paths: list[Path] | None = None,
    ) -> None:
        include_resolved = {p.resolve() for p in (include_paths or [])}
        with tarfile.open(out, "w:gz") as tf:
            for path in working_dir.rglob("*"):
                rel = path.relative_to(working_dir)
                rel_s = rel.as_posix()
                force_include = path.resolve() in include_resolved
                if self._is_workspace_excluded(rel_s) and not force_include:
                    continue
                if path.is_file() and guest_taskit_url:
                    data = path.read_bytes()
                    if _TASKIT_URL_PLACEHOLDER.encode() in data:
                        data = data.replace(_TASKIT_URL_PLACEHOLDER.encode(), guest_taskit_url.encode())
                        info = tf.gettarinfo(str(path), arcname=rel_s)
                        info.size = len(data)
                        import io
                        tf.addfile(info, io.BytesIO(data))
                        continue
                tf.add(path, arcname=rel_s, recursive=False)

    def _restore_workspace_tar(self, working_dir: Path, src: Path) -> None:
        with tarfile.open(src, "r:gz") as tf:
            for member in tf.getmembers():
                rel_s = member.name[2:] if member.name.startswith("./") else member.name
                target = (working_dir / rel_s).resolve()
                if not str(target).startswith(str(working_dir)):
                    raise RuntimeError(f"unsafe path in forkd workspace result: {member.name}")
                if self._is_workspace_excluded(rel_s):
                    continue
                tf.extract(member, working_dir)

    def _sandbox_script(self, *, cli_name: str, cli_cmd: list[str], port: int, timeout: int, asset_host: str, path_mappings: list[tuple[Path, str]], mcp_config_guest_path: str | None = None, require_chrome: bool = False, trace_upload: bool = False) -> str:
        quoted_cmd = " ".join(shlex.quote(part) for part in cli_cmd)
        runner_cmd_json = json.dumps(quoted_cmd)
        trace_upload_url_json = json.dumps(
            f"http://{asset_host}:{port}/__odin_trace/stdout" if trace_upload else ""
        )
        cli_link = _CLI_LINKS[cli_name]
        mapped_setup = "\n".join(
            f"mkdir -p {shlex.quote(str(Path(guest).parent))}" for _host, guest in path_mappings
        )
        mapped_uploads = "\n".join(
            f"if [ -f {shlex.quote(guest)} ]; then {_upload_command(port, guest, f'mapped-output-{idx}', host=asset_host)}; fi"
            for idx, (_host, guest) in enumerate(path_mappings)
        )
        opencode_config_block = ""
        if cli_name == "opencode":
            # Preserve Odin-generated MCP config when present. Only create a
            # minimal OpenCode config for plain runs without MCP.
            opencode_config_block = textwrap.dedent(f"""
            if [ ! -f {shlex.quote(_GUEST_WORKSPACE)}/opencode.json ]; then
              cat > {shlex.quote(_GUEST_WORKSPACE)}/opencode.json << 'OPENCODE_FORKD_CONFIG'
            {{
              "$schema": "https://opencode.ai/config.json"
            }}
            OPENCODE_FORKD_CONFIG
            fi
            """).strip()
        api_key_env_block = self._api_key_env_block()
        gemini_auth_config_block = ""
        if cli_name == "gemini":
            selected_type = "gemini-api-key" if self.config.api_key else "oauth-personal"
            # Pin the sandbox copy explicitly so Gemini does not fall back to
            # interactive auth-selection inside non-interactive forkd runs.
            gemini_auth_config_block = textwrap.dedent(f"""
            mkdir -p "$HOME_DIR/.gemini"
            cat > "$HOME_DIR/.gemini/settings.json" << 'GEMINI_FORKD_SETTINGS'
            {{"security":{{"auth":{{"selectedType":"{selected_type}"}}}}}}
            GEMINI_FORKD_SETTINGS
            """).strip()
        mcp_config_download = ""
        if mcp_config_guest_path:
            mcp_config_download = (
                f"mkdir -p {shlex.quote(str(Path(mcp_config_guest_path).parent))}\n"
                + _download_command(port, "mcp-config.json", mcp_config_guest_path, host=asset_host)
            )
        chrome_preflight_block = _CHROME_PREFLIGHT_SCRIPT if require_chrome else ""
        log_dirs = " ".join(_LOG_PATHS.get(cli_name, []))
        launcher = _CLI_LAUNCHERS.get(cli_name)
        shim_exec = f"exec {shlex.quote(launcher)} \"%s\" \"$@\"" if launcher else 'exec "%s" "$@"'
        return textwrap.dedent(f"""
            set -eu
            RUN_DIR="$(mktemp -d /opt/odin-forkd-run.XXXXXX)"
            WORKSPACE_DIR={shlex.quote(_GUEST_WORKSPACE)}
            HOME_DIR="$RUN_DIR/home"
            rm -rf "$WORKSPACE_DIR" "$HOME_DIR"
            mkdir -p "$RUN_DIR/npm-global" "$WORKSPACE_DIR" "$HOME_DIR"
            {mapped_setup}
            {_download_command(port, "cli.tgz", "/tmp/cli.tgz", host=asset_host)}
            tar -xzf /tmp/cli.tgz -C "$RUN_DIR/npm-global"
            rm -f "$RUN_DIR/npm-global/bin/{cli_name}"
            printf '#!/bin/sh\n{shim_exec}\n' "$RUN_DIR/npm-global/{cli_link}" > "$RUN_DIR/npm-global/bin/{cli_name}"
            chmod +x "$RUN_DIR/npm-global/bin/{cli_name}"
            cat > "$RUN_DIR/npm-global/bin/taskit-mcp" << 'ODIN_TASKIT_MCP'
{_TASKIT_MCP_SHIM}
ODIN_TASKIT_MCP
            chmod +x "$RUN_DIR/npm-global/bin/taskit-mcp"
            {mcp_config_download}
            {_download_command(port, "creds.tgz", "/tmp/creds.tgz", host=asset_host)}
            tar -xzf /tmp/creds.tgz -C "$HOME_DIR"
            {gemini_auth_config_block}
            {_download_command(port, "workspace.tgz", "/tmp/workspace.tgz", host=asset_host)}
            tar -xzf /tmp/workspace.tgz -C "$WORKSPACE_DIR"
            if {"true" if self.config.forkd_init_git else "false"} && command -v git >/dev/null 2>&1; then
              git -C "$WORKSPACE_DIR" init >/dev/null
              git -C "$WORKSPACE_DIR" config user.email odin-forkd@harness.kit
              git -C "$WORKSPACE_DIR" config user.name odin-forkd
              git -C "$WORKSPACE_DIR" add . >/dev/null 2>&1 || true
              git -C "$WORKSPACE_DIR" commit -m 'forkd workspace baseline' >/dev/null 2>&1 || true
            fi
            {opencode_config_block}
            export PATH="$RUN_DIR/npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            export HOME="$HOME_DIR"
            export XDG_CONFIG_HOME="$HOME_DIR/.config"
            export XDG_CACHE_HOME="$HOME_DIR/.cache"
            export GEMINI_CLI_HOME="$HOME_DIR"
            export TERM=xterm-256color
            export COLORTERM=truecolor
            export CI=true
            export GEMINI_CLI_TRUST_WORKSPACE=true
            export CLAUDE_CODE_TRUST_WORKSPACE=true
            export CHROME_PATH="${{CHROME_PATH:-$(command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null || command -v google-chrome 2>/dev/null || true)}}"
            export PUPPETEER_EXECUTABLE_PATH="$CHROME_PATH"
            {api_key_env_block}
            {chrome_preflight_block}
            cd {shlex.quote(_GUEST_WORKSPACE)}
            set +e
            cat > /tmp/odin-forkd-runner.py << 'ODIN_FORKD_RUNNER'
import subprocess, sys, threading, time, urllib.request

cmd = {runner_cmd_json}
timeout = {int(timeout)}
trace_url = {trace_upload_url_json}

def post_chunk(chunk):
    if not trace_url or not chunk:
        return
    try:
        req = urllib.request.Request(
            trace_url,
            data=chunk,
            headers={{"Content-Type": "application/octet-stream"}},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as exc:
        sys.stderr.write("odin forkd trace upload failed: " + str(exc) + "\\n")
        sys.stderr.flush()

def pump(src, out_path, upload):
    with open(out_path, "ab") as out:
        while True:
            chunk = src.readline()
            if not chunk:
                break
            out.write(chunk)
            out.flush()
            if upload:
                post_chunk(chunk)

stop_event = threading.Event()
post_chunk(b"[odin-forkd] agent process started inside sandbox\\n")
proc = subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def heartbeat():
    while not stop_event.wait(30):
        post_chunk(b"[odin-forkd] agent still running inside sandbox\\n")

threads = [
    threading.Thread(target=pump, args=(proc.stdout, "/tmp/odin-forkd-stdout.txt", True), daemon=True),
    threading.Thread(target=pump, args=(proc.stderr, "/tmp/odin-forkd-stderr.txt", False), daemon=True),
    threading.Thread(target=heartbeat, daemon=True),
]
for thread in threads:
    thread.start()
try:
    rc = proc.wait(timeout=timeout)
except subprocess.TimeoutExpired:
    proc.kill()
    rc = 124
finally:
    stop_event.set()
for thread in threads:
    thread.join(timeout=10)
post_chunk(("[odin-forkd] agent process exited rc=%s\\n" % rc).encode())
sys.exit(rc)
ODIN_FORKD_RUNNER
            python3 /tmp/odin-forkd-runner.py >> /tmp/odin-forkd-stdout.txt 2>> /tmp/odin-forkd-stderr.txt
            rc=$?
            if [ "$rc" -ne 0 ]; then
              {{
                echo "--- forkd command diagnostics rc=$rc ---"
                echo "PATH=$PATH"
                echo "HOME=$HOME"
                echo "GEMINI_CLI_HOME=${{GEMINI_CLI_HOME:-}}"
                if [ -d "$HOME/.gemini" ]; then
                  echo "--- gemini auth diagnostics ---"
                  ls -la "$HOME/.gemini" 2>/dev/null || true
                  python3 - <<'GEMINI_AUTH_DIAG' 2>/dev/null || true
import json, os, pathlib, sys
base = pathlib.Path(os.environ.get("GEMINI_CLI_HOME") or os.environ.get("HOME", "")) / ".gemini"
for name in ("settings.json", "oauth_creds.json", "google_accounts.json"):
    file = base / name
    try:
        raw = file.read_text()
        parsed = json.loads(raw)
        safe = dict(exists=True, size=file.stat().st_size, keys=sorted(parsed.keys()) if isinstance(parsed, dict) else [])
        if name == "settings.json":
            safe["selectedType"] = parsed.get("security", dict()).get("auth", dict()).get("selectedType")
        if name == "oauth_creds.json":
            safe["hasAccessToken"] = bool(parsed.get("access_token"))
            safe["hasRefreshToken"] = bool(parsed.get("refresh_token"))
            safe["expiryDate"] = parsed.get("expiry_date") or parsed.get("expiryDate")
            safe["type"] = parsed.get("type")
        print(name + ": " + json.dumps(safe, sort_keys=True), file=sys.stderr)
    except Exception as exc:
        print(name + ": missing_or_invalid: " + str(exc), file=sys.stderr)
GEMINI_AUTH_DIAG
                fi
                free -m 2>/dev/null || true
                echo "--- /proc/meminfo ---"
                head -40 /proc/meminfo 2>/dev/null || true
                echo "--- dmesg tail ---"
                dmesg 2>/dev/null | tail -80 || true
              }} >> /tmp/odin-forkd-stderr.txt 2>&1
            fi
            if [ "$rc" -ne 0 ] && [ ! -s /tmp/odin-forkd-stdout.txt ] && [ ! -s /tmp/odin-forkd-stderr.txt ]; then
              {{
                echo "sandbox command exited with $rc and produced no stdout/stderr"
                echo "PATH=$PATH"
                echo "HOME=$HOME"
                command -v {shlex.quote(cli_name)} || true
                {shlex.quote(cli_name)} --version || true
                for d in "$HOME/.{cli_name}" {log_dirs}; do
                  [ -d "$d" ] || continue
                  echo "--- files under $d ---"
                  find "$d" -maxdepth 4 -type f 2>/dev/null | sort | head -120 || true
                  for f in "$d"/log/* "$d"/logs/* "$d"/*.log; do
                    [ -f "$f" ] || continue
                    echo "--- $f ---"
                    tail -120 "$f" || true
                  done
                done
              }} >> /tmp/odin-forkd-stderr.txt 2>&1
            fi
            set -e
            {mapped_uploads}
            tar -czf /tmp/workspace-result.tgz -C "$WORKSPACE_DIR" .
            {_upload_command(port, "/tmp/workspace-result.tgz", "workspace-result.tgz", host=asset_host)}
            {_upload_command(port, "/tmp/odin-forkd-stdout.txt", "stdout.txt", host=asset_host)}
            {_upload_command(port, "/tmp/odin-forkd-stderr.txt", "stderr.txt", host=asset_host)}
            rm -rf "$RUN_DIR" "$WORKSPACE_DIR" "$HOME_DIR"
            exit $rc
        """).strip()

    def _api_key_env_block(self) -> str:
        env_names = _API_KEY_ENV_VARS.get(self.harness_name)
        api_key = self._resolved_api_key()
        if not env_names or not api_key:
            return ""
        return "\n".join(f"export {env_name}={shlex.quote(api_key)}" for env_name in env_names)

    def _resolved_api_key(self) -> str | None:
        if self.config.api_key:
            return self.config.api_key
        if self.harness_name == "gemini":
            return None
        for env_name in _API_KEY_ENV_VARS.get(self.harness_name, ()):
            value = os.environ.get(env_name)
            if value:
                return value
        return None

    @staticmethod
    def _cli_name(command: str) -> str:
        return Path(command).name


class _UploadHandler(SimpleHTTPRequestHandler):
    append_trace_file: str | None = None
    append_output_file: str | None = None
    append_lock = None

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/__odin_trace/stdout":
            self._append_stdout_trace(body)
            return

        target = (Path(self.directory) / self.path.lstrip("/")).resolve()
        if not str(target).startswith(str(Path(self.directory).resolve())):
            self.send_response(400)
            self.end_headers()
            return
        target.write_bytes(body)
        self.send_response(200)
        self.end_headers()

    def _append_stdout_trace(self, body: bytes) -> None:
        if not self.append_trace_file:
            self.send_response(404)
            self.end_headers()
            return
        text = body.decode("utf-8", errors="replace")
        trace_path = Path(self.append_trace_file)
        output_path = Path(self.append_output_file) if self.append_output_file else None
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.append_lock
        if lock:
            lock.acquire()
        try:
            with trace_path.open("a", encoding="utf-8") as tf:
                tf.write(text)
                tf.flush()
            if output_path:
                extracted = "".join(extract_text_from_line(line) for line in text.splitlines(True))
                if extracted:
                    with output_path.open("a", encoding="utf-8") as of:
                        of.write(extracted)
                        of.flush()
        finally:
            if lock:
                lock.release()
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class _TaskItProxyHandler(BaseHTTPRequestHandler):
    upstream = ""

    def _forward(self) -> None:
        target = self.upstream.rstrip("/") + self.path
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length"}}
        try:
            resp = httpx.request(self.command, target, content=body or None, headers=headers, timeout=120)
            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() in {"content-encoding", "transfer-encoding", "connection"}:
                    continue
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.content)
        except Exception as exc:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(exc).encode())

    def do_GET(self) -> None: self._forward()  # noqa: N802
    def do_POST(self) -> None: self._forward()  # noqa: N802
    def do_PATCH(self) -> None: self._forward()  # noqa: N802
    def do_PUT(self) -> None: self._forward()  # noqa: N802
    def do_DELETE(self) -> None: self._forward()  # noqa: N802

    def log_message(self, format: str, *args: object) -> None:
        return


class _TaskItProxy:
    def __init__(self, upstream: str, bind_host: str):
        self.upstream = upstream
        self.bind_host = bind_host
        self.httpd: ThreadingHTTPServer | None = None
        self.port = 0

    def start(self) -> None:
        handler = type("BoundTaskItProxyHandler", (_TaskItProxyHandler,), {"upstream": self.upstream})
        self.httpd = ThreadingHTTPServer((self.bind_host, 0), handler)
        self.port = int(self.httpd.server_address[1])
        import threading
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class _AssetServer:
    def __init__(self, directory: Path, bind_host: str, trace_file: str | None = None, output_file: str | None = None):
        self.directory = directory
        self.bind_host = bind_host
        self.trace_file = trace_file
        self.output_file = output_file
        self.httpd: ThreadingHTTPServer | None = None
        self.port = 0

    def start(self) -> None:
        import threading
        handler_cls = type(
            "BoundUploadHandler",
            (_UploadHandler,),
            {
                "append_trace_file": self.trace_file,
                "append_output_file": self.output_file,
                "append_lock": threading.Lock(),
            },
        )
        handler = partial(handler_cls, directory=str(self.directory))
        self.httpd = ThreadingHTTPServer((self.bind_host, 0), handler)
        self.port = int(self.httpd.server_address[1])
        self._task = asyncio.get_event_loop() if False else None
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


def _download_command(port: int, name: str, target: str, *, host: str = "10.42.0.1") -> str:
    return (
        "node -e "
        + shlex.quote(
            "const fs=require('fs'),http=require('http');"
            f"const out=fs.createWriteStream('{target}');"
            f"http.get('http://{host}:{port}/{name}', r => "
            "r.pipe(out).on('finish', () => out.close()))"
            ".on('error', e => { console.error(e.message); process.exit(1); });"
        )
    )


def _upload_command(port: int, source: str, name: str, *, host: str = "10.42.0.1") -> str:
    return (
        "node -e "
        + shlex.quote(
            "const fs=require('fs'),http=require('http');"
            f"const data=fs.readFileSync('{source}');"
            "const req=http.request("
            f"'http://{host}:{port}/{name}',"
            "{method:'POST',headers:{'Content-Length':data.length}},"
            "res=>res.resume());"
            "req.on('error', e => { console.error(e.message); process.exit(1); });"
            "req.end(data);"
        )
    )


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value).strip("-")


_CLI_PACKAGES = {
    "claude": ["bin/claude", "lib/node_modules/@anthropic-ai/claude-code"],
    "codex": ["bin/codex", "lib/node_modules/@openai/codex"],
    "gemini": ["bin/gemini", "lib/node_modules/@google/gemini-cli"],
    "opencode": ["bin/opencode", "lib/node_modules/opencode-ai"],
}

_CLI_LINKS = {
    "claude": "lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
    "codex": "lib/node_modules/@openai/codex/bin/codex.js",
    "gemini": "lib/node_modules/@google/gemini-cli/bundle/gemini.js",
    "opencode": "lib/node_modules/opencode-ai/bin/opencode.exe",
}

_CLI_LAUNCHERS = {
    "gemini": "node",
}

_CREDENTIAL_PATHS = {
    "claude": [".claude", ".claude.json"],
    "codex": [".codex/auth.json"],
    "gemini": [".gemini", ".config/google-cloud"],
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

_API_KEY_ENV_VARS = {
    "gemini": ("GEMINI_API_KEY", "GEMINIAPIKEY"),
    "glm": ("ZAI_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
}

_LOG_PATHS = {
    "claude": ["$HOME/.claude", "$HOME/.cache/claude"],
    "codex": ["$HOME/.codex", "$HOME/.cache/codex"],
    "gemini": ["$HOME/.gemini", "$HOME/.config/google-cloud", "$HOME/.cache/gemini"],
    "opencode": ["$HOME/.config/opencode", "$HOME/.local/share/opencode", "$HOME/.local/share/kilo"],
}

_SANDBOX_BYPASS_FLAGS = {
    "claude": ["--dangerously-skip-permissions"],
    "gemini": ["--skip-trust"],
    "glm": ["--dangerously-skip-permissions"],
    "minimax": ["--dangerously-skip-permissions"],
}
