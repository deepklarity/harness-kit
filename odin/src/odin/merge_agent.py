"""Automated merge-conflict resolution agent.

When a task branch merge into its spec branch conflicts, this module
classifies the conflict and attempts resolution for clearly mechanical
cases.  Ambiguous conflicts (product-code overlap) are escalated as
blocking questions — the agent never guesses on semantic conflicts.

Classification is deterministic (rule-based, not LLM-based) so outcomes
are predictable and testable:

- **Mechanical**: every conflicting file is a generated agent config
  (``HARNESS_GENERATED_PATHS``).  These files are rewritten on every
  task run and frequently collide (F24).  Taking the spec side is safe
  because the next task run regenerates them anyway.

- **Ambiguous, additive-non-overlapping**: a product-code file whose
  hunk already classifies as ``keep-both`` (:func:`classify_resolution`)
  AND where BOTH sides are pure additions against the merge base — no
  modified or deleted lines on either side (:func:`_is_pure_additive_both_sides`).
  Two disjoint, pure additions at the same spot have exactly one safe
  combination, so this is applied directly (both sides, in file order),
  syntax-checked, and reported — never asked as a question. A question
  whose answer is always "keep both" isn't a question (task 254). Any
  modification on either side (which git represents as a delete+add
  against the merge base) fails this gate and still parks for a human.

- **Ambiguous, everything else**: any conflicting file is product code
  with a real edit on at least one side.  Auto-resolving these would
  silently pick a winner on a semantic decision — exactly what the
  agent must not do.

Reversibility (task #244's ``reversibility.py``): a spec-branch merge is
``SEMI`` — revertible but shared with other tasks — and auto-applying
additive-non-overlapping conflicts stays within the merge agent's
existing authority; it already auto-resolves mechanical conflicts
without asking. The behavioral safety net for this auto-decision is the
post-merge spec-branch suite run (W5.12) — it runs the full suite
against the spec branch after every merge and flags the spec if the
combination broke something the per-task suites couldn't see. This
module's own syntax check only catches "obviously broken", not behavior.

The LLM merge agent (configured via ``merge_agent`` in
``.odin/config.yaml``) can be dispatched for finer-grained analysis of
gray-area conflicts in the future.  For now, the deterministic
classifier handles the common cases correctly and the LLM dispatch
point is wired through :func:`resolve_conflicts_in_worktree`.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from odin.worktree import _is_generated_agent_config

logger = logging.getLogger("odin.merge_agent")

# Maximum lines of a conflict hunk surfaced in the operator report.
# Trimmed to keep the comment scannable; full file is still on disk.
_HUNK_HEAD_MAX_LINES = 12
# Side-summary hard cap so one file can't blow out the comment.
_SIDE_SUMMARY_MAX_CHARS = 180
# Strip trailing whitespace and collapse blank-line runs when summarising.
_BLANK_LINE_RE = re.compile(r"\n\s*\n+")
# How many recent commits per side to surface as "why" context.
_COMMIT_CONTEXT_LIMIT = 3
# Above this many lines on BOTH sides, a low Jaccard overlap means "two
# independent rewrites", not "two small complementary additions" — see
# classify_resolution.
_LARGE_REWRITE_LINE_THRESHOLD = 30


@dataclass
class MergeResolution:
    """Outcome of a merge-conflict resolution attempt.

    Either ``resolved`` is True (mechanical conflicts were auto-fixed
    and the merge was committed) or ``needs_human`` is True (ambiguous
    conflicts were found and the merge was aborted).

    When ``needs_human`` is True the resolution also carries per-file
    context captured from the worktree BEFORE the merge was aborted:

    - ``conflict_hunks``: path → first conflict region as written by git
      (with the ``<<<<<<<``/``=======``/``>>>>>>>`` markers).
    - ``file_resolutions``: path → heuristic verdict (``"keep-both"`` or
      ``"choose"``) plus a one-line rationale.

    Downstream consumers (``format_merge_question``, ``dag_executor``)
    use these to build a self-contained report — the operator can
    decide without opening the worktree.
    """

    resolved: bool = False
    needs_human: bool = False
    mechanical_files: List[str] = field(default_factory=list)
    ambiguous_files: List[str] = field(default_factory=list)
    resolution_method: Optional[str] = None
    rationale: Optional[str] = None
    question_text: Optional[str] = None
    error: Optional[str] = None
    conflict_hunks: Dict[str, str] = field(default_factory=dict)
    file_resolutions: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Per-file commit subject lines explaining WHY each side touched the
    # file — spec side read from ``HEAD``, task side from ``MERGE_HEAD``
    # (both exist while the conflicted merge is in progress).  Used by
    # ``format_merge_question`` to ground the report in the actual stated
    # intent instead of guessing from the diff.
    spec_commit_messages: Dict[str, List[str]] = field(default_factory=dict)
    task_commit_messages: Dict[str, List[str]] = field(default_factory=dict)
    # Model + token usage when resolution invoked an LLM (task #209) —
    # empty for today's deterministic rule-based classification. Threaded
    # through to MergeResult so dag_executor can record merge cost the
    # same way reflection cost is captured.
    agent_model: Optional[str] = None
    token_usage: Dict[str, int] = field(default_factory=dict)


def classify_conflict(conflicting_files: List[str]) -> Tuple[List[str], List[str]]:
    """Split conflicting files into (mechanical, ambiguous) buckets.

    A file is **mechanical** when it matches a known generated agent
    config pattern (``HARNESS_GENERATED_PATHS``).  These are rewritten
    on every task run and taking either side is safe.

    Any other file is **ambiguous** — it may contain real product logic
    and must not be auto-resolved.

    Returns ``(mechanical_files, ambiguous_files)``.
    """
    mechanical: List[str] = []
    ambiguous: List[str] = []
    for path in conflicting_files:
        if _is_generated_agent_config(path):
            mechanical.append(path)
        else:
            ambiguous.append(path)
    return mechanical, ambiguous


def resolve_conflicts_in_worktree(
    wt,
    worktree: Path,
    conflicting_files: List[str],
    task_brief: str = "",
    *,
    resolution_guidance: str = "",
    task_id: str = "",
) -> MergeResolution:
    """Attempt to resolve merge conflicts inside a worktree.

    The merge must already be in progress (``git merge`` was run and
    produced conflicts).  This function classifies the conflicts and,
    if **all** are mechanical, resolves them by taking the spec side
    (``--ours``) for each generated config file, then stages and
    commits the merge.

    If any file is ambiguous and no ``resolution_guidance`` was
    supplied, the merge is **not** completed by this function — the
    caller is responsible for aborting.  The returned
    :class:`MergeResolution` carries ``needs_human=True`` with the file
    list and a formatted question for the board.

    When a human has replied to that question, the caller re-invokes
    this function with ``resolution_guidance`` set to the reply text
    (and ``task_id`` so a "move to its own dir" instruction can name a
    destination). See :func:`resolve_ambiguous_with_guidance`.

    Parameters
    ----------
    wt : WorktreeManager
        The worktree manager (used for ``_git`` access).
    worktree : Path
        The worktree directory where the conflicted merge lives.
    conflicting_files : List[str]
        Files in conflict (from ``git status --porcelain``).
    task_brief : str
        The task title/brief, included in the question text for context.
    resolution_guidance : str
        A human's free-text reply to a previous merge question. When
        set and there are ambiguous files, resolution is attempted
        instead of escalating unconditionally.
    task_id : str
        The task ID — used to build a ``.proof/task-<id>/...``
        destination when guidance asks to move a file to its own
        directory.
    """
    mechanical, ambiguous = classify_conflict(conflicting_files)

    if ambiguous and resolution_guidance:
        return resolve_ambiguous_with_guidance(
            wt, worktree, mechanical, ambiguous, task_brief,
            resolution_guidance, task_id,
        )

    if ambiguous:
        # Capture per-file conflict context BEFORE the caller aborts the
        # merge (the merge is still in progress; the markers are still on
        # disk).  Once the merge is aborted the files revert to one side
        # and the markers are gone, so this window is the only chance to
        # snapshot the divergent lines for the report.
        conflict_hunks: Dict[str, str] = {}
        file_resolutions: Dict[str, Dict[str, str]] = {}
        spec_commit_messages: Dict[str, List[str]] = {}
        task_commit_messages: Dict[str, List[str]] = {}
        side_texts: Dict[str, Tuple[str, str]] = {}
        for path in conflicting_files:
            full_region = _extract_conflict_region(worktree, path)
            if full_region:
                conflict_hunks[path] = _truncate_conflict_region(full_region, _HUNK_HEAD_MAX_LINES)
                # Classify from the FULL region, not the truncated display
                # hunk — truncating first can make a large hunk's task side
                # look empty and misclassify a genuine modify/modify
                # conflict as delete-vs-modify.
                spec_text, task_text = _split_hunk_sides(full_region)
                side_texts[path] = (spec_text, task_text)
                verdict, why = classify_resolution(spec_text, task_text)
                file_resolutions[path] = {"verdict": verdict, "rationale": why}
                spec_msgs = _commit_subjects_for_path(wt, worktree, "HEAD", path)
                task_msgs = _commit_subjects_for_path(wt, worktree, "MERGE_HEAD", path)
                if spec_msgs:
                    spec_commit_messages[path] = spec_msgs
                if task_msgs:
                    task_commit_messages[path] = task_msgs

        auto_rationale = _try_auto_resolve_additive(
            wt, worktree, ambiguous, file_resolutions, side_texts,
        )
        if auto_rationale is not None:
            resolved_mechanical: List[str] = []
            for path in mechanical:
                checkout = wt._git("checkout", "--ours", "--", path, cwd=worktree)
                if checkout.returncode != 0:
                    logger.warning(
                        "checkout --ours failed for %s during additive "
                        "auto-resolve: %s", path, checkout.stderr.strip(),
                    )
                    return MergeResolution(
                        needs_human=True,
                        mechanical_files=mechanical,
                        ambiguous_files=ambiguous,
                        error=f"Failed to resolve {path}: {checkout.stderr.strip()}",
                        conflict_hunks=conflict_hunks,
                        file_resolutions=file_resolutions,
                        spec_commit_messages=spec_commit_messages,
                        task_commit_messages=task_commit_messages,
                        question_text=format_merge_question(
                            mechanical, ambiguous, task_brief,
                            conflict_hunks=conflict_hunks,
                            file_resolutions=file_resolutions,
                            spec_commit_messages=spec_commit_messages,
                            task_commit_messages=task_commit_messages,
                        ),
                    )
                wt._git("add", "--", path, cwd=worktree)
                resolved_mechanical.append(path)

            return MergeResolution(
                resolved=True,
                mechanical_files=resolved_mechanical + ambiguous,
                resolution_method=(
                    "auto keep-both — both sides are pure, non-overlapping "
                    "additions against the merge base"
                ),
                rationale=auto_rationale,
                file_resolutions={path: {"verdict": "keep-both"} for path in ambiguous},
            )

        question = format_merge_question(
            mechanical, ambiguous, task_brief,
            conflict_hunks=conflict_hunks,
            file_resolutions=file_resolutions,
            spec_commit_messages=spec_commit_messages,
            task_commit_messages=task_commit_messages,
        )
        return MergeResolution(
            needs_human=True,
            mechanical_files=mechanical,
            ambiguous_files=ambiguous,
            question_text=question,
            conflict_hunks=conflict_hunks,
            file_resolutions=file_resolutions,
            spec_commit_messages=spec_commit_messages,
            task_commit_messages=task_commit_messages,
            rationale=(
                f"{len(ambiguous)} ambiguous file(s) require human judgment; "
                f"{len(mechanical)} generated-config file(s) could be auto-resolved "
                f"but are held pending the human decision."
            ),
        )

    # All mechanical — resolve each by taking the spec side (--ours).
    # Generated configs are rewritten per-run, so the spec baseline is
    # the safe canonical version.
    resolved: List[str] = []
    for path in mechanical:
        checkout = wt._git("checkout", "--ours", "--", path, cwd=worktree)
        if checkout.returncode != 0:
            logger.warning("checkout --ours failed for %s: %s", path, checkout.stderr.strip())
            # Capture hunks for any remaining conflicted files so the
            # escalation carries the same per-file context.
            conflict_hunks: Dict[str, str] = {}
            for remaining in mechanical:
                hunk = extract_conflict_hunk_text(worktree, remaining)
                if hunk:
                    conflict_hunks[remaining] = hunk
            return MergeResolution(
                needs_human=True,
                mechanical_files=mechanical,
                ambiguous_files=[],
                error=f"Failed to resolve {path}: {checkout.stderr.strip()}",
                conflict_hunks=conflict_hunks,
                question_text=format_merge_question(
                    mechanical, [], task_brief,
                    conflict_hunks=conflict_hunks,
                ),
            )
        wt._git("add", "--", path, cwd=worktree)
        resolved.append(path)

    return MergeResolution(
        resolved=True,
        mechanical_files=resolved,
        resolution_method="checkout --ours (spec side) for generated agent configs",
        rationale=(
            "All conflicting files are generated agent configs (F24). "
            "Taking the spec side is safe — these files are rewritten on "
            "every task run."
        ),
    )


# ------------------------------------------------------------------
# Additive-non-overlapping auto-resolve — the confidence gate (task 254)
# ------------------------------------------------------------------
#
# classify_resolution's "keep-both" verdict only looks at the two hunk
# texts in isolation. That's the right signal for what to show a human,
# but it isn't by itself a strong enough guarantee to skip asking:
# it can't see whether a side's change was actually an edit to existing
# content that happens to read as an "addition" within the hunk
# boundaries. The merge-base diff can see that — it's the independent,
# stronger check this module requires before applying without asking.


def _diff_deletions(wt, worktree: Path, base_ref: str, ref: str, path: str) -> Optional[int]:
    """Return how many lines ``ref`` deleted from ``path`` relative to
    ``base_ref``, or ``None`` if that can't be determined (git failure,
    binary file — ``--numstat`` reports ``-``/``-`` for those).

    A modification shows up here too: git's diff represents "change line
    X" as "delete old line X, add new line X", so a non-zero count
    catches edits, not just outright removals.
    """
    result = wt._git("diff", "--numstat", base_ref, ref, "--", path, cwd=worktree)
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    if not line:
        # No diff for this path on this side vs. the base — trivially
        # zero deletions.
        return 0
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    added, deleted = parts[0], parts[1]
    if added == "-" or deleted == "-":
        return None
    try:
        return int(deleted)
    except ValueError:
        return None


def _is_pure_additive_both_sides(wt, worktree: Path, path: str) -> bool:
    """True when BOTH the spec side (``HEAD``) and the task side
    (``MERGE_HEAD``) only added lines to ``path`` relative to the merge
    base — no modified or deleted lines on either side.

    This is checked against the whole file's diff, not just the
    unresolved hunk, but that's sufficient: the hunk is a subset of the
    file, so zero deletions across the whole file guarantees zero
    deletions within the hunk too. Any git failure (no merge in
    progress, detached refs, binary content) resolves to ``False`` —
    the gate fails closed.
    """
    base = wt._git("merge-base", "HEAD", "MERGE_HEAD", cwd=worktree)
    if base.returncode != 0:
        return False
    merge_base = base.stdout.strip()
    if not merge_base:
        return False
    for ref in ("HEAD", "MERGE_HEAD"):
        deletions = _diff_deletions(wt, worktree, merge_base, ref, path)
        if deletions is None or deletions > 0:
            return False
    return True


def _syntax_check(worktree: Path, path: str) -> bool:
    """Fast post-apply sanity check — not a substitute for the real
    test suite (the post-merge spec-branch suite run, W5.12, is that),
    just a guard against auto-applying a keep-both that isn't even
    parseable.

    Python files are compiled (syntax only, never executed); JSON files
    are parsed. Any other extension is assumed fine — this check can't
    reason about every file type, and the suite run behind it is the
    real safety net.
    """
    full = Path(worktree) / path
    try:
        text = full.read_text(errors="replace")
    except OSError:
        return False

    suffix = full.suffix.lower()
    if suffix == ".py":
        try:
            compile(text, str(full), "exec")
        except SyntaxError:
            return False
        return True
    if suffix == ".json":
        import json

        try:
            json.loads(text)
        except ValueError:
            return False
        return True
    return True


# Conflict-marker lines that should never appear in a committed file.
# Each is anchored at the start of a line so a docstring containing
# ``=======`` as content doesn't trip the gate — only an actual
# conflict marker counts.
_CONFLICT_MARKER_PREFIXES = ("<<<<<<<", "=======", ">>>>>>>")


def _first_marker_line(text: str) -> int:
    """Return the 1-based line number of the first conflict marker in
    ``text``, or ``0`` when none is present. Anchored at line start so
    incidental ``=======`` inside string content is ignored."""
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        for prefix in _CONFLICT_MARKER_PREFIXES:
            if stripped.startswith(prefix):
                return idx
    return 0


def _check_post_merge_files(
    worktree: Path,
    paths: List[str],
) -> List[Tuple[str, str, int]]:
    """Post-merge safety gate (task 338).

    Run after every merge commit (whether produced by ``git merge``
    alone or by the merge-agent resolution path). Scans each file in
    ``paths`` for the two breakage classes that historically rode a
    merge commit into the spec branch:

    1. **Conflict markers** — ``<<<<<<<``/``=======``/``>>>>>>>`` lines
       surviving in a committed file. Every file type is checked;
       markers should never appear in any committed file.
    2. **Python syntax errors** — ``compile()`` rejects files that
       would break ``migrate`` / runtime. JSON is left to
       :func:`_syntax_check` (the broader gate is the multi-format
       safety net; this function only handles the cases whose
       breakage is the historical incident the task is closing).

    Returns a list of ``(path, error_type, line_no)`` violations
    (empty when the merge is clean). ``error_type`` is one of
    ``"conflict_marker"`` or ``"syntax_error"``.

    Missing files are intentionally **not** flagged: a path the merge
    deleted can't carry markers or syntax errors into the result, so
    there's nothing for this gate to check. The worktree layer is
    responsible for filtering deletions before passing paths in
    (``git diff --name-only --diff-filter=ACMRT HEAD~1 HEAD`` is the
    cheap option) — but even if it doesn't, the gate stays correct
    because deletion produces no committed content to scan.

    The function is deliberately cheap — file read + line scan +
    ``compile()`` (which is itself a single C call). The behavioral
    safety net behind it is the post-merge spec-branch suite run
    (W5.12); this gate is the syntactic one, catching the case
    where the merge agent's resolution produced something obviously
    broken before the suite would even start.
    """
    violations: List[Tuple[str, str, int]] = []
    for raw_path in paths:
        path = raw_path.strip()
        if not path:
            continue
        full = Path(worktree) / path
        try:
            text = full.read_text(errors="replace")
        except OSError:
            # File not present (deletion by the merge, or never
            # existed) — not a gate concern. Deletion can't carry
            # the breakage types this gate is designed to catch.
            continue

        # Marker check runs first — when markers AND syntax both fail,
        # reporting the marker is more actionable (operators know
        # exactly what to look for). One violation per file is enough
        # to refuse the merge.
        marker_line = _first_marker_line(text)
        if marker_line:
            violations.append((path, "conflict_marker", marker_line))
            continue

        if full.suffix.lower() == ".py":
            try:
                compile(text, str(full), "exec")
            except SyntaxError as exc:
                # ``SyntaxError.lineno`` is 1-based on the input source;
                # fall back to 1 when the compiler can't pinpoint one.
                line_no = int(getattr(exc, "lineno", 0) or 0) or 1
                violations.append((path, "syntax_error", line_no))
    return violations


def _try_auto_resolve_additive(
    wt,
    worktree: Path,
    ambiguous: List[str],
    file_resolutions: Dict[str, Dict[str, str]],
    side_texts: Dict[str, Tuple[str, str]],
) -> Optional[str]:
    """Auto-apply keep-both for every ambiguous file when the whole set
    clears the confidence gate: every file's hunk already classifies as
    ``"keep-both"`` AND both sides are pure additions against the merge
    base for every file (:func:`_is_pure_additive_both_sides`). All-or-
    nothing, like the rest of this module — one file failing the gate
    means the whole conflict still parks, same as today.

    Returns a rationale string (naming each file, both sides' line
    counts, and the non-overlap proof) when applied — files are already
    written and staged. Returns ``None`` when the gate wasn't met; no
    file has been touched in that case, so the caller falls straight
    through to the normal escalation path.
    """
    if not ambiguous:
        return None

    proofs: Dict[str, str] = {}
    for path in ambiguous:
        entry = file_resolutions.get(path)
        if not entry or entry.get("verdict") != "keep-both":
            return None
        if not _is_pure_additive_both_sides(wt, worktree, path):
            return None
        spec_text, task_text = side_texts.get(path, ("", ""))
        spec_n = len([l for l in spec_text.splitlines() if l.strip()])
        task_n = len([l for l in task_text.splitlines() if l.strip()])
        proofs[path] = (
            f"spec side added {spec_n} line(s), task side added {task_n} "
            f"line(s) — both pure additions vs. the merge base, 0 shared "
            f"lines between them"
        )

    # Gate cleared for every file. Apply, then syntax-check the result
    # before staging — a failure here means we don't guess further; the
    # merge is escalated and the caller's `git merge --abort` discards
    # whatever was written here.
    for path in ambiguous:
        if not _resolve_keep_both_in_file(worktree, path):
            return None
        if not _syntax_check(worktree, path):
            return None

    for path in ambiguous:
        wt._git("add", "--", path, cwd=worktree)

    lines = [
        "Auto-resolved as keep-both (no human reply needed): both sides "
        "made pure, non-overlapping additions against the merge base — no "
        "modified or deleted lines on either side — so there is exactly "
        "one safe combination.",
    ]
    for path, proof in proofs.items():
        lines.append(f"- `{path}`: {proof}")
    lines.append(
        "Applied both sides in file order and syntax-checked the result "
        "(py_compile for Python, parse for JSON; other types assumed "
        "fine). The post-merge spec-branch suite run (W5.12) is the "
        "behavioral safety net for this decision — this stays within the "
        "merge agent's existing authority (it already auto-resolves "
        "mechanical conflicts) on a semi-reversible spec-branch merge "
        "(reversibility.py)."
    )
    return "\n".join(lines)


# ------------------------------------------------------------------
# Guided resolution — a human replies to the blocking merge question
# ------------------------------------------------------------------
#
# Interpretation stays deterministic (keyword matching), consistent with
# ``classify_resolution`` — no LLM call, predictable and testable. When
# the reply doesn't map to a recognized action for every ambiguous file,
# nothing is applied and the merge is escalated again rather than
# guessed at.

_TASK_SIDE_PATTERNS = ("task side", "keep task", "accept task", "incoming", "keep the task")
_SPEC_SIDE_PATTERNS = ("spec side", "keep spec", "keep the spec", "keep existing", "discard task")
_KEEP_BOTH_PATTERNS = ("keep both", "merge both", "both sides", "combine both")
_MOVE_PATTERNS = ("move ", "own dir", "own directory", "its dir", "its directory")

# Conflict regions across a whole file (not just the first, unlike
# ``_extract_conflict_region`` which is display-oriented) — keep-both
# needs to rewrite every region in one pass.
_ALL_CONFLICT_REGIONS_RE = re.compile(
    r"<<<<<<<[^\n]*\n(.*?)\n=======\n(.*?)\n>>>>>>>[^\n]*\n?",
    re.DOTALL,
)


def _normalize_guidance(text: str) -> str:
    """Lowercase and collapse hyphens so ``keep-both`` and ``keep both``
    match the same pattern."""
    return re.sub(r"[-_]", " ", (text or "").lower())


def interpret_guidance(guidance: str, ambiguous_files: List[str]) -> Dict[str, str]:
    """Map a human's free-text reply to a per-file resolution action.

    Returns ``{path: action}`` for every file the guidance covers,
    where ``action`` is one of ``"task"`` (take the incoming task
    side), ``"spec"`` (keep the existing spec side), ``"keep-both"``
    (concatenate both sides), or ``"move"`` (keep the spec side in
    place and write the task side to its own file — for "keep both,
    move X to its own dir" replies where inline concatenation would
    produce an incoherent document).

    The same action applies uniformly to every ambiguous file — the
    guidance is not parsed per-file. Returns ``{}`` when the reply
    doesn't map to a recognized action; the caller must not guess.
    """
    text = _normalize_guidance(guidance)
    if not text.strip():
        return {}

    keep_both = any(p in text for p in _KEEP_BOTH_PATTERNS)
    wants_move = any(p in text for p in _MOVE_PATTERNS)

    if keep_both and wants_move:
        action = "move"
    elif any(p in text for p in _TASK_SIDE_PATTERNS):
        action = "task"
    elif any(p in text for p in _SPEC_SIDE_PATTERNS):
        action = "spec"
    elif keep_both:
        action = "keep-both"
    else:
        return {}

    return {path: action for path in ambiguous_files}


def _resolve_keep_both_in_file(worktree: Path, path: str) -> bool:
    """Strip conflict markers from ``path``, keeping both sides'
    content concatenated (spec above task) for every conflict region
    in the file. Returns False if the file has no conflict markers
    OR if the resulting Python (when ``path`` is a ``.py`` file)
    fails to parse — keep-both must compose both sides into valid
    code; if it can't, the file is restored to its conflicted state
    so the merge agent parks instead of committing broken code.

    The look-one-level-up rule from task 338: a "keep-both" that
    silently lands an unclosed dict (the historical fbce704e
    breakage) is worse than asking the human. The cheaper post-merge
    gate in :func:`_check_post_merge_files` is the multi-format
    safety net; this function applies the same rule eagerly for
    Python files because that's the file type whose breakage can
    break ``migrate`` / runtime and is the case operators actually
    hit.
    """
    full = Path(worktree) / path
    try:
        text = full.read_text(errors="replace")
    except OSError:
        return False

    def _combine(match: "re.Match") -> str:
        return match.group(1) + "\n" + match.group(2) + "\n"

    new_text, count = _ALL_CONFLICT_REGIONS_RE.subn(_combine, text)
    if count == 0:
        return False
    full.write_text(new_text)
    if full.suffix.lower() == ".py":
        try:
            compile(new_text, str(full), "exec")
        except SyntaxError:
            # Roll back so the file is back in its conflicted state —
            # the merge agent will see no resolution was applied and
            # park, and a human can resolve the genuine conflict
            # instead of debugging a half-applied keep-both.
            full.write_text(text)
            return False
    return True


def _resolve_move_to_own_dir(wt, worktree: Path, path: str, task_id: str) -> Optional[str]:
    """Keep the spec side's content at ``path``; write the task side's
    full file content to ``.proof/task-<task_id>/<basename>`` so both
    documents survive as independent files instead of being
    concatenated into one incoherent document (the historical failure
    mode this guards: two full-document rewrites of the same path —
    see task 206).

    Returns the destination path (relative to the worktree) on
    success, ``None`` if the task side's content can't be read or the
    spec side can't be restored.
    """
    show = wt._git("show", f"MERGE_HEAD:{path}", cwd=worktree)
    if show.returncode != 0:
        return None

    dest_rel = f".proof/task-{task_id}/{Path(path).name}"
    dest_full = Path(worktree) / dest_rel
    dest_full.parent.mkdir(parents=True, exist_ok=True)
    dest_full.write_text(show.stdout)

    ours = wt._git("checkout", "--ours", "--", path, cwd=worktree)
    if ours.returncode != 0:
        return None
    return dest_rel


def resolve_ambiguous_with_guidance(
    wt,
    worktree: Path,
    mechanical: List[str],
    ambiguous: List[str],
    task_brief: str,
    guidance: str,
    task_id: str = "",
) -> MergeResolution:
    """Apply a human's free-text guidance to resolve ambiguous conflicts.

    Captures the same per-file context ``resolve_conflicts_in_worktree``
    would (hunks, heuristic verdict, commit messages) so a follow-up
    question — if the guidance doesn't cover every file — carries the
    same detail as the original explain comment.

    Returns a resolved ``MergeResolution`` (files staged, ready to
    commit) when the guidance maps to a recognized action for every
    ambiguous file. Returns ``needs_human=True`` again — nothing
    staged, working tree untouched — when any file is left unresolved;
    the agent never guesses on a file the human didn't address.
    """
    conflict_hunks: Dict[str, str] = {}
    file_resolutions: Dict[str, Dict[str, str]] = {}
    spec_commit_messages: Dict[str, List[str]] = {}
    task_commit_messages: Dict[str, List[str]] = {}

    for path in ambiguous:
        full_region = _extract_conflict_region(worktree, path)
        if full_region:
            conflict_hunks[path] = _truncate_conflict_region(full_region, _HUNK_HEAD_MAX_LINES)
            spec_text, task_text = _split_hunk_sides(full_region)
            verdict, why = classify_resolution(spec_text, task_text)
            file_resolutions[path] = {"verdict": verdict, "rationale": why}
            spec_msgs = _commit_subjects_for_path(wt, worktree, "HEAD", path)
            task_msgs = _commit_subjects_for_path(wt, worktree, "MERGE_HEAD", path)
            if spec_msgs:
                spec_commit_messages[path] = spec_msgs
            if task_msgs:
                task_commit_messages[path] = task_msgs

    actions = interpret_guidance(guidance, ambiguous)
    unresolved = [path for path in ambiguous if path not in actions]

    if unresolved:
        return MergeResolution(
            needs_human=True,
            mechanical_files=mechanical,
            ambiguous_files=ambiguous,
            conflict_hunks=conflict_hunks,
            file_resolutions=file_resolutions,
            spec_commit_messages=spec_commit_messages,
            task_commit_messages=task_commit_messages,
            rationale=(
                f'Guidance "{guidance.strip()}" did not cover: '
                f"{', '.join(unresolved)}"
            ),
        )

    def _escalate(path: str, message: str) -> MergeResolution:
        return MergeResolution(
            needs_human=True,
            mechanical_files=mechanical,
            ambiguous_files=ambiguous,
            error=f"Failed to apply guided resolution for {path}: {message}",
            conflict_hunks=conflict_hunks,
            file_resolutions=file_resolutions,
            spec_commit_messages=spec_commit_messages,
            task_commit_messages=task_commit_messages,
        )

    applied: Dict[str, str] = {}

    for path in mechanical:
        checkout = wt._git("checkout", "--ours", "--", path, cwd=worktree)
        if checkout.returncode != 0:
            return _escalate(path, checkout.stderr.strip())
        wt._git("add", "--", path, cwd=worktree)
        applied[path] = "spec side (generated config)"

    for path, action in actions.items():
        add_paths = [path]
        if action == "task":
            result = wt._git("checkout", "--theirs", "--", path, cwd=worktree)
            ok = result.returncode == 0
            desc = "task side (per your guidance)"
        elif action == "spec":
            result = wt._git("checkout", "--ours", "--", path, cwd=worktree)
            ok = result.returncode == 0
            desc = "spec side (per your guidance)"
        elif action == "move":
            dest = _resolve_move_to_own_dir(wt, worktree, path, task_id)
            ok = dest is not None
            if ok:
                add_paths = [path, dest]
                desc = f"kept spec side at `{path}`, moved task's version to `{dest}`"
            else:
                desc = ""
        else:  # keep-both
            ok = _resolve_keep_both_in_file(worktree, path)
            desc = "kept both sides (markers removed, content concatenated)"

        if not ok:
            return _escalate(path, f"resolution action '{action}' failed")
        for add_path in add_paths:
            wt._git("add", "--", add_path, cwd=worktree)
        applied[path] = desc

    return MergeResolution(
        resolved=True,
        mechanical_files=list(applied.keys()),
        resolution_method=f'human-guided resolution — guidance: "{guidance.strip()}"',
        rationale="; ".join(f"`{path}`: {desc}" for path, desc in applied.items()),
        file_resolutions={
            path: {"verdict": actions.get(path, "mechanical")} for path in applied
        },
    )


def format_resolution_comment(
    branch: str,
    spec_branch: str,
    resolution: MergeResolution,
    diff_stat: Optional[str] = None,
) -> str:
    """Format the success comment for an auto-resolved merge conflict.

    Names every resolved file and includes the rationale so the
    operator can audit the agent's decision from the board UI.
    """
    lines = [
        f"Merge agent auto-resolved conflict merging `{branch}` into `{spec_branch}`:",
        "",
        f"Resolved files ({len(resolution.mechanical_files)}):",
    ]
    for path in resolution.mechanical_files:
        lines.append(f"- `{path}` — {resolution.resolution_method}")
    if resolution.rationale:
        lines.append("")
        lines.append(f"Rationale: {resolution.rationale}")
    if diff_stat:
        lines.append("")
        lines.append("```")
        lines.append(diff_stat)
        lines.append("```")
    return "\n".join(lines)


def _extract_conflict_region(worktree: Path, file_path: str) -> str:
    """Read the full, untruncated first conflict region from
    ``worktree/file_path``.

    This is the raw material both the display hunk
    (:func:`extract_conflict_hunk_text`) and the resolution heuristic
    (:func:`classify_resolution`) are derived from. Classification must
    see the *whole* region — truncating before classifying can make a
    large hunk's task side look artificially small or absent, which
    misreports a genuine modify/modify conflict as delete-vs-modify.

    Returns an empty string if the file is missing, unreadable, or has
    no conflict markers.
    """
    try:
        full = Path(worktree) / file_path
        text = full.read_text(errors="replace")
    except OSError:
        return ""

    lines = text.splitlines()
    region: List[str] = []
    in_conflict = False
    for line in lines:
        if line.startswith("<<<<<<<"):
            in_conflict = True
            region.append(line)
            continue
        if in_conflict:
            region.append(line)
            if line.startswith(">>>>>>>"):
                break
        elif region:
            # Already collected a region — stop scanning.
            break

    return "\n".join(region)


def _truncate_conflict_region(region_text: str, max_lines: int) -> str:
    """Truncate a full conflict region for display.

    Truncation is applied **per side** (spec vs task), not linearly
    across the whole region — a linear cap can run out before ever
    reaching the ``=======`` separator on a long hunk, which would drop
    the entire task side from the displayed text (regression: a
    166-line ``.proof/proof.md`` collision between two real tasks).
    """
    if not region_text:
        return ""
    region = region_text.splitlines()

    def _capped(side_lines: List[str], budget: int) -> List[str]:
        if len(side_lines) > budget:
            kept = side_lines[:budget]
            kept.append(f"... ({len(side_lines) - budget} more lines)")
            return kept
        return side_lines

    well_formed = region[0].startswith("<<<<<<<") and region[-1].startswith(">>>>>>>")
    sep_idx = None
    if well_formed:
        body = region[1:-1]
        sep_idx = next((i for i, l in enumerate(body) if l.startswith("=======")), None)

    if sep_idx is None:
        # Malformed/binary-ish or no separator captured — fall back to a
        # single linear cap so we still return *something*.
        return "\n".join(_capped(region, max_lines))

    spec_lines = body[:sep_idx]
    task_lines = body[sep_idx + 1:]
    per_side = max(1, max_lines // 2)
    out = [
        region[0],
        *_capped(spec_lines, per_side),
        "=======",
        *_capped(task_lines, per_side),
        region[-1],
    ]
    return "\n".join(out)


def extract_conflict_hunk_text(
    worktree: Path,
    file_path: str,
    max_lines: int = _HUNK_HEAD_MAX_LINES,
) -> str:
    """Read the first conflict region from ``worktree/file_path``,
    truncated per-side for display.

    During a failed merge the file on disk contains ``<<<<<<<`` /
    ``=======`` / ``>>>>>>>`` markers.  This helper pulls out the first
    such region so the report can show the divergent lines without
    forcing the operator to open the file.

    Returns an empty string if the file is missing or unreadable; the
    caller treats that as "no hunk available".
    """
    return _truncate_conflict_region(_extract_conflict_region(worktree, file_path), max_lines)


def _commit_subjects_for_path(
    wt, worktree: Path, ref: str, path: str, limit: int = _COMMIT_CONTEXT_LIMIT,
) -> List[str]:
    """Return up to ``limit`` commit subject lines that touched ``path``
    on ``ref``.

    ``ref`` is ``"HEAD"`` for the spec side or ``"MERGE_HEAD"`` for the
    task side — both refs exist while a conflicted merge is in progress,
    before the caller aborts it.  This is how the report answers "why" a
    side touched the file: the operator reads the side's own stated
    intent instead of the agent guessing it from the diff.

    Returns ``[]`` on any git failure (missing ref, mocked ``wt`` in
    tests, no real repo) — callers fall back to describing the diff
    content instead.
    """
    try:
        result = wt._git("log", f"-{limit}", "--format=%s", ref, "--", path, cwd=worktree)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _first_meaningful_line(text: str) -> str:
    """Return the first non-blank, non-marker line of ``text`` — the
    line that actually carries the operator's intent.  Trimmed to 80
    characters so it fits a single-line summary.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("#!", "<<<<<<<", "=======", ">>>>>>>")):
            continue
        return line[:80]
    return ""


