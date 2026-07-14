"""Warm-start doc suggestions injected into task prompts.

At prompt-build time the orchestrator calls :func:`suggest_warm_start_docs` with
the task's title + description. The call scans the repo's docs tree under the
working directory and scores each entry against the query, returning up to
three repo-relative paths with a one-line reason each.

The corpus has four sources, broadened in task #242 to lift coverage:
  * ``docs/breadcrumb_analysis/_INDEX.md`` — flow-table rows + quick-nav
    symptom lines (the questions agents burn tokens rediscovering)
  * every other ``.md`` under ``breadcrumb_analysis/`` — each file's own H1/H2
    headings + first paragraph (files beyond the index rows)
  * ``docs/patterns/*.md`` — slug + tags + headings + summary
  * ``docs/wiki/<cat>/TOC.md`` — one-liner per entry (title + summary + tags)

Text matching reuses the SAME dependency-free TF-IDF + cosine scoring the
Memory "twins" service uses (``taskit-backend/tasks/similarity.py``): identical
tokenization regex, stopword set, smoothed IDF, and cosine. odin cannot import
that module (it lives behind a Django app import), so the pure-text primitives
are mirrored here byte-for-byte and must stay in sync. The structural signals
the twins scorer adds (same spec / assignee / files-touched) are task-to-task
signals with no doc analog, so only the text half is reused.

:func:`coverage_curve` replays a corpus of historical briefs across a range of
floors offline so the floor can be tuned against measured coverage rather than
guessed — see ``run_warm_start_replay.py``.

Paths only — the worktree already has the files, so the prompt pays near-zero
tokens. A task that matches nothing gets no section (no filler).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------
# Dependency-free text matching — mirrors taskit-backend/tasks/similarity.py.
# Kept in sync deliberately; do not diverge (no second matcher).
# ---------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "to", "of", "in", "on", "at", "for", "with", "as", "by", "this",
    "that", "it", "from", "into", "not", "no", "so", "if", "then", "than",
    "we", "you", "will", "should", "can", "could", "would", "do", "does",
    "did", "task", "tasks", "issue",
})


def _tokenize(text: str) -> List[str]:
    return [
        t for t in _TOKEN_RE.findall((text or "").lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def _build_idf(token_lists: List[List[str]]):
    """Classic smoothed IDF: log((N+1)/(df+1)) + 1, N = corpus size."""
    n = len(token_lists)
    df: Dict[str, int] = {}
    for tokens in token_lists:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1
    default_idf = math.log(n + 1) + 1  # weight for a term unseen in the corpus (df=0)
    idf = {t: math.log((n + 1) / (d + 1)) + 1 for t, d in df.items()}
    return idf, default_idf


def _tfidf_vector(tokens: List[str], idf: Dict[str, float], default_idf: float) -> Dict[str, float]:
    vec: Dict[str, float] = {}
    for t in tokens:
        vec[t] = vec.get(t, 0.0) + idf.get(t, default_idf)
    return vec


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = a.keys() & b.keys()
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------
# Corpus construction
# ---------------------------------------------------------------------

MAX_SUGGESTIONS = 3
# Below this cosine the match is too thin to act on. Twins uses ``score > 0``;
# the broadened corpus (task #242: breadcrumb file headings + wiki TOC + pattern
# H2s, ~98 entries vs ~15) made the old 0.05 floor match ~98% of briefs — forced
# matches are noise. The replay (run_warm_start_replay.py) swept the floor over
# 95 real historical brief titles: 0.12 yields 53.7% coverage with ~85% relevance
# on the spot-check, sitting mid-target (30-60%). Real briefs carry descriptions
# too (more shared terms → higher scores), so live coverage should land slightly
# above the titles-only figure. Re-measure per W7.4 once the read-rate query runs.
MIN_SCORE = 0.12

# ``docs_root`` is the repo's ``docs/`` directory; the subdirs scanned live
# directly under it. Emitted paths are repo-relative (prefixed with ``docs/``)
# so they resolve from the agent's worktree root.
_BC_SCAN_DIR = Path("breadcrumb_analysis")
_BC_INDEX = _BC_SCAN_DIR / "_INDEX.md"
_PATTERNS_SCAN_DIR = Path("patterns")
_WIKI_SCAN_DIR = Path("wiki")
_BC_EMIT = Path("docs") / "breadcrumb_analysis"
_PATTERNS_EMIT = Path("docs") / "patterns"
_WIKI_EMIT = Path("docs") / "wiki"

# Flows-table row: ``| `folder/` | description |``
_FLOW_ROW_RE = re.compile(r"^\|\s*`([a-z0-9_-]+)/`\s*\|\s*(.+?)\s*\|\s*$")
# Quick-nav line: ``- **symptom?** → `folder/sub/File.md```  (symptom may end in ?)
_QUICKNAV_RE = re.compile(r"^\-\s*\*\*([^*]+?)\*\*.*?→\s*`([^`]+)`")
# Wiki TOC entry: ``- [Title](file.md) — summary. tags: a, b``
_WIKI_TOC_RE = re.compile(r"^-\s*\[([^\]]+)\]\(([^)]+)\)\s*[—-]\s*(.+)$")
# Markdown H1/H2 heading.
_HEADING_RE = re.compile(r"^#{1,2}\s+(.+)$")
# Frontmatter-ish lines to skip when mining prose.
_FM_PREFIXES = ("tags:", "source:", "author:", "fetched:", "status:")
_REASON_MAX = 100


def _md_headings_and_summary(body: str):
    """Mine a markdown body for topic-dense text: H1/H2 headings + first prose
    paragraph. Frontmatter, table rows, code fences, and H3+ are skipped.

    Returns ``(headings, first_paragraph)`` — both strings, either possibly empty.
    Headings dominate the TF-IDF signal (they are the file's own vocabulary);
    the first paragraph adds the one-line summary a symptom row would carry.
    """
    headings: List[str] = []
    first_para: List[str] = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            if first_para:
                break
            continue
        if s.startswith(_FM_PREFIXES):
            continue
        m = _HEADING_RE.match(s)
        if m:
            headings.append(m.group(1).strip())
            continue
        if s.startswith("#") or s.startswith("|") or s.startswith("```"):
            continue
        first_para.append(s)
        if len(first_para) >= 2:
            break
    return headings, " ".join(first_para)


def _clip(text: str, limit: int = _REASON_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _merge_entry(corpus: Dict[str, dict], path: str, text: str, reason: str) -> None:
    """Add text to a corpus entry, keeping the first reason per path."""
    entry = corpus.get(path)
    if entry is None:
        corpus[path] = {"path": path, "text": text, "reason": _clip(reason)}
    else:
        if text and text not in entry["text"]:
            entry["text"] = (entry["text"] + " " + text).strip()
        if not entry["reason"] and reason:
            entry["reason"] = _clip(reason)


def _breadcrumb_corpus(docs_root: Path) -> Dict[str, dict]:
    """Parse ``docs/breadcrumb_analysis/_INDEX.md`` into corpus entries.

    Two sources, merged per path:
      * the Flows table (``| `folder/` | what it traces |``) → ``<folder>/FLOW.md``
      * the Quick-navigation symptom lines → the exact file they cite

    Symptom lines carry the questions agents burn tokens rediscovering, so they
    dominate the matching signal; the table rows add folder-level context.
    """
    index_path = docs_root / _BC_INDEX
    if not index_path.is_file():
        return {}
    corpus: Dict[str, dict] = {}
    section = ""
    in_flows_table = False
    for raw in index_path.read_text(errors="replace").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("### "):
            section = stripped[4:].strip()
            in_flows_table = False
            continue
        if stripped.startswith("## "):
            in_flows_table = stripped[2:].strip().lower().startswith("flows")
            continue

        m = _FLOW_ROW_RE.match(line)
        if m:
            folder, desc = m.group(1), m.group(2)
            _merge_entry(
                corpus,
                str(_BC_EMIT / folder / "FLOW.md"),
                f"{folder} {desc}",
                desc,
            )
            continue

        m = _QUICKNAV_RE.match(line)
        if m:
            symptom, rel_path = m.group(1).strip(), m.group(2).strip()
            path = str(_BC_EMIT / rel_path)
            text = f"{symptom} {section} {rel_path}"
            _merge_entry(corpus, path, text, symptom)
            continue
    return corpus


def _patterns_corpus(docs_root: Path) -> Dict[str, dict]:
    """Scan ``docs/patterns/*.md``; one entry per file from slug + tags + headings + summary."""
    patterns_dir = docs_root / _PATTERNS_SCAN_DIR
    if not patterns_dir.is_dir():
        return {}
    corpus: Dict[str, dict] = {}
    for md in sorted(patterns_dir.glob("*.md")):
        body = md.read_text(errors="replace")
        tags = ""
        for ln in body.splitlines():
            s = ln.strip()
            if s.startswith("tags:") and not tags:
                tags = s[len("tags:"):].strip()
                break
        headings, first_para = _md_headings_and_summary(body)
        slug = md.stem.replace("-", " ").replace("_", " ")
        reason = headings[0] if headings else slug
        text = " ".join(p for p in (slug, tags, " ".join(headings), first_para) if p)
        _merge_entry(corpus, str(_PATTERNS_EMIT / md.name), text, reason)
    return corpus


def _breadcrumb_files_corpus(docs_root: Path) -> Dict[str, dict]:
    """Walk every ``.md`` under ``docs/breadcrumb_analysis/`` (except the index)
    and index its own H1/H2 headings + first paragraph.

    The ``_INDEX.md`` parser only sees the symptom/table rows; the individual
    FLOW/DETAILS/DEBUG files carry richer topic vocabulary (their headings and
    summaries) that no symptom line reproduces. This is the primary coverage
    lever: files beyond the index rows become matchable. Entries merge by path
    with any the index parser already created.
    """
    bc_dir = docs_root / _BC_SCAN_DIR
    if not bc_dir.is_dir():
        return {}
    corpus: Dict[str, dict] = {}
    for md in sorted(bc_dir.rglob("*.md")):
        if md.name == "_INDEX.md":
            continue
        rel = md.relative_to(bc_dir)
        headings, first_para = _md_headings_and_summary(md.read_text(errors="replace"))
        if not headings and not first_para:
            continue
        reason = headings[0] if headings else str(rel)
        text = " ".join(headings + [first_para]) if first_para else " ".join(headings)
        _merge_entry(corpus, str(_BC_EMIT / rel), text, reason)
    return corpus


def _wiki_corpus(docs_root: Path) -> Dict[str, dict]:
    """Index ``docs/wiki/<category>/TOC.md`` one-liners.

    Each TOC row is ``- [Title](file.md) — one-line summary. tags: a, b`` — the
    cheapest, highest-signal text in the wiki (title + summary + tags). Entries
    resolve to the per-entry file path so the agent can read the full entry.
    """
    wiki_dir = docs_root / _WIKI_SCAN_DIR
    if not wiki_dir.is_dir():
        return {}
    corpus: Dict[str, dict] = {}
    for toc in sorted(wiki_dir.rglob("TOC.md")):
        cat = toc.parent.relative_to(wiki_dir)
        for raw in toc.read_text(errors="replace").splitlines():
            m = _WIKI_TOC_RE.match(raw.strip())
            if not m:
                continue
            title, file_name, rest = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            tags = ""
            tm = re.search(r"tags:\s*(.+)$", rest)
            if tm:
                tags = tm.group(1).strip()
                summary = re.sub(r"\s*tags:\s*.+$", "", rest).strip()
            else:
                summary = rest
            text = " ".join(p for p in (title, summary, tags) if p)
            _merge_entry(corpus, str(_WIKI_EMIT / cat / file_name), text, title)
    return corpus


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def _build_corpus(docs_root) -> List[dict]:
    """Build the full matchable corpus (list of ``{path, text, reason}``)."""
    root = Path(docs_root) if docs_root is not None else None
    if root is None or not root.is_dir():
        return []
    corpus_map: Dict[str, dict] = {}
    corpus_map.update(_breadcrumb_corpus(root))
    corpus_map.update(_breadcrumb_files_corpus(root))
    corpus_map.update(_patterns_corpus(root))
    corpus_map.update(_wiki_corpus(root))
    return list(corpus_map.values())


def _score_entries(title: str, description: str, entries: List[dict]) -> List[dict]:
    """Score a brief against every corpus entry. Returns ``[{path, reason,
    score}]`` sorted descending. Entries with zero term-overlap (cosine 0.0)
    are dropped — a zero score is a non-match, not a weak one.

    Title is short and topic-dense; weight it 2x by repetition (same trick the
    twins scorer uses) so the brief's framing dominates the body's noise.
    """
    if not entries:
        return []
    idf, default_idf = _build_idf([_tokenize(e["text"]) for e in entries])
    query_tokens = _tokenize(title) * 2 + _tokenize(description)
    if not query_tokens:
        return []
    query_vec = _tfidf_vector(query_tokens, idf, default_idf)
    scored: List[dict] = []
    for entry in entries:
        vec = _tfidf_vector(_tokenize(entry["text"]), idf, default_idf)
        score = _cosine(query_vec, vec)
        if score <= 0.0:
            continue
        scored.append({"path": entry["path"], "reason": entry["reason"], "score": round(score, 4)})
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored


def suggest_warm_start_docs(
    title: str,
    description: str,
    docs_root,
    *,
    min_score: Optional[float] = None,
) -> List[dict]:
    """Return up to :data:`MAX_SUGGESTIONS` doc suggestions for a task brief.

    Each suggestion is ``{"path", "reason", "score"}``, best first. Returns
    ``[]`` when the docs tree is absent or nothing clears the floor.

    ``docs_root`` is the ``docs/`` directory of the repo the agent will run in
    (i.e. ``<working_dir>/docs``). Emitted paths are repo-relative so they
    resolve correctly from the worktree root.

    ``min_score`` overrides :data:`MIN_SCORE` so the offline replay harness can
    sweep the floor; production calls omit it and get the tuned default.
    """
    if min_score is None:
        min_score = MIN_SCORE
    entries = _build_corpus(docs_root)
    if not entries:
        return []
    scored = _score_entries(title, description, entries)
    matched = [s for s in scored if s["score"] >= min_score]
    return matched[:MAX_SUGGESTIONS]


def score_all(title: str, description: str, docs_root) -> List[dict]:
    """Score a brief against every corpus entry, unfiltered by floor or cap.

    Used by the replay harness (:func:`coverage_curve`) so the corpus is parsed
    once and many floors can be evaluated without re-reading the docs tree.
    """
    return _score_entries(title, description, _build_corpus(docs_root))


def coverage_curve(briefs: List[dict], docs_root, floors: List[float]) -> dict:
    """Replay a corpus of briefs and return the match-coverage curve.

    ``briefs`` is a list of ``{"id", "title", "description?"}``. For each brief
    the best (highest) score across all corpus entries is recorded; coverage at
    a floor is the fraction of briefs whose best score clears it. The corpus is
    parsed exactly once.

    Returns ``{"curve": [{floor, matched, total, coverage}], "results": [...]}``
    where each result carries its top suggestion (or ``None`` when nothing
    matched even at floor 0).
    """
    entries = _build_corpus(docs_root)
    results: List[dict] = []
    for brief in briefs:
        scored = _score_entries(brief.get("title", ""), brief.get("description", ""), entries)
        top = scored[0] if scored else None
        results.append({
            "id": brief.get("id"),
            "title": brief.get("title", ""),
            "max_score": top["score"] if top else 0.0,
            "top": top,
        })
    total = len(results)
    curve = [
        {
            "floor": f,
            "matched": sum(1 for r in results if r["top"] is not None and r["max_score"] >= f),
            "total": total,
            "coverage": round(
                sum(1 for r in results if r["top"] is not None and r["max_score"] >= f) / total, 3
            )
            if total
            else 0,
        }
        for f in floors
    ]
    return {"curve": curve, "results": results}


def relevance_sample(results: List[dict], k: int = 10, floor: Optional[float] = None) -> List[dict]:
    """Pick up to ``k`` matched results (those with a top suggestion) for the
    relevance spot-check in the replay proof, strongest matches first."""
    if floor is not None:
        pool = [r for r in results if r["top"] is not None and r["max_score"] >= floor]
    else:
        pool = [r for r in results if r["top"] is not None]
    pool.sort(key=lambda r: r["max_score"], reverse=True)
    return pool[:k]


def format_warm_start_section(suggestions: List[dict]) -> str:
    """Render suggestions as a prompt section. Empty string when none.

    Paths only — the agent reads them from its worktree. The header is
    omitted entirely on a no-match so the prompt never carries filler.
    """
    if not suggestions:
        return ""
    lines = ["## Warm start (read these first)"]
    seen = set()
    for s in suggestions:
        path = s["path"]
        if path in seen:
            continue
        seen.add(path)
        lines.append(f"- `{path}` — {s['reason']}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"
