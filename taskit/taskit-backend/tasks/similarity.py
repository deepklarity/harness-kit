"""Memory: find a task's closest finished "twins" on the same board.

Dependency-free similarity — no embeddings service, runs entirely offline
against the DB. Scores candidates on TF-IDF-weighted token overlap between
title/description, plus small structural boosts (same spec, same assignee,
overlapping files touched per the merge diff stat). This is the read path
(`find_twins`) used by the task-detail API and the write path
(`post_twins_comment`) used at dispatch time.
"""

import math
import re
from collections import Counter

from .models import Task, TaskStatus

# A task is a candidate "twin" once it has run its course — DONE, FAILED and
# CANCELED all carry a real cost/outcome signal worth surfacing; anything
# still in flight is excluded.
FINISHED_STATUSES = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELED)

DEFAULT_LIMIT = 3

# Weights sum to 1.0 at the maximum (perfect text match + every structural
# boost), so `score` stays a comparable, boundable number.
TEXT_WEIGHT = 0.6
SAME_SPEC_WEIGHT = 0.15
SAME_AGENT_WEIGHT = 0.05
FILES_OVERLAP_WEIGHT = 0.20

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "to", "of", "in", "on", "at", "for", "with", "as", "by", "this",
    "that", "it", "from", "into", "not", "no", "so", "if", "then", "than",
    "we", "you", "will", "should", "can", "could", "would", "do", "does",
    "did", "task", "tasks", "issue",
})

# git `diff --stat` lines look like " src/foo.py | 12 ++++++------" — the
# file path is whatever precedes the ` | <count>` column.
_DIFFSTAT_FILE_RE = re.compile(r"^\s*(\S+)\s+\|\s+\d+", re.MULTILINE)


