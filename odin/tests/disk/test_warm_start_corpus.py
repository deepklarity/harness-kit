"""Tests for the broadened warm-start corpus — task #242.

Covers the three corpus-breadth additions plus the configurable match floor:
  - breadcrumb flow files' own H1/H2 headings + first paragraph are indexed
    (not just the _INDEX.md symptom/table rows)
  - docs/wiki category TOC one-liners are indexed
  - patterns pick up H2 heading text
  - suggest_warm_start_docs accepts a ``min_score`` override so the replay
    harness can sweep the floor

Tags:
- [disk] — reads a tmp_path docs tree; no network, no subprocesses
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from odin import warm_start as w


# ---------------------------------------------------------------------
# Fixture: a docs tree with breadcrumb flow files + wiki TOC + patterns
# ---------------------------------------------------------------------

_INDEX_MD = """# Breadcrumb Analyses

Workflow traces.

## Flows

| Folder | What it traces |
|--------|---------------|
| `token-economy/` | Token profiling: finding vs doing vs checking, measured per task. |

## Quick navigation

### Cost
- **Token count shows 0 or ---?** → `token-economy/DEBUG.md`
"""

# A FLOW.md whose H1 + summary are NOT mirrored verbatim in _INDEX symptoms.
# The _INDEX table row mentions the folder; the file's own headings carry the
# richer topic words ("finding", "doing", "checking", "attribution").
_BC_FLOW = """# Token Profiling Flow

Finding vs doing vs checking, decomposed per task. Reads harness tool-call
inputs and buckets each call into find/do/check/other to get an honest
finding-share number.

## Per-bucket attribution
## Reconciliation against cost rows
"""

# A DETAILS.md that is NOT referenced anywhere in _INDEX — only the file walk
# discovers it. Proves the corpus grew beyond index rows.
_BC_DETAILS = """# Token attribution detail

Maps the harness JSONL token fields onto backend cost columns, including
the cached-vs-uncached split and the tokenizer-inflation correction.
"""

_WIKI_TOC = """# Token economics — TOC

- [Prompt caching essentials](prompt-caching-essentials.md) — Caching is automatic but fragile; every action that changes your prefix recomputes the full request. tags: prompt-caching, cache-invalidation, token-economy
- [Slash your Claude token usage](slash-token-usage.md) — Edit prompts instead of following up, start fresh chats every 15 messages with a summary. tags: token-economy, context-compression, memory-management
"""

_PATTERN = """tags: cost-tracking, dispatch, graceful-degradation

# Bookkeeping Never Kills the Run

Cost tracking and metadata writes are best-effort.

## Failure isolation
A metadata write failure must never gate the executor — the run always finishes.
"""


@pytest.fixture
def docs_root(tmp_path):
    root = tmp_path / "docs"
    bc = root / "breadcrumb_analysis" / "token-economy"
    bc.mkdir(parents=True)
    (root / "breadcrumb_analysis" / "_INDEX.md").write_text(_INDEX_MD)
    (bc / "FLOW.md").write_text(_BC_FLOW)
    (bc / "DETAILS.md").write_text(_BC_DETAILS)

    wiki_cat = root / "wiki" / "token-economics"
    wiki_cat.mkdir(parents=True)
    (wiki_cat / "TOC.md").write_text(_WIKI_TOC)

    pat = root / "patterns"
    pat.mkdir(parents=True)
    (pat / "bookkeeping-never-kills-the-run.md").write_text(_PATTERN)
    return root


# ---------------------------------------------------------------------
# Corpus breadth — breadcrumb flow headings/summaries
# ---------------------------------------------------------------------

class TestBreadcrumbFlowHeadings:
    def test_flow_file_heading_enriches_match(self, docs_root):
        """A brief phrased after the FLOW.md summary (not the _INDEX symptom)
        lands on the FLOW.md because the file's own headings are now indexed."""
        suggestions = w.suggest_warm_start_docs(
            "Finding vs doing vs checking, measured per task",
            "Decompose tool-call inputs into find/do/check buckets for an honest finding-share.",
            docs_root,
        )
        assert suggestions, "expected a match from the flow-file heading"
        paths = [s["path"] for s in suggestions]
        assert any("token-economy" in p and p.endswith("FLOW.md") for p in paths)

    def test_unindexed_details_file_is_discovered(self, docs_root):
        """DETAILS.md is referenced nowhere in _INDEX — only the file walk
        finds it. This is the core coverage win: files beyond the index rows."""
        suggestions = w.suggest_warm_start_docs(
            "Map harness JSONL token fields onto backend cost columns",
            "Cached vs uncached split and the tokenizer-inflation correction.",
            docs_root,
        )
        assert suggestions, "expected DETAILS.md to be discovered by the file walk"
        paths = [s["path"] for s in suggestions]
        assert any(p.endswith("DETAILS.md") for p in paths), paths

    def test_breadcrumb_paths_repo_relative(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "Token profiling finding share", "", docs_root,
        )
        assert suggestions
        for s in suggestions:
            assert s["path"].startswith("docs/breadcrumb_analysis/")