def summarize_side(label: str, side_text: str) -> str:
    """Return a plain-English one-liner for one side of a conflict.

    ``label`` is ``"spec"`` or ``"task"`` (which side changed what);
    ``side_text`` is the half of the hunk between the marker lines.
    The output is short enough to fit in a single sentence — long
    content is truncated at ``_SIDE_SUMMARY_MAX_CHARS``.
    """
    body = (side_text or "").strip()
    if not body:
        return f"{label} side: empty (likely deletion)"

    lines = [_ for _ in body.splitlines() if _.strip()]
    if not lines:
        return f"{label} side: empty (likely deletion)"

    first = _first_meaningful_line(body)
    n_lines = len(lines)
    verb = "added" if n_lines >= 2 else "changed"

    summary = f"{label} side {verb} {n_lines} line{'s' if n_lines != 1 else ''}"
    if first:
        summary += f" — e.g. `{first}`"
    if len(summary) > _SIDE_SUMMARY_MAX_CHARS:
        summary = summary[: _SIDE_SUMMARY_MAX_CHARS - 1] + "…"
    return summary


def _normalize(text: str) -> str:
    """Collapse all whitespace runs (including indentation and inter-
    token spaces) so two sides that differ only by spacing compare
    equal under the resolution heuristic.  Preserves newlines between
    lines so token alignment still works downstream.
    """
    lines = (text or "").splitlines()
    return "\n".join(" ".join(line.split()) for line in lines).strip()