def _tokenize(text):
    return [
        t for t in _TOKEN_RE.findall((text or "").lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def _task_tokens(task):
    # Titles are short and topic-dense; weight them 2x relative to the body
    # by simple repetition rather than maintaining two separate vectors.
    return _tokenize(task.title) * 2 + _tokenize(task.description)


def _files_touched(task):
    diff_stat = (task.metadata or {}).get("diff_stat")
    if not isinstance(diff_stat, str) or not diff_stat.strip():
        return frozenset()
    return frozenset(_DIFFSTAT_FILE_RE.findall(diff_stat))


def _build_idf(token_lists):
    """Classic smoothed IDF: log((N+1)/(df+1)) + 1, N = corpus size."""
    n = len(token_lists)
    df = Counter()
    for tokens in token_lists:
        for t in set(tokens):
            df[t] += 1
    default_idf = math.log(n + 1) + 1  # weight for a term unseen in the corpus (df=0)
    idf = {t: math.log((n + 1) / (d + 1)) + 1 for t, d in df.items()}
    return idf, default_idf


def _tfidf_vector(tokens, idf, default_idf):
    tf = Counter(tokens)
    return {t: c * idf.get(t, default_idf) for t, c in tf.items()}


def _cosine(vec_a, vec_b):
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a) & set(vec_b)
    if not common:
        return 0.0
    dot = sum(vec_a[t] * vec_b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _proof_path(task_id):
    # Matches the canonical per-task proof convention (odin/src/odin/promote_check.py).
    return f".proof/task-{task_id}/proof.md"


def _serialize_twin(candidate, score, text_score, structural_score, *, warning=None):
    from .execution_processing import compute_usage_from_trace

    usage = compute_usage_from_trace(candidate) or {}
    metadata = candidate.metadata or {}
    return {
        "task_id": candidate.id,
        "title": candidate.title,
        "outcome": candidate.status,
        "tokens": usage.get("total_tokens"),
        "duration_ms": metadata.get("last_duration_ms"),
        "redo_rounds": int(metadata.get("rework_count", 0) or 0),
        "agent": candidate.assignee.name if candidate.assignee_id else None,
        "model": candidate.model_name,
        "score": round(score, 4),
        "text_score": round(text_score, 4),
        "structural_score": round(structural_score, 4),
        "proof_path": _proof_path(candidate.id),
        "warning": warning,
    }


def find_twins(task, limit=DEFAULT_LIMIT):
    """Rank finished tasks on the same board by similarity to `task`.

    Returns up to `limit` twins, highest score first. Returns [] when there
    is no board history or nothing scores above zero — the feature degrades
    silently instead of surfacing unrelated tasks.
    """
    if task.board_id is None:
        return []

    candidates = list(
        Task.objects.filter(board_id=task.board_id, status__in=FINISHED_STATUSES)
        .exclude(id=task.id)
        .select_related("assignee")
    )
    if not candidates:
        return []

    corpus = [_task_tokens(c) for c in candidates]
    idf, default_idf = _build_idf(corpus)
    query_vec = _tfidf_vector(_task_tokens(task), idf, default_idf)
    task_files = _files_touched(task)

    scored = []
    for candidate, cand_tokens in zip(candidates, corpus):
        cand_vec = _tfidf_vector(cand_tokens, idf, default_idf)
        text_score = _cosine(query_vec, cand_vec)

        structural_score = 0.0
        if task.spec_id and candidate.spec_id and task.spec_id == candidate.spec_id:
            structural_score += SAME_SPEC_WEIGHT
        if task.assignee_id and candidate.assignee_id and task.assignee_id == candidate.assignee_id:
            structural_score += SAME_AGENT_WEIGHT
        cand_files = _files_touched(candidate)
        if task_files and cand_files:
            overlap = len(task_files & cand_files) / len(task_files | cand_files)
            structural_score += overlap * FILES_OVERLAP_WEIGHT

        score = text_score * TEXT_WEIGHT + structural_score
        if score > 0:
            scored.append((score, text_score, structural_score, candidate))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    top = scored[:limit]

    # Wave-5 mistakes ledger (task #223): surface each twin's latest failure
    # as a warning so a similar new task learns from it. Batch-loaded to
    # avoid an N+1 (limit is small, but the contract holds regardless).
    from .mistakes import latest_mistake_for_tasks, warning_for_twin
    mistake_map = latest_mistake_for_tasks([c.id for _, _, _, c in top])
    return [
        _serialize_twin(
            candidate, score, text_score, structural_score,
            warning=warning_for_twin(mistake_map.get(candidate.id)),
        )
        for score, text_score, structural_score, candidate in top
    ]


def format_twins_comment(twins, *, estimate=None):
    """Render twins as one compact markdown comment body.

    Twins carrying a ``warning`` (from the mistakes ledger) get a
    continuation line so a similar new task sees "tasks like this failed
    on X before — check it first" without a separate comment.

    If `estimate` is provided, the quote line is appended at the end —
    same comment, not a second one (acceptance: "post the quote in the
    same dispatch comment as the twins").
    """
    lines = ["**Memory — closest finished twins:**"]
    for i, twin in enumerate(twins, start=1):
        tokens = twin["tokens"] if twin["tokens"] is not None else "—"
        duration_ms = twin["duration_ms"]
        duration = f"{duration_ms / 1000:.0f}s" if duration_ms is not None else "—"
        agent = twin["agent"] or "—"
        lines.append(
            f"{i}. #{twin['task_id']} \"{twin['title']}\" — {twin['outcome']} · "
            f"{tokens} tokens · {duration} · {twin['redo_rounds']} redo(s) · {agent} "
            f"(match {twin['score']:.2f}) · proof: {twin['proof_path']}"
        )
        warning = twin.get("warning")
        if warning:
            cls = warning.get("failure_class")
            tag = f"({cls}): " if cls else ""
            lines.append(
                f"   -> warning: tasks like this failed before — {tag}{warning['one_liner']}"
            )
    if estimate is not None:
        from .estimation import format_estimate_line
        lines.append(format_estimate_line(estimate))
    return "\n".join(lines)


def post_twins_comment(task, *, author_email="odin+memory@system", author_label="memory"):
    """Post exactly one twins comment for this dispatch, then return the twins.

    The same comment carries the quote (estimate) line — never a second
    one. The estimate is also stamped on `task.metadata["estimate"]` so
    DONE / auto-promotion paths can pair it with the actual cost.

    Idempotent in two layers:

    1. Per dispatch — `metadata.active_execution.run_token` guards a
       repeated call for the same dispatch (a Celery double-fire).
    2. Per content — a retry whose board history didn't change produces a
       byte-identical card, so it is suppressed by ``has_identical_comment``.
       The card re-posts only when the twin set actually changes (a new
       finished relative, a moved assignee), not on every attempt.

    Returns [] and posts nothing when there is no history — the caller
    (executor dispatch path) treats that as a silent no-op. With no twins
    there is no quote to post, and we never invent one.
    """
    from .comment_dedup import has_identical_comment

    metadata = task.metadata or {}
    run_token = (metadata.get("active_execution") or {}).get("run_token")
    if run_token and metadata.get("twins_posted_for_run_token") == run_token:
        return []

    twins = find_twins(task)

    if run_token:
        new_metadata = {**metadata, "twins_posted_for_run_token": run_token}
        Task.objects.filter(id=task.id).update(metadata=new_metadata)
        task.metadata = new_metadata

    if not twins:
        return []

    from .estimation import compute_estimate, stamp_estimate
    estimate = compute_estimate(twins)
    body = format_twins_comment(twins, estimate=estimate)

    # A retry that changed nothing on the board produces the same card —
    # don't bury the page under copies. Re-stamp the estimate (it is
    # derived from the same twins) but skip the duplicate post.
    if has_identical_comment(task, body, author_label=author_label):
        stamp_estimate(task, estimate)
        return twins

    stamp_estimate(task, estimate)

    from .models import CommentType, TaskComment

    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email=author_email,
        author_label=author_label,
        content=body,
        comment_type=CommentType.STATUS_UPDATE,
    )
    return twins
