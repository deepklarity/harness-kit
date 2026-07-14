"""Reflection audit module — structured code review by a stronger model.

Provides:
- build_reflection_prompt(): Generates the structured audit prompt
- parse_reflection_report(): Extracts sections from agent output
- reflect_task(): Orchestrates the full reflection flow
"""

import asyncio
import json
import re
import shutil
import tempfile
import time
import logging
from pathlib import Path

import httpx

from odin.harnesses import get_harness
from odin.harnesses.base import extract_text_from_stream
from odin.orchestrator import _truncate_trace

logger = logging.getLogger("odin.reflection")

_COMMENT_CHAR_LIMIT = 2000
# Proof / non-noisy comments are meant to be short pointers to the committed
# per-task proof file `.proof/task-<id>/proof.md` (the round-tripped, uncapped
# source of truth the reviewer reads from the worktree mount). A worker that
# pastes a full suite dump into the comment anyway must not inflate the Opus
# reviewer prompt — cap it.
_PROOF_COMMENT_CHAR_LIMIT = 4000
# Comments before the current-attempt checkpoint are history the rubric tells
# the reviewer to use for context only, never to judge. Keep them, but tightly.
_HISTORY_COMMENT_CHAR_LIMIT = 600
# The whole executor text trace is injected as `execution_output`. The reviewer
# also has the read-only worktree + uncapped per-task `.proof/` files, so the
# inline copy only needs to carry the shape of the run (head) and the
# ODIN-STATUS/summary (tail). Head+tail truncation bounds the prompt without
# losing the verdict-relevant ends. ~12 KB ≈ 3 K tokens.
_EXECUTION_OUTPUT_CHAR_LIMIT = 12000
_TRUNCATION_MARKER_TEMPLATE = "[truncated by pipeline at {limit} chars]"
_NOISY_COMMENT_TYPES = {"status_update", "status", "summary", "reflection", "question", "reply"}
_NOISE_PATTERNS = (
    "DeprecationWarning:",
    "YOLO mode",
    "Loaded cached credentials",
    "Loading extension:",
    "supports tool updates",
    "--trace-deprecation",
    "(node:",
    "Server '",
)


def _resolve_reflection_config_path(working_dir: str | None) -> str | None:
    """Prefer the task's board-local config over the worker's ambient cwd."""
    if not working_dir:
        return None
    config_path = Path(working_dir) / ".odin" / "config.yaml"
    if config_path.exists():
        return str(config_path)
    return None


def _clean_comment_content(content: str) -> str:
    """Strip raw tool-stream noise from non-proof comments."""
    clean_lines = []
    for ln in content.splitlines():
        stripped = ln.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            continue
        if any(noise in stripped for noise in _NOISE_PATTERNS):
            continue
        clean_lines.append(ln)
    return "\n".join(clean_lines).strip()


def _truncate_comment_content(content: str, limit: int) -> str:
    """Cap prompt context and annotate when the pipeline trimmed it."""
    if len(content) <= limit:
        return content
    marker = _TRUNCATION_MARKER_TEMPLATE.format(limit=limit)
    available = max(0, limit - len(marker) - 1)
    return f"{content[:available].rstrip()}\n{marker}"