def _line_set(text: str) -> set:
    return {_ for _ in _normalize(text).splitlines() if _.strip()}


def _tokens(text: str) -> List[str]:
    """Split ``text`` into identifier-like tokens (letters/digits/underscore
    runs).  Used by :func:`classify_resolution` to detect when both sides
    of a conflict share the same leading identifier (e.g. ``def hello()``).
    """
    return [t for t in re.split(r"[\s\W]+", text) if t]


# Python declaration keywords whose *second* token is the identifier that
# matters for the resolution heuristic.  Without this carve-out, ``def
# helper_a()`` and ``def helper_b()`` would match on the keyword ``def``
# and be mis-classified as the same leading identifier.
_DECLARATION_KEYWORDS = frozenset({"def", "class", "import", "from", "async"})


def _leading_identifier(line: str) -> str:
    """Return the primary identifier of ``line`` for similarity checks.

    For ``def foo():`` returns ``foo`` (the function name); for
    ``class Bar:`` returns ``Bar``; for ``value = 1`` returns ``value``
    (the assignment target).  This is the identifier the operator reads
    first when eyeballing the conflict — same identifier on both sides
    means "same slot, pick one"; different identifiers means "different
    things added at the same place, keep both".
    """
    tokens = _tokens(line)
    if not tokens:
        return ""
    if tokens[0] in _DECLARATION_KEYWORDS and len(tokens) > 1:
        return tokens[1]
    return tokens[0]