# ---------------------------------------------------------------------
# Corpus breadth — wiki TOC one-liners
# ---------------------------------------------------------------------

class TestWikiToc:
    def test_wiki_entry_matched_on_toc_summary(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "Prompt caching invalidation on prefix change",
            "Every action that changes the cached prefix recomputes the request.",
            docs_root,
        )
        assert suggestions, "expected a wiki entry to match"
        paths = [s["path"] for s in suggestions]
        assert any("wiki" in p and "prompt-caching" in p for p in paths), paths

    def test_wiki_paths_repo_relative(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "Slash Claude token usage", "Edit prompts instead of following up.", docs_root,
        )
        assert suggestions
        for s in suggestions:
            if "wiki" in s["path"]:
                assert s["path"].startswith("docs/wiki/")
                assert s["reason"]


# ---------------------------------------------------------------------
# Corpus breadth — patterns H2
# ---------------------------------------------------------------------

class TestPatternsH2:
    def test_pattern_h2_heading_text_indexed(self, docs_root):
        """The H2 'Failure isolation' carries words the H1 + first para lack;
        a brief about failure isolation should now reach the pattern."""
        suggestions = w.suggest_warm_start_docs(
            "Metadata write failure must not gate the executor",
            "Isolate cost-tracking failures so the run always finishes.",
            docs_root,
        )
        assert suggestions
        assert any("patterns" in s["path"] for s in suggestions)


# ---------------------------------------------------------------------
# Configurable floor — min_score override
# ---------------------------------------------------------------------

class TestFloorConfig:
    def test_min_score_override_respected(self, docs_root):
        """A lower floor surfaces strictly more (or equal) matches than a
        high one for the same brief."""
        title = "Token profiling finding vs doing checking"
        desc = "Cached vs uncached cost column mapping."
        high = w.suggest_warm_start_docs(title, desc, docs_root, min_score=0.99)
        low = w.suggest_warm_start_docs(title, desc, docs_root, min_score=0.0)
        assert len(low) >= len(high)
        assert high == []  # 0.99 is unreachable for cosine
        assert low  # floor of zero surfaces something

    def test_default_floor_uses_module_constant(self, docs_root):
        """Omitting min_score falls back to MIN_SCORE (backward compatible)."""
        title = "Token profiling finding share"
        desc = "finding vs doing vs checking measured"
        defaulted = w.suggest_warm_start_docs(title, desc, docs_root)
        explicit = w.suggest_warm_start_docs(title, desc, docs_root, min_score=w.MIN_SCORE)
        assert defaulted == explicit

    def test_all_scores_unfiltered(self, docs_root):
        """The unfiltered scorer returns every entry with a score, so the
        replay can sweep floors without re-parsing the corpus."""
        rows = w.score_all("Token profiling", "finding vs doing", docs_root)
        assert rows, "expected scored entries"
        # sorted descending
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True)
        # unfiltered: includes entries that would fall below the default floor
        assert len(rows) >= 2
        for r in rows:
            assert "path" in r and "reason" in r and "score" in r