def _truncate_execution_output(text: str, limit: int) -> str:
    """Head+tail truncate the inline executor trace.

    Keeps the run's opening (plan/first actions) and its tail (ODIN-STATUS +
    summary), dropping the middle — where the reviewer, if it needs detail,
    reads the uncapped per-task `.proof/` files from the worktree instead.
    """
    if len(text) <= limit:
        return text
    marker = f"\n{_TRUNCATION_MARKER_TEMPLATE.format(limit=limit)}\n"
    tail_len = min(2000, limit // 3)
    head_len = max(0, limit - tail_len - len(marker))
    return f"{text[:head_len].rstrip()}{marker}{text[-tail_len:].lstrip()}"


def _format_comment_for_prompt(comment: dict, is_history: bool = False) -> str:
    """Prepare one TaskIt comment for the reviewer prompt.

    ``is_history`` marks comments before the current-attempt checkpoint; they
    are capped harder since the rubric judges only the current attempt.
    """
    ctype = comment.get("comment_type", "status")
    content = comment.get("content", "")
    if ctype in _NOISY_COMMENT_TYPES:
        limit = _COMMENT_CHAR_LIMIT
        content = _clean_comment_content(content)
    else:
        # Proof / other comments: previously uncapped — bound them so a stray
        # full-output paste can't dominate the reviewer prompt.
        limit = _PROOF_COMMENT_CHAR_LIMIT
        content = content.strip()
    if is_history:
        limit = min(limit, _HISTORY_COMMENT_CHAR_LIMIT)
    content = _truncate_comment_content(content, limit)
    if not content:
        return ""
    return f"- [{ctype}] {content}"


def build_reflection_prompt(task_context: dict, custom_prompt: str = "") -> str:
    """Build a structured reflection audit prompt from task context.

    Args:
        task_context: Dict with keys: title, status, agent, model, duration_ms,
            tokens, description, execution_output, comments, dependencies.
            May also carry `selection_reason` (W3.18) — surfaced as a
            [CTX:reviewer-selection] section so the reviewer can audit
            whether the chosen reviewer matches the task's context size.
        custom_prompt: Optional additional focus areas from the requester.

    Returns:
        Complete prompt string for the reviewer agent.
    """
    custom_section = ""
    if custom_prompt:
        custom_section = f"""
## ADDITIONAL FOCUS
{custom_prompt}
"""

    selection_reason = task_context.get("selection_reason") or ""
    reviewer_section = ""
    if selection_reason:
        # W3.18 — show the reviewer why they were picked. Cheap reviewers
        # must not misinterpret "you were chosen because the task is small"
        # as "the work is unimportant"; large reviewers must not assume
        # escalation when the strategy happens to land on them. The
        # section is purely informational; it does NOT bias the verdict.
        reviewer_section = f"""
## [CTX:reviewer-selection] Why you were picked

selection_reason: {selection_reason}
- size_small / size_medium / size_large: board strategy bucketed the task by review context size
- default: no board strategy; legacy single-reviewer default
- board_model_override: board.reflection_model set a specific reviewer
- forced_provider: env-var forced provider mode
- caller_override / manual_default: manual POST /reflect/ endpoint

Treat the verdict independently of this section.
"""

    screenshot_section = ""
    if task_context.get("skip_proof"):
        screenshot_section = """
## [CTX:proof] Proof Status

Proof collection was **explicitly disabled** for this board.
- Do NOT penalize for missing proof or screenshots
- Judge the task on code quality, build results, and task completion only
"""
    else:
        screenshot_paths = task_context.get("screenshot_paths", [])
        if screenshot_paths:
            screenshot_section = "\n## [CTX:screenshots] Proof Screenshots\n\n"
            screenshot_section += (
                "The agent submitted these screenshots as proof of work. "
                "**Read each image file** and verify the screenshots actually show "
                "the feature working correctly. Do NOT assume screenshots prove "
                "correctness just because they exist — visually inspect them for "
                "errors, broken UI, error messages, or missing functionality.\n\n"
            )
            for path in screenshot_paths:
                screenshot_section += f"- {path}\n"

    proof_block = ""
    if not task_context.get("skip_proof"):
        # Per-task namespacing (`.proof/task-<id>/proof.md`) prevents the
        # merge collision the old shared `.proof/proof.md` caused when
        # consecutive tasks landed on one spec branch. Fall back to the
        # legacy path when task_id is absent (defensive — should not happen
        # in production where reflect_task always threads the id through).
        _tid = task_context.get("task_id")
        proof_path = (
            f".proof/task-{_tid}/proof.md" if _tid else ".proof/proof.md"
        )
        proof_dir = proof_path.rsplit("/", 1)[0]
        proof_block = f"""

## CTX — proof artifact (`{proof_path}`)

The worker writes the canonical, complete proof file to `{proof_path}`
in the worktree (read-only worktree mount; `cat`, `grep`, `head` freely).
Raw suite outputs sit beside it as `{proof_dir}/<name>.txt` — full output, no
cap. Read **`{proof_path}` first** — it is the primary, authoritative
source for what the worker claims and where it lives.

For each acceptance criterion, choose complete vs minimal context: read
the full proof file once, then grep the section you need. Do NOT re-run
the verify suites to fill gaps — the worker already attached raw outputs
under `{proof_dir}/`. The proof path carries no truncation or size cap; read
the full file when a criterion needs the full chain of evidence.
"""

    return f"""You are auditing a task executed by an AI agent.
{proof_block}
## CONSTRAINTS
- READ-ONLY mode. Do NOT modify files, make commits, or run destructive commands.
- You MAY read files and grep to verify the agent's work.
- Work efficiently: batch independent file reads/greps into a single step, and do NOT re-read a file or re-run a search you have already done — each extra tool call is a full model round-trip.
- **REPORT FORMAT: ONE fenced JSON block, then optionally a markdown rendering.** Your first non-blank line MUST be a single ```json ... ``` fence containing a JSON object with at minimum a `verdict` key. After the fence you MAY also emit the legacy `### Quality Assessment / ### Verdict` sections as a human-readable rendering of the same JSON — the JSON block is the source of truth, the rendering is for the board UI.
- Be concise. Bullet points only. 1-2 lines per finding. If a section has no findings, write "None."
- **Evaluate only the LATEST execution attempt.** This task may have been attempted by multiple agents or models previously. Comments are separated by "--- CURRENT ATTEMPT ---" when prior attempts exist. Use earlier history for understanding context only — do NOT penalize the current agent for failures, quota issues, or code quality problems from previous agents/models. Your verdict must reflect solely the current agent's work.

## TASK UNDER REVIEW
Title: {task_context.get('title', 'Unknown')}
Status: {task_context.get('status', 'Unknown')}
Agent: {task_context.get('agent', 'Unknown')} ({task_context.get('model', 'Unknown')})
Duration: {task_context.get('duration_ms', 'N/A')}ms | Tokens: {task_context.get('tokens', 'N/A')}

## [CTX:description] Task Description
{task_context.get('description', 'No description provided.')}

## [CTX:execution_result] Execution Output
{task_context.get('execution_output', 'No execution output available.')}

## [CTX:comments] Comments & Proof
{task_context.get('comments', 'No comments.')}
{screenshot_section}
## [CTX:dependencies] Dependent Tasks
{task_context.get('dependencies', 'No dependencies.')}

## [CTX:metadata] Task Metadata
{task_context.get('metadata_summary', 'No metadata.')}
{reviewer_section}
{custom_section}
## YOUR REPORT

Output ONE fenced JSON block as the canonical review, then optionally a
markdown rendering of the same content for human readers. The JSON block
is the source of truth for the parser; the markdown is a convenience
rendering the parser will ignore if both are present.

### Required JSON contract

Your first non-blank line MUST be a fenced JSON block:

```json
{{
  "verdict": "PASS" | "NEEDS_WORK" | "FAIL",
  "summary": "Single-sentence justification, the same one you'd put after ### Verdict.",
  "quality_assessment": "For each requirement: MET/UNMET with one-line reason. Then defects (file:line).",
  "slop_detection": "Specific AI slop found, or 'None.'",
  "improvements": "Bullet list of up to 5 actionable items (what, where, why). 'None.' if clean.",
  "agent_optimization": "Description clarity / model tier / token efficiency / prompt improvement.",
  "quota_failure": "QUOTA_FAILURE: <agent_name> if the current attempt hit a quota/rate limit; otherwise 'None.'.",
  "fix_list": ["targeted", "fix", "items", "for", "rework"]
}}
```

Rules:
- `verdict` MUST be exactly `PASS`, `NEEDS_WORK`, or `FAIL` (uppercase). A
  bare word with no review content (just "PASS" alone, or a verdict
  keyword buried in CLI noise) is treated as a reviewer failure — the
  system will NOT honor it. The JSON object must be the FIRST thing you
  emit, complete and parseable, or your review will not be processed.
  Do NOT skip the structured JSON just because the verdict is obvious;
  emit the JSON with full sections, even if the verdict is PASS with
  nothing else to flag.
- `summary` is the one-sentence justification. It appears on the board
  as the verdict summary. The "Evidence rules" below apply to it.
- `fix_list` is REQUIRED for `NEEDS_WORK`: name the exact artifact or
  change needed so the rework is scoped, not a blind redo. Empty list
  `[]` is fine for `PASS` and `FAIL`.
- `quality_assessment`, `slop_detection`, `improvements`,
  `agent_optimization`, `quota_failure` are strings; default to
  `"None."` when there's nothing to report.
- Do NOT emit anything before the opening ```json fence. No
  preamble, no narration, no permission requests, no conversational
  text. The parser uses the JSON block as the first content and
  anything before it is treated as noise.

### Verdict rubric (applies to `verdict` in the JSON)

Choose from what the evidence PROVES. Each rework round costs a full VM
boot, environment rebuild, and tokens — so FAIL and NEEDS_WORK must be
earned by substance, never spent on process trivia or invented rules.

- PASS — every acceptance criterion is verifiably met and the work is
  correct, safe, and builds/runs. Minor style or proof-formatting nits
  do NOT block a PASS; put them in `improvements`.
- NEEDS_WORK — the work is substantially correct but has a fixable
  gap: a missing or truncated proof artifact, an unmet non-critical
  criterion, or a process slip that does not make the code wrong. Pair
  EVERY NEEDS_WORK with a TARGETED fix list (the `fix_list` field in
  the JSON, or a list of items under "### Actionable Improvements" in
  the rendering — name the exact artifact or change needed) so the
  rework is scoped, not a blind redo.
- FAIL — reserved for work that is WRONG, UNSAFE, or UNVERIFIABLE: it breaks the build or a downstream integration, corrupts state, crashes the orchestrator, contradicts a stated acceptance criterion, or supplies no evidence that the core behavior works. Also use FAIL for quota/resource exhaustion (justification must mention "quota" or "rate limit") so the system can reassign to a different agent.

Evidence rules — apply these BEFORE choosing a verdict:
- CITE OR DROP. Any claimed rule/policy/convention violation MUST quote the exact rule text and name the file it lives in (e.g. CLAUDE.md: "Never run ANY git stash command"). If you cannot quote and locate the rule, it does not exist for this review — you may NOT lower the verdict for it; raise it in `improvements` at most. A cited rule only counts if its stated scope actually covers this situation (a rule scoped to "shared repos / concurrent sessions" is not violated by an action inside the task's own isolated worktree). If a rule violation affects the verdict, include that exact citation in the report text.
- A truncated, malformed, or missing proof artifact is a REQUEST, not a verdict. Name the specific artifact you need and ask for it (NEEDS_WORK at most). It only drives FAIL when it leaves the CORE behavior genuinely unverifiable AND no other evidence (code you can read, build output, comments) establishes correctness.
- UNVERIFIABLE CLAIM → NEEDS_WORK. While reading `.proof/task-<id>/proof.md`, every concrete claim the worker makes ("tests pass", "page renders", "all 87 tests green", "no regressions") must have raw output behind it — a pasted command tail, a render log, a screenshot, a grep result. A claim with no raw output behind it is unverifiable: name the exact claim text in the verdict summary and lower the verdict to NEEDS_WORK with a targeted fix_list item ("paste the pytest tail for claim '<quoted claim>'" or "attach a screenshot for claim '<quoted claim>'"). Prose-only proof is not proof. The rule is output for claims, not volume — a one-line docs task doesn't need a wall of logs, but every claim still needs raw output behind it. PASS only when each claim in proof.md is grounded in raw output the reviewer can read in the proof file.
- Judge the SUBSTANCE of the best available evidence. Tasks may span
  several attempts; earlier attempts' partial numbers or
  contradictions with history are NOT defects of the current work.
- Formatting/completeness of proof comments is NOT a verdict criterion
  when the underlying acceptance criteria are verifiably met; mention
  formatting issues in `improvements` instead.
- Comments authored by a human operator (non-agent email) are
  authoritative context — do not contradict them.
- Do not demand artifacts the environment cannot produce (e.g. an
  ODIN-STATUS block when the run was concluded by an operator).
- Proof-artifact commits (`.proof/` directory, message containing "proof artifacts") are created by the harness auto-commit, not the agent — never treat them as a rule violation or lower the verdict for them.
- If the run uncovered a durable project fact (a feed quirk, a command, a gotcha) that future tasks would rediscover, it should have been appended to PROJECT_NOTES.md — a missing note is an `improvements` item, not a verdict blocker.

### Optional markdown rendering (after the JSON block)

After the JSON fence you MAY emit the legacy sections as a human-readable rendering of the same content. The parser uses the JSON as the source of truth and ignores the rendering, so emitting it is purely for the board UI. The rendering is OPTIONAL — the JSON block is sufficient on its own. The legacy section names, in order, are:

- `### Quality Assessment`
- `### Slop Detection`
- `### Actionable Improvements`
- `### Agent Optimization`
- `### Quota / Resource Failure`
- `### Verdict`
"""


# Section header pattern: "### <SectionName>"
_SECTION_MAP = {
    "quality assessment": "quality_assessment",
    "slop detection": "slop_detection",
    "actionable improvements": "improvements",
    "agent optimization": "agent_optimization",
    "quota / resource failure": "quota_failure",
    "verdict": "verdict",
}


def _deduplicate_summary(lines: list[str]) -> list[str]:
    """Remove stuttered/duplicated lines from verdict summary.

    Agents sometimes repeat the verdict summary multiple times.
    Returns lines up to the first duplicate.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        normalized = line.strip().lower()
        if not normalized:
            result.append(line)
            continue
        if normalized in seen:
            break
        seen.add(normalized)
        result.append(line)
    return result


def _strip_odin_envelopes(text: str) -> str:
    """Remove all ODIN-STATUS/ODIN-SUMMARY envelopes from agent output.

    Agents sometimes append these envelopes to their reflection output.
    They're protocol framing, not part of the review content.
    """
    separator = "-------ODIN-STATUS-------"
    idx = text.find(separator)
    if idx == -1:
        return text
    return text[:idx].rstrip()


_REFLECTION_HEADER_RE = re.compile(
    r"^(Quality Assessment|Slop Detection|Actionable Improvements|Agent Optimization|Quota / Resource Failure|Verdict)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)


# Claude CLI workspace-trust warning emitted when the worktree carries a
# `.claude/settings.local.json` with stale permissions.allow entries from
# prior operator sessions. The reviewer is the ONLY consumer that skips
# project/local settings discovery: reflection.py sets
# `context["setting_sources"]="user"` so the claude harness emits
# `--setting-sources user` for the reviewer invocation only. Regular task
# execution does NOT set the flag, so project-level Claude Code safety
# hooks (secrets / lock-file edit blocks) keep loading normally. This
# regex is the defense-in-depth strip in case a future CLI version or
# settings layout reintroduces the warning.
_TRUST_WARNING_RE = re.compile(
    r"^.*Ignoring\s+\d+\s+permissions\.allow\s+entries.*workspace has not been trusted.*$\n?",
    flags=re.MULTILINE,
)


def _strip_trust_warning(text: str) -> str:
    """Remove the Claude CLI workspace-trust warning line(s) from output.

    The warning is a single line at the top of stdout when the reviewer
    runs in a worktree with a stale `.claude/settings.local.json`. The
    primary fix is the reviewer's explicit opt-in to
    `setting_sources="user"` (reflection.py) so the harness emits
    `--setting-sources user`; this is the belt-and-suspenders strip so a
    regression in upstream settings discovery doesn't pollute the
    captured raw output and force the parser to hard-ERROR the review
    (task 159: 4 reflections, all hard-ERRORed on this single line).
    """
    if not text:
        return text
    return _TRUST_WARNING_RE.sub("", text)


# JSON-fence block: matches ```json ... ``` and bare ``` ... ```. The
# content between fences is captured non-greedily and DOTALL so JSON
# values can contain newlines. We deliberately do NOT use a balanced-fence
# matcher because the prompt only emits flat JSON objects.
_JSON_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n(.*?)\n\s*```",
    flags=re.DOTALL,
)

_VERDICT_ENUM = {"PASS", "NEEDS_WORK", "FAIL"}


def _parse_json_review_block(text: str) -> dict | None:
    """Extract a single fenced JSON review block from reviewer output.

    The contract is a ```json ... ``` fence (or a bare ``` ... ``` fence)
    containing a JSON object with at minimum a `verdict` key. The
    function returns the parsed dict on success, None on no-block /
    parse-failure so the caller can fall through to the lenient markdown
    parser. The LAST parseable block wins so a stray JSON example in
    a preamble doesn't override the real review.
    """
    if not text:
        return None
    parsed = None
    for match in _JSON_FENCE_RE.finditer(text):
        candidate = match.group(1).strip()
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        # Must at least claim a verdict; otherwise it's not a review.
        if "verdict" not in obj:
            continue
        parsed = obj
    return parsed


def _sanitize_reflection_output(raw_output: str) -> str:
    """Keep reviewer content while dropping noisy CLI retry/tool traces."""
    if not raw_output:
        return ""

    text = _strip_odin_envelopes(raw_output).replace("\r\n", "\n")
    text = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)
    # Strip the Claude CLI workspace-trust warning (defense-in-depth — the
    # primary fix is `--setting-sources user` in the harness; this regex
    # catches a regression in upstream settings discovery).
    text = _strip_trust_warning(text)

    # If the CLI emitted retries/stack traces before the report, start at the
    # first report header. This preserves the actual review and drops provider
    # noise such as Gemini 429 retry dumps.
    header_match = re.search(
        r"(?im)^(?:#{1,6}\s*)?(Quality Assessment|Slop Detection|Actionable Improvements|Agent Optimization|Quota / Resource Failure|Verdict)\s*$",
        text,
    )
    if header_match:
        text = text[header_match.start():]

    # Some CLIs strip markdown heading markers in streamed text. Normalize bare
    # required headers back to markdown headings so the parser can section them.
    text = _REFLECTION_HEADER_RE.sub(lambda m: f"### {m.group(1).strip()}", text)

    clean_lines: list[str] = []
    skip_stack = False
    noisy_prefixes = (
        "YOLO mode is enabled",
        "Ripgrep is not available",
        "Attempt ",
        "_GaxiosError:",
        "Error executing tool read_file:",
        "Error executing tool list_directory:",
    )
    for line in text.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in noisy_prefixes):
            skip_stack = stripped.startswith(("Attempt ", "_GaxiosError:"))
            continue
        if skip_stack:
            if re.match(r"^(###\s+)?(Quality Assessment|Slop Detection|Actionable Improvements|Agent Optimization|Quota / Resource Failure|Verdict)\b", stripped, re.IGNORECASE):
                skip_stack = False
            elif stripped.startswith(("at ", "config:", "response:", "data:", "headers:", "status:", "request:")) or stripped in ("{", "}", "},"):
                continue
        clean_lines.append(line)
    return "\n".join(clean_lines).strip()


def _stage_reflection_screenshots_for_workspace(paths: list[str], working_dir: str | None, report_id: str) -> tuple[list[str], Path | None]:
    """Copy proof screenshots into the staged workspace and return guest paths."""
    if not paths or not working_dir:
        return paths, None
    workspace = Path(working_dir).resolve()
    if not workspace.exists():
        return paths, None
    staging_dir = workspace / ".odin-reflection-assets" / f"reflect_{report_id}"
    staged_guest_paths: list[str] = []
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
        for idx, raw in enumerate(paths):
            src = Path(raw)
            if not src.is_file():
                continue
            suffix = src.suffix or ".png"
            dest = staging_dir / f"proof_{idx}{suffix}"
            shutil.copy2(src, dest)
            rel = dest.relative_to(workspace).as_posix()
            staged_guest_paths.append(f"/tmp/odin-workspace/{rel}")
    except Exception:
        logger.warning("Failed to stage reflection screenshots for forkd", exc_info=True)
        return paths, None
    return staged_guest_paths or paths, staging_dir


def _extract_screenshot_urls(comments: list[dict]) -> list[str]:
    """Extract screenshot URLs from comment attachments.

    Scans proof comments for screenshot URLs in their attachments array.
    Returns a flat list of URLs.
    """
    urls = []
    for comment in comments:
        for attachment in comment.get("attachments") or []:
            if isinstance(attachment, dict):
                for url in attachment.get("screenshots") or []:
                    if url and isinstance(url, str):
                        urls.append(url)
    return urls


def _download_screenshots(
    urls: list[str],
    task_id: str,
    taskit_url: str,
    headers: dict,
) -> list[str]:
    """Download screenshot images to a temp directory.

    Returns list of local file paths. Silently skips images that fail to download.
    """
    if not urls:
        return []

    import tempfile
    img_dir = Path(tempfile.mkdtemp(prefix=f"odin_reflect_screenshots_{task_id}_"))

    paths = []
    for url in urls:
        # Extract filename from URL
        filename = url.rsplit("/", 1)[-1] if "/" in url else f"screenshot_{len(paths)}.png"
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=30, headers=headers)
            resp.raise_for_status()
            dest = img_dir / filename
            dest.write_bytes(resp.content)
            paths.append(str(dest))
            logger.info("Downloaded proof screenshot for task %s: %s", task_id, filename)
        except Exception:
            logger.warning(
                "Failed to download proof screenshot for task %s: %s",
                task_id, url, exc_info=True,
            )
    return paths


def parse_reflection_report(raw_output: str) -> dict:
    """Parse structured agent output into report sections.

    Splits on ``### `` headers to extract named sections.

    Args:
        raw_output: Full text output from the reviewer agent.

    Returns:
        Dict with keys: quality_assessment, slop_detection, improvements,
        agent_optimization, verdict, verdict_summary. Missing sections
        default to empty strings.
    """
    result = {
        "quality_assessment": "",
        "slop_detection": "",
        "improvements": "",
        "agent_optimization": "",
        "quota_failure": "",
        "verdict": "",
        "verdict_summary": "",
        "fix_list": [],
    }

    if not raw_output or not raw_output.strip():
        # Reviewer produced nothing at all — a reviewer/infra failure, not a
        # judgment. See the ERROR fallback at the bottom for the semantics.
        result["verdict"] = "ERROR"
        result["verdict_summary"] = (
            "Reviewer failure — no verdict in output. The reviewer produced no output."
        )
        return result

    # ── Layer 1: JSON contract (preferred) ─────────────────────────────
    # A clean ```json ... ``` fence is the canonical review format. The
    # prompt asks for it explicitly; if present, it is the source of
    # truth and any markdown ### sections after it are treated as a
    # rendering (not parsed separately).
    json_block = _parse_json_review_block(raw_output)
    if json_block is not None:
        result = _apply_json_review(result, json_block)
        # Trust the JSON verdict if it's one of the three enums. Anything
        # else (e.g. "MAYBE", a non-string, or a number) coerces to ERROR
        # so we never dispatch rework on a verdict the system can't route.
        v = result["verdict"]
        if v in _VERDICT_ENUM:
            return result
        # JSON present but verdict invalid → ERROR with the JSON verdict
        # surfaced for debuggability.
        head = json.dumps(json_block)[:500]
        result["verdict"] = "ERROR"
        result["verdict_summary"] = (
            f"Reviewer JSON review had invalid verdict {v!r}. "
            f"JSON: {head}"
        )
        return result

    # Normalize any markdown heading level (# through ######) to ### so the
    # parser works regardless of which heading depth the model chose.
    normalized = re.sub(r"^#{1,6}\s+", "### ", raw_output, flags=re.MULTILINE)

    # Split into sections by ### headers
    sections = re.split(r"^###\s+", normalized, flags=re.MULTILINE)

    # Strip preamble: text before the first ### header is typically
    # conversational noise from non-Claude models ("I'll analyze this...").
    # Only keep it if it contains checklist-style MET/UNMET content.
    preamble = sections[0].strip() if sections else ""
    if preamble and not result["quality_assessment"] and re.search(
        r"\b(MET|UNMET)\b", preamble
    ):
        # Strip leading narration line(s) before the first bullet
        bullet_match = re.search(r"^[-*]", preamble, flags=re.MULTILINE)
        if bullet_match:
            result["quality_assessment"] = preamble[bullet_match.start():].strip()

    for section in sections:
        if not section.strip():
            continue

        # First line is the header name, rest is content
        lines = section.split("\n", 1)
        header = lines[0].strip().lower()
        content = lines[1].strip() if len(lines) > 1 else ""

        field_name = _SECTION_MAP.get(header)
        if field_name and field_name != "verdict":
            result[field_name] = content
        elif field_name == "verdict":
            # Extract verdict enum, rest is summary
            verdict_lines = content.strip().split("\n")
            if verdict_lines:
                first_line = verdict_lines[0].strip()
                # Strip markdown formatting (bold, italic, backticks) and leading bullets
                cleaned = re.sub(r"[*_`#]+", "", first_line).strip()
                cleaned = re.sub(r"^[-•]\s*", "", cleaned).strip()
                # Strip leading "verdict:" prefix that some models add
                cleaned = re.sub(r"^verdict\s*:\s*", "", cleaned, flags=re.IGNORECASE)
                verdict_match = re.match(r"^(PASS|NEEDS_WORK|FAIL)\b", cleaned)
                if verdict_match:
                    result["verdict"] = verdict_match.group(1)
                    rest_of_first = cleaned[verdict_match.end():].strip()
                    rest_of_first = re.sub(r"^[:\-—–]+\s*", "", rest_of_first)
                    # Only take lines before any duplicate/stuttered summary
                    unique_lines = _deduplicate_summary(verdict_lines[1:])
                    subsequent = "\n".join(unique_lines).strip()
                    summary_parts = [p for p in [rest_of_first, subsequent] if p]
                    result["verdict_summary"] = "\n".join(summary_parts).strip()
                else:
                    # No recognized verdict — put the whole content in summary
                    result["verdict"] = "NEEDS_WORK"
                    result["verdict_summary"] = content.strip()

    # Last-resort fallback: if no verdict was extracted from structured sections,
    # scan the entire raw output for a verdict keyword. This handles cases where
    # non-Claude models bury the verdict in unstructured text.
    if not result["verdict"]:
        fallback_match = re.search(
            r"\b(PASS|NEEDS_WORK|FAIL)\b", raw_output
        )
        if fallback_match and fallback_match.group(1) == "PASS":
            # A bare "PASS" keyword with no other structure is the only
            # safe-to-honor fallback: it never dispatches rework, so the
            # cost of a false PASS is a missed NEEDS_WORK, not a wasted VM
            # cycle.
            result["verdict"] = "PASS"
            result["verdict_summary"] = "Verdict extracted from unstructured output."
        elif fallback_match:
            # A bare non-PASS keyword in otherwise unparseable output is
            # NOT a review: it carries no fix list, so dispatching rework
            # on it burns a full VM cycle with zero guidance (task #159:
            # three consecutive haiku reviews laundered this way). ERROR
            # is deliberately outside the backend's auto-advance set; the
            # task holds for operator triage or a reflection retry
            # instead of a blind rework loop.
            result["verdict"] = "ERROR"
            head = raw_output.strip()[:500]
            result["verdict_summary"] = (
                f"Reviewer output unparseable (bare {fallback_match.group(1)} "
                f"keyword, no structured review/fix list) — treating as reviewer "
                f"failure, not a judgment of the work. Raw head: {head}"
            )
        else:
            # No verdict anywhere ⇒ the REVIEWER failed (crashed harness, empty
            # output, infra error) — that is not a judgment about the work.
            # ERROR is deliberately outside the backend's auto-advance set
            # (PASS merges; NEEDS_WORK/FAIL retry): the task stays in REVIEW
            # for operator triage instead of looping rework on garbage
            # (task #103, 2026-07-05: an msb boot error was coerced to
            # NEEDS_WORK and drove a pointless retry). Embed the head of the
            # raw output so the actual failure is visible on the board.
            result["verdict"] = "ERROR"
            head = raw_output.strip()[:500]
            result["verdict_summary"] = (
                "Reviewer failure — no verdict in output. "
                + (f"Output head: {head}" if head else "The reviewer produced no output.")
            )

    return result


def _apply_json_review(result: dict, json_block: dict) -> dict:
    """Map a JSON review block to the result dict.

    The JSON contract is the source of truth. Only the `verdict` key
    must be present; the other fields default to empty strings / empty
    list if absent. `verdict_summary` is the user-facing one-line
    justification; the legacy markdown section names
    (quality_assessment, slop_detection, etc.) are still mapped so the
    existing TaskIt board rendering keeps working.
    """
    # Verdict first: must be one of the enums (validated by caller).
    verdict_raw = json_block.get("verdict")
    if isinstance(verdict_raw, str):
        result["verdict"] = verdict_raw.strip().upper()

    # summary → verdict_summary
    summary = json_block.get("summary")
    if isinstance(summary, str):
        result["verdict_summary"] = summary.strip()

    # Section fields — accept either the legacy names or the JSON-canonical
    # names. Both are valid; legacy names kept for any operator-facing
    # tools that already emit them.
    field_map = {
        "quality_assessment": "quality_assessment",
        "slop_detection": "slop_detection",
        "improvements": "improvements",
        "agent_optimization": "agent_optimization",
        "quota_failure": "quota_failure",
    }
    for json_key, result_key in field_map.items():
        value = json_block.get(json_key)
        if isinstance(value, str) and value.strip():
            result[result_key] = value.strip()

    # fix_list — list of strings, only meaningful for NEEDS_WORK
    fix_list = json_block.get("fix_list")
    if isinstance(fix_list, list):
        result["fix_list"] = [
            str(item).strip() for item in fix_list if str(item).strip()
        ]
    elif isinstance(fix_list, str) and fix_list.strip():
        # Some reviewers may emit a single string instead of a list;
        # split on newlines and bullets so the downstream code can
        # treat it as a list.
        result["fix_list"] = [
            line.strip().lstrip("-*•").strip()
            for line in fix_list.splitlines()
            if line.strip()
        ]

    return result


def _get_auth_token(taskit_url: str) -> str:
    """Obtain a Bearer token from TaskIt using credentials in env/config."""
    import os
    email = os.environ.get("ODIN_ADMIN_USER", "")
    password = os.environ.get("ODIN_ADMIN_PASSWORD", "")
    if not email or not password:
        return ""
    try:
        from odin.backends.taskit import TaskItAuth
        login_url = f"{taskit_url.rstrip('/')}/auth/login/"
        auth = TaskItAuth(login_url, email, password)
        return auth.get_token() or ""
    except Exception:
        logger.warning("Failed to obtain auth token for reflection", exc_info=True)
        return ""


def _reflection_requires_forkd_browser(agent_cfg) -> bool:
    """Return True when reflection uses a browser/chrome forkd snapshot."""
    if not getattr(agent_cfg, "run_in_forkd", False):
        return False
    tag = str(getattr(agent_cfg, "forkd_snapshot_tag", "") or "")
    extras = " ".join(str(x) for x in (getattr(agent_cfg, "forkd_extra", []) or []))
    haystack = f"{tag} {extras}".lower()
    return any(term in haystack for term in ("browser", "chrome", "chromium"))


def reflect_task(
    task_id: str,
    report_id: str,
    model: str,
    agent: str,
    taskit_url: str,
    auth_token: str = "",
    timeout: int = 300,
    log_dir: str | None = None,
    selection_reason: str = "",
):
    """Execute a reflection audit on a completed task.

    Flow:
    1. Update report status → RUNNING
    2. Gather task context from TaskIt API
    3. Build reflection prompt
    4. Execute reviewer agent via harness
    5. Parse and submit results

    Args:
        task_id: TaskIt task ID.
        report_id: ReflectionReport ID.
        model: Reviewer model name (e.g. "claude-opus-4-6").
        agent: Reviewer agent name (e.g. "claude").
        taskit_url: TaskIt backend base URL.
        auth_token: Optional Bearer token (auto-obtained from env if empty).
        timeout: Max seconds for harness execution.
        log_dir: Directory for structured JSONL logs (OdinLogger). None to skip.
        selection_reason: W3.18 — why this reviewer was picked
            ("size_small" / "size_medium" / "size_large" / "default" /
            "board_model_override" / "forced_provider" / "caller_override" /
            "manual_default"). Surfaced in the prompt's
            [CTX:reviewer-selection] section so the reviewer can confirm
            the choice matches the task's context size.
    """
    # Structured logger (optional — mirrors orchestrator pattern)
    structured_log = None
    if log_dir:
        from odin.logging import OdinLogger
        structured_log = OdinLogger(log_dir)

    def _slog(action: str, **kwargs):
        if structured_log:
            structured_log.log(action=action, task_id=task_id, agent=agent, **kwargs)

    _slog("reflection_started", metadata={"report_id": report_id, "model": model, "selection_reason": selection_reason})

    if not auth_token:
        auth_token = _get_auth_token(taskit_url)

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    def _patch_report(payload: dict):
        resp = httpx.patch(
            f"{taskit_url}/reflections/{report_id}/",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    start_time = time.time()
    staged_screenshot_dir: Path | None = None

    try:
        # 1. Gather context
        detail_resp = httpx.get(
            f"{taskit_url}/tasks/{task_id}/detail/",
            headers=headers,
        )
        detail_resp.raise_for_status()
        task_data = detail_resp.json()

        metadata = task_data.get("metadata") or {}
        working_dir = metadata.get("working_dir")
        usage = metadata.get("last_usage", {})
        tokens_str = f"{usage.get('total_tokens', 'N/A'):,}" if usage.get("total_tokens") else "N/A"

        comments_list = task_data.get("comments") or []

        # Find the latest reflection/summary checkpoint to mark the boundary
        # between previous attempts and the current one. Comments before the
        # checkpoint are "history"; comments after are the current attempt.
        checkpoint_idx = None
        for i in range(len(comments_list) - 1, -1, -1):
            if comments_list[i].get("comment_type") in ("reflection", "summary"):
                checkpoint_idx = i
                break

        # Filter and annotate comments — keep all but insert a visible separator
        # so the reviewer can distinguish previous attempts from the current one.
        filtered_comments = []
        separator_inserted = False
        for i, c in enumerate(comments_list):
            ctype = c.get("comment_type", "status")

            # Insert separator after the checkpoint comment
            if checkpoint_idx is not None and i == checkpoint_idx + 1 and not separator_inserted:
                filtered_comments.append(
                    "\n--- CURRENT ATTEMPT (evaluate this) ---\n"
                )
                separator_inserted = True

            # Filter BEFORE truncating: slicing a large raw-JSON stream mid-line
            # leaves dangling fragments the line filter can't catch, and the
            # reviewer then (correctly) flags the evidence as truncated garbage.
            content = c.get("content", "")
            # Skip status_update comments that echo the task description
            if ctype == "status_update" and content.startswith("Effective input"):
                continue
            # Skip status_update comments that are hook responses (raw JSON system events)
            if ctype == "status_update" and '{"type":"system"' in content:
                continue
            is_history = checkpoint_idx is not None and i <= checkpoint_idx
            formatted = _format_comment_for_prompt(c, is_history=is_history)
            if formatted:
                filtered_comments.append(formatted)

        # If checkpoint exists but no comments came after it, add separator at end
        if checkpoint_idx is not None and not separator_inserted:
            filtered_comments.append(
                "\n--- CURRENT ATTEMPT (evaluate this) ---\n"
            )

        comments_text = "\n".join(filtered_comments)

        # Extract screenshot URLs from proof comment attachments
        screenshot_urls = _extract_screenshot_urls(comments_list)

        deps_list = task_data.get("depends_on") or []
        deps_text = "\n".join(
            f"- {dep}" for dep in deps_list
        ) or "No dependencies."

        # Clean raw JSONL execution output into human-readable text
        raw_execution = metadata.get("full_output", "")
        if raw_execution:
            execution_output = extract_text_from_stream(raw_execution)
            if not execution_output.strip():
                execution_output = raw_execution[:5000]
            # Bound the inline trace — the reviewer reads uncapped per-task
            # `.proof/` from the worktree for detail; head+tail keeps the
            # verdict-relevant ends.
            execution_output = _truncate_execution_output(
                execution_output, _EXECUTION_OUTPUT_CHAR_LIMIT
            )
        else:
            execution_output = "No execution output available."

        # Metadata summary for the [CTX:metadata] section
        meta_parts = []
        if metadata.get("selected_model"):
            meta_parts.append(f"Model: {metadata['selected_model']}")
        if metadata.get("last_duration_ms"):
            meta_parts.append(f"Duration: {metadata['last_duration_ms']}ms")
        if usage.get("total_tokens"):
            meta_parts.append(f"Tokens: {usage['total_tokens']:,}")
        if metadata.get("working_dir"):
            meta_parts.append(f"Working dir: {metadata['working_dir']}")
        metadata_summary = "\n".join(meta_parts) if meta_parts else "No metadata."

        task_context = {
            "title": task_data.get("title", "Unknown"),
            "status": task_data.get("status", "Unknown"),
            "agent": (task_data.get("assignee") or {}).get("name", agent),
            "model": task_data.get("model_name") or metadata.get("selected_model", model),
            "duration_ms": metadata.get("last_duration_ms", "N/A"),
            "tokens": tokens_str,
            "description": task_data.get("description", ""),
            "execution_output": execution_output,
            "comments": comments_text or "No comments.",
            "dependencies": deps_text,
            "metadata_summary": metadata_summary,
            "selection_reason": selection_reason,
            "task_id": task_id,
        }

        # Resolve reviewer config before building the prompt so forkd reviewers
        # receive screenshot proof at paths visible inside the staged workspace.
        from odin.config import load_config
        from odin.models import AgentConfig

        cfg = None
        config_path = _resolve_reflection_config_path(working_dir)
        try:
            cfg = load_config(config_path)
        except Exception:
            pass
        agent_cfg = cfg.agents[agent] if cfg and agent in cfg.agents else AgentConfig(default_model=model)

        # Download proof screenshots so the reviewer can visually inspect them.
        # For forkd, host /tmp paths are invisible; stage images inside the
        # workspace and reference their guest /tmp/odin-workspace paths.
        skip_proof = task_data.get("board_skip_proof", False)
        if not skip_proof:
            screenshot_paths = _download_screenshots(
                screenshot_urls, task_id, taskit_url, headers,
            )
            if screenshot_paths and getattr(agent_cfg, "run_in_forkd", False):
                screenshot_paths, staged_screenshot_dir = _stage_reflection_screenshots_for_workspace(
                    screenshot_paths, working_dir, report_id,
                )
            if screenshot_paths:
                task_context["screenshot_paths"] = screenshot_paths
        else:
            task_context["skip_proof"] = True

        # Log context sizes for verification
        context_sizes = {
            "description_len": len(task_context["description"]),
            "comments_count": len(comments_list),
            "execution_output_len": len(execution_output),
            "deps_count": len(deps_list),
            "metadata_summary_len": len(metadata_summary),
        }
        _slog("reflection_context_gathered", metadata=context_sizes)

        # 2. Build prompt
        custom_prompt = ""
        prompt = build_reflection_prompt(task_context, custom_prompt=custom_prompt)

        _slog("reflection_prompt_built", metadata={"prompt_length": len(prompt)})

        # 3. Mark as RUNNING and store the assembled prompt for transparency
        _patch_report({"status": "RUNNING", "assembled_prompt": prompt})

        # 4. Build execution context

        # Create trace/output files for harness capture (mirrors orchestrator
        # pattern — resolve() included: sandboxed harnesses bind-mount the trace
        # file and msb rejects relative mount sources).
        if log_dir:
            trace_dir = Path(log_dir).resolve()
            trace_dir.mkdir(parents=True, exist_ok=True)
        else:
            trace_dir = Path(tempfile.mkdtemp(prefix="odin_reflect_"))
        trace_file = str(trace_dir / f"reflect_{report_id}.trace.jsonl")
        output_file = str(trace_dir / f"reflect_{report_id}.out")
        for raw in (trace_file, output_file):
            path = Path(raw)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            path.touch()

        context = {
            "working_dir": working_dir,
            "model": model,
            "trace_file": trace_file,
            "output_file": output_file,
            # Reflection prompts ask for ### Quality Assessment ... ### Verdict,
            # not the -------ODIN-STATUS------- envelope. Skip envelope
            # validation so a clean reviewer run isn't reported as failed.
            "validate_status": False,
            # Reflection is a READ-ONLY audit: the reviewer must never write to
            # the repo. Sandbox harnesses bind-mount every repo/workspace path
            # :ro so a rogue reviewer cannot mutate the worktree or main .git.
            # The reviewer's own trace/output files live OUTSIDE the repo mounts
            # (trace_dir above) and stay writable so the guest can tee into them.
            "read_only_workspace": True,
            # Skip project/local `.claude/settings.*` discovery for the
            # reviewer ONLY. The task worktree carries a stale
            # `.claude/settings.local.json` whose `permissions.allow`
            # entries are pre-trust, and loading it dumps "Ignoring N
            # permissions.allow entries ... workspace has not been
            # trusted" warnings into the captured reviewer output — which
            # used to force the parser to hard-ERROR the reflection as a
            # reviewer failure (task 159: 4 reflections hard-ERRORed on
            # this single noise line). Regular task execution does NOT
            # set this flag, so project-level safety hooks (secrets /
            # lock-file edit blocks) keep loading normally.
            "setting_sources": "user",
        }

        # 5. Execute reviewer via harness
        if _reflection_requires_forkd_browser(agent_cfg):
            context["forkd_require_chrome"] = True
        harness = get_harness(agent, agent_cfg)

        _slog("reflection_harness_started", metadata={"model": model, "working_dir": working_dir})

        harness_start = time.time()
        result = asyncio.run(harness.execute(prompt, context))
        duration_ms = int((time.time() - harness_start) * 1000)
        if staged_screenshot_dir:
            shutil.rmtree(staged_screenshot_dir.parent, ignore_errors=True)
            staged_screenshot_dir = None

        # 6. Cost tracking (local audit trail only — cost is computed dynamically by TaskIt)
        token_usage = result.metadata.get("usage", {})
        try:
            from odin.cost_tracking import CostStore, CostTracker
            pricing = _load_pricing_table()
            cost_store = CostStore(cfg.cost_storage if cfg else ".odin/costs")
            tracker = CostTracker(cost_store, pricing=pricing)
            tracker.record_task(
                task_id=f"reflect_{report_id}",
                spec_id=None,
                result=result,
                model=model,
            )
        except Exception:
            logger.debug("Cost tracking failed for reflection %s", report_id, exc_info=True)

        # Read raw JSONL trace for debugging visibility
        raw_jsonl = ""
        if Path(trace_file).exists():
            raw_jsonl = Path(trace_file).read_text()
        elif Path(output_file).exists():
            raw_jsonl = Path(output_file).read_text()

        # 7. Parse and submit
        clean_output = _sanitize_reflection_output(result.output)
        if not result.success:
            _patch_report({
                "status": "FAILED",
                "error_message": result.error or "Harness execution failed",
                "raw_output": clean_output[:10000],
                "execution_trace": _truncate_trace(raw_jsonl, 50000),
                "duration_ms": duration_ms,
                "token_usage": token_usage,
            })
            _slog("reflection_failed", duration_ms=duration_ms, metadata={
                "error": result.error or "Harness execution failed",
                "token_usage": token_usage,
            })
            return

        parsed = parse_reflection_report(clean_output)

        _patch_report({
            "status": "COMPLETED",
            "quality_assessment": parsed["quality_assessment"],
            "slop_detection": parsed["slop_detection"],
            "improvements": parsed["improvements"],
            "agent_optimization": parsed["agent_optimization"],
            "quota_failure": parsed["quota_failure"],
            "verdict": parsed["verdict"],
            "verdict_summary": parsed["verdict_summary"],
            "raw_output": clean_output[:10000],
            "execution_trace": _truncate_trace(raw_jsonl, 50000),
            "duration_ms": duration_ms,
            "token_usage": token_usage,
        })

        _slog("reflection_completed", duration_ms=duration_ms, metadata={
            "verdict": parsed["verdict"],
            "token_usage": token_usage,
        })

        logger.info(
            "Reflection completed: task=%s, report=%s, verdict=%s, duration=%sms",
            task_id, report_id, parsed["verdict"], duration_ms,
        )

    except Exception as exc:
        if staged_screenshot_dir:
            shutil.rmtree(staged_screenshot_dir.parent, ignore_errors=True)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error("Reflection failed: task=%s, report=%s", task_id, report_id, exc_info=True)
        _slog("reflection_failed", duration_ms=duration_ms, metadata={
            "error": str(exc),
        })
        try:
            _patch_report({
                "status": "FAILED",
                "error_message": str(exc),
                "duration_ms": duration_ms,
            })
        except Exception:
            logger.error("Failed to report reflection failure", exc_info=True)


def _load_pricing_table():
    """Load pricing table — reuses Orchestrator's static method logic."""
    from pathlib import Path
    from odin.cost_tracking.estimator import load_pricing_table
    candidates = [
        Path(__file__).resolve().parents[3] / "taskit" / "taskit-backend" / "data" / "agent_models.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return load_pricing_table(str(path))
            except Exception:
                return None
    return None