def classify_resolution(spec_text: str, task_text: str) -> Tuple[str, str]:
    """Heuristically classify a conflict as ``"keep-both"`` or ``"choose"``.

    - ``keep-both`` — the two sides look complementary (disjoint lines
      or identical content).  The operator can confirm and accept both.
    - ``choose`` — the sides contradict each other (same lines changed
      to different values, or one side deleted what the other added).
      The operator must pick.

    Returns ``(verdict, rationale)``.  The rationale is short and
    designed to appear inline in the merge report.

    The heuristic is intentionally conservative — when in doubt it
    falls back to ``"choose"`` so the operator still gets a chance to
    review, rather than silently picking a winner.
    """
    spec = spec_text or ""
    task = task_text or ""

    if not spec.strip() and not task.strip():
        return "keep-both", "both sides empty"
    if not spec.strip() or not task.strip():
        return "choose", "one side empty (delete-vs-modify)"
    if _normalize(spec) == _normalize(task):
        return "keep-both", "sides identical after whitespace normalization"

    spec_lines = [l for l in _normalize(spec).splitlines() if l.strip()]
    task_lines = [l for l in _normalize(task).splitlines() if l.strip()]
    if not spec_lines or not task_lines:
        return "choose", "no overlapping content on either side"

    # If corresponding lines share their primary identifier (the
    # function/class/variable name the operator reads first), the
    # conflict is almost certainly a same-line replacement — surface
    # that as ``choose`` before falling back to the Jaccard ratio,
    # which would miss this case because the values differ character-
    # for-character.
    if len(spec_lines) == len(task_lines):
        all_pairs_similar = True
        for s_line, t_line in zip(spec_lines, task_lines):
            s_id = _leading_identifier(s_line)
            t_id = _leading_identifier(t_line)
            if not s_id or not t_id:
                continue
            if s_id != t_id:
                all_pairs_similar = False
                break
        if all_pairs_similar and spec_lines:
            return "choose", "corresponding lines share leading identifier (likely same code, different values)"

    # Fallback: line-set Jaccard.  High overlap → both sides touched
    # the same lines → contradictory.  Low overlap → disjoint additions
    # → complementary.
    spec_set = set(spec_lines)
    task_set = set(task_lines)
    overlap = len(spec_set & task_set)
    union = len(spec_set | task_set)
    overlap_ratio = overlap / union if union else 0

    if overlap_ratio >= 0.5:
        return "choose", f"both sides changed overlapping lines ({overlap} shared of {union})"

    # A low overlap ratio normally means two small, complementary
    # additions — safe to keep both.  But when each side is itself a
    # large, largely independent body of content (e.g. two full
    # document rewrites sharing the same path), "keep both" would
    # concatenate two incompatible documents rather than combine two
    # snippets.  That's never actually safe, no matter how little they
    # textually overlap — route large mutual rewrites to "choose".
    if len(spec_lines) > _LARGE_REWRITE_LINE_THRESHOLD and len(task_lines) > _LARGE_REWRITE_LINE_THRESHOLD:
        return "choose", (
            f"both sides substantially rewrote this file with unrelated "
            f"content ({len(spec_lines)} vs {len(task_lines)} lines, only "
            f"{overlap} shared) — too large to safely auto-combine"
        )
    return "keep-both", f"disjoint changes ({overlap} shared of {union} total lines)"


def _split_hunk_sides(hunk_text: str) -> Tuple[str, str]:
    """Split a conflict block into (spec_text, task_text).

    A conflict block looks like::

        <<<<<<< HEAD
        spec lines
        =======
        task lines
        >>>>>>> task/spec/157

    The ``HEAD`` branch in odin is the spec branch, so the text between
    ``<<<<<<<`` and ``=======`` is the spec side; the text between
    ``=======`` and ``>>>>>>>`` is the task side.
    """
    lines = (hunk_text or "").splitlines()
    spec_lines: List[str] = []
    task_lines: List[str] = []
    state = "head"  # head → before <<<<<<<; spec → between << and ==; task → between == and >>
    for line in lines:
        if line.startswith("<<<<<<<") or line.startswith("|||||||"):
            state = "spec"
            continue
        if line.startswith("======="):
            state = "task"
            continue
        if line.startswith(">>>>>>>") or line.startswith("|||||||"):
            state = "done"
            continue
        if state == "spec":
            spec_lines.append(line)
        elif state == "task":
            task_lines.append(line)
    return "\n".join(spec_lines), "\n".join(task_lines)


# Maps a classify_resolution rationale to a plain "why they collide"
# sentence.  Order matters — first substring match wins.
_COLLISION_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("both sides empty", "both sides removed this content — there's nothing left to keep here"),
    ("one side empty", "one side deleted this content while the other side kept modifying it"),
    ("share leading identifier", "both sides changed the same code to different values"),
    ("changed overlapping lines", "both sides edited the same lines, so git can't auto-merge them"),
    ("identical after whitespace", "the sides only differ by formatting, not real content"),
    (
        "disjoint changes",
        "both sides added different content at the same spot in the file — the "
        "additions themselves don't overlap, but git can't tell that automatically",
    ),
    (
        "substantially rewrote this file",
        "both sides independently rewrote this file with unrelated content, so "
        "there's no single combined version that makes sense",
    ),
    ("no overlapping content", "neither side's content lines up with the other, so git can't tell how to combine them"),
)


def _collision_reason(rationale: str) -> str:
    """Translate ``classify_resolution``'s technical rationale into a
    plain "why they collide" sentence a non-author can read cold.
    """
    rationale = rationale or ""
    for needle, phrase in _COLLISION_PHRASES:
        if needle in rationale:
            return phrase
    return rationale or "the two sides changed the same location in incompatible ways"


def _side_why(label: str, commit_subjects: Optional[List[str]], side_text: str, brief: str = "") -> str:
    """One-line plain-English explanation of what a side was doing and why.

    Prefers the side's own commit message (its actual stated intent) over
    guessing from the diff.  Falls back to the caller-supplied brief
    (e.g. the task title), then to a content-based summary as a last
    resort when no commit history was captured (legacy callers, mocked
    git in tests).
    """
    subjects = [s for s in (commit_subjects or []) if s.strip()]
    human_label = "Already on the shared branch" if label == "Spec" else "This task's change"
    if subjects:
        # Strip machine prefixes so a human reads intent, not plumbing.
        subject = subjects[0]
        for prefix in ("Auto-commit task ", "Merge task "):
            if subject.startswith(prefix):
                rest = subject[len(prefix):]
                subject = rest.split(": ", 1)[1] if ": " in rest else rest
        return f"{human_label}: {subject}"
    if brief:
        return f"{human_label}: {brief}"
    return summarize_side(label, side_text)


def _propose_resolution(verdict: str, rationale: str, path: str) -> Tuple[str, str]:
    """Return ``(proposal, question)`` for one conflicted file.

    ``proposal`` is the agent's recommendation with reasoning attached —
    never a bare verdict word.  ``question`` is the concrete, answerable
    question that closes the report for this file.
    """
    if verdict == "keep-both":
        proposal = (
            "keep both changes — they touch different, non-overlapping "
            "content, so combining them is safe."
        )
        question = f"Confirm keep-both for `{path}` (or tell us to drop one side)?"
        return proposal, question

    if "one side empty" in (rationale or ""):
        proposal = (
            "restore the modified side's content — the other side deleted "
            "work that this side was actively changing, and dropping it "
            "silently would lose that work."
        )
        question = (
            f"For `{path}`: keep the modified side (undo the deletion), or "
            f"was the deletion intentional (drop the change instead)?"
        )
        return proposal, question

    proposal = (
        "keep the task side's version — it's the incoming change meant to "
        "supersede this file here; the spec side's prior change would be "
        "discarded."
    )
    question = (
        f"For `{path}`: accept the task side (drop spec's change), keep "
        f"the spec side (drop task's change), or merge both by hand?"
    )
    return proposal, question


def format_merge_question(
    mechanical_files: List[str],
    ambiguous_files: List[str],
    task_brief: str = "",
    *,
    conflict_hunks: Optional[Dict[str, str]] = None,
    file_resolutions: Optional[Dict[str, Dict[str, str]]] = None,
    spec_commit_messages: Optional[Dict[str, List[str]]] = None,
    task_commit_messages: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Format a blocking question comment for an ambiguous merge conflict.

    The acceptance bar: a non-author can decide from the report in under
    a minute, from their chair, without opening files. For each
    conflicted file the report states what the spec side was doing and
    why, what the task side was doing and why (both grounded in the
    side's own commit messages/task brief when available), why the two
    collide, and the agent's proposed resolution with reasoning. Raw
    conflict hunks are collapsed into an appendix — evidence, not the
    headline — and the report always ends with the concrete question(s)
    the human must answer.
    """
    conflict_hunks = conflict_hunks or {}
    file_resolutions = dict(file_resolutions or {})
    spec_commit_messages = spec_commit_messages or {}
    task_commit_messages = task_commit_messages or {}

    # Preserve the order of ambiguous_files first, then any extra paths
    # a hunks-only caller supplied.
    ordered = list(ambiguous_files)
    for path in conflict_hunks:
        if path not in ordered:
            ordered.append(path)

    why_blocks: List[str] = []
    questions: List[str] = []
    raw_hunks: List[str] = []

    for path in ordered:
        hunk = conflict_hunks.get(path, "")
        if not hunk:
            why_blocks.append(f"- `{path}` — no conflict detail captured; open the file to inspect.")
            questions.append(f"- `{path}`: resolve manually, then re-run the merge.")
            continue

        spec_text, task_text = _split_hunk_sides(hunk)
        entry = file_resolutions.get(path)
        if entry is None:
            verdict, rationale = classify_resolution(spec_text, task_text)
        else:
            verdict, rationale = entry.get("verdict", "choose"), entry.get("rationale", "")

        spec_why = _side_why("Spec", spec_commit_messages.get(path), spec_text)
        task_why = _side_why("Task", task_commit_messages.get(path), task_text, brief=task_brief)
        collides = _collision_reason(rationale)
        proposal, question = _propose_resolution(verdict, rationale, path)

        why_blocks.append(
            f"- `{path}`\n"
            f"    - {spec_why}\n"
            f"    - {task_why}\n"
            f"    - Why they collide: {collides}\n"
            f"    - Proposed resolution: **{verdict}** — {proposal}"
        )
        questions.append(f"- `{path}`: {question}")
        hunk_lines = "\n".join(f"      {line}" for line in hunk.splitlines())
        raw_hunks.append(f"  `{path}`:\n{hunk_lines}")

    lines = [
        "This merge needs you. Two changes collided and I can't pick "
        "safely on my own. Reply with your choice below and I'll finish "
        "the merge — no need to touch git.",
    ]

    if why_blocks:
        lines.append("")
        lines.append(f"What collided ({len(ordered)} file{'s' if len(ordered) != 1 else ''}):")
        lines.extend(why_blocks)

    if mechanical_files:
        lines.append("")
        lines.append(
            f"Auto-resolvable (generated-config files, held pending your decision — "
            f"{len(mechanical_files)}):"
        )
        for path in mechanical_files:
            lines.append(f"- `{path}`")

    if raw_hunks:
        lines.append("")
        lines.append("Conflict regions (raw, for reference):")
        lines.extend(raw_hunks)

    lines.append("")
    lines.append(
        "If you'd rather do it by hand: fix the files in the worktree and "
        "say \"re-merge\" in a reply."
    )

    if task_brief:
        lines.append("")
        lines.append(f"Task brief: {task_brief}")

    lines.append("")
    lines.append("Your call — one line per file is enough (e.g. \"keep-both for everything\"):")
    lines.extend(questions or ["- Fix the files in the worktree, then reply \"re-merge\"."])

    return "\n".join(lines)
