"""Tests for odin.warm_start — task-brief → doc suggestion at prompt-build time.

Covers the acceptance criteria for task #227:
  - matching picks the right breadcrumb for real wave-5-style briefs
  - a no-match brief yields no section
  - suggested docs are recorded into task metadata

Tags:
- [disk] — reads/writes a tmp_path docs tree + local TaskManager; no network

The fixture mirrors the real repo's docs layout (breadcrumb _INDEX.md with a
Flows table + Quick-nav symptom lines, and patterns/*.md with tags frontmatter
+ H1) so briefs modelled on actual wave-5 tasks exercise the same parsing path.
"""

import os
import sys

import pytest

# Ensure the odin package (src layout) is importable when run via the repo's
# verify gate, which invokes pytest from the odin/ root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from odin import warm_start as w
from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator


# ---------------------------------------------------------------------
# Fixture: a synthetic docs tree mirroring the real repo layout
# ---------------------------------------------------------------------

_INDEX_MD = """# Breadcrumb Analyses

Workflow traces for debugging.

## Flows

| Folder | What it traces |
|--------|---------------|
| `planning-flow/` | End-to-end planning: UI spec creation → orchestrator (prompt, agent dispatch, plan parse, task creation). |
| `trace-data-pipeline/` | Trace capture (harness JSONL) → backend ingestion → cost/token computation → frontend TraceViewer. |
| `task-state-machine-celery-automation/` | The full task state machine: all statuses, stale recovery, merge-gated TESTING promotion. |
| `git-worktree-isolation/` | Git worktree per task, spec branch per spec. Merge deferred to reflection pass. |

## Quick navigation

### Planning (spec creation → task board)
- **Planning failed or created wrong tasks?** → `planning-flow/03-orchestrator/DEBUG.md`
- **Planning terminal not connecting?** → `planning-flow/02-pty-session/DEBUG.md`
- **Tasks not created after planning completes?** → `planning-flow/02-pty-session/DEBUG.md`
- **How does CreateSpecModal build the odin command?** → `planning-flow/01-spec-creation/FLOW.md`

### Routing & traces
- **Token count shows 0 or "---"?** → `trace-data-pipeline/DEBUG.md`
- **Cost mismatch between backend and UI?** → `trace-data-pipeline/DEBUG.md`
- **TraceViewer shows unknown format?** → `trace-data-pipeline/DEBUG.md`

### Execution & reflection
- **Task stuck in IN_PROGRESS?** → `task-state-machine-celery-automation/DEBUG.md`
- **PASS verdict but task didn't reach TESTING?** → `task-state-machine-celery-automation/DETAILS.md`
"""

_CLOSE_LOOP_MD = """tags: verification, live-check, rca, proof-of-work, diagnosis

# Close the Loop Before Claiming

A claim of success is only reportable after a check that could have falsified
it. Refresh and it should work hands your verification job to the user.
"""

_BOOKKEEPING_MD = """tags: cost-tracking, dispatch, graceful-degradation

# Bookkeeping Never Kills the Run

Cost tracking and metadata writes are best-effort. A write failure must never
gate the executor — the run always finishes.
"""


@pytest.fixture
def docs_root(tmp_path):
    root = tmp_path / "docs"
    bc = root / "breadcrumb_analysis"
    pat = root / "patterns"
    bc.mkdir(parents=True)
    pat.mkdir(parents=True)
    (bc / "_INDEX.md").write_text(_INDEX_MD)
    (pat / "close-the-loop-before-claiming.md").write_text(_CLOSE_LOOP_MD)
    (pat / "bookkeeping-never-kills-the-run.md").write_text(_BOOKKEEPING_MD)
    return root


# ---------------------------------------------------------------------
# Matching — real wave-5-style briefs
# ---------------------------------------------------------------------

class TestBriefMatching:
    def test_planning_failed_brief_picks_planning_flow_debug(self, docs_root):
        # Modelled on wave-5 briefs that decompose/touch the planning flow.
        suggestions = w.suggest_warm_start_docs(
            "Planning agent created wrong tasks",
            "Spec decomposition produced no tasks after the planning terminal session.",
            docs_root,
        )
        assert suggestions, "expected at least one suggestion"
        top = suggestions[0]
        assert "planning-flow" in top["path"]
        assert top["path"].endswith("DEBUG.md")
        assert top["score"] >= w.MIN_SCORE

    def test_token_count_brief_picks_trace_data_pipeline(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "Token count shows 0 in trace viewer",
            "Cost and token data mismatch between backend and UI after a run.",
            docs_root,
        )
        assert suggestions
        paths = [s["path"] for s in suggestions]
        assert any("trace-data-pipeline" in p for p in paths)

    def test_worktree_isolation_brief_picks_worktree_breadcrumb(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "Merge conflict on task completion",
            "Downstream task missing upstream work; spec branch not created.",
            docs_root,
        )
        assert suggestions
        assert any("git-worktree-isolation" in s["path"] for s in suggestions)

    def test_known_similar_outranks_unrelated(self, docs_root):
        """Parity with the twins scorer's shape: a topically-close doc must
        outrank an unrelated one for the same query."""
        suggestions = w.suggest_warm_start_docs(
            "Token count shows 0",
            "TraceViewer cost mismatch.",
            docs_root,
        )
        scores = {s["path"]: s["score"] for s in suggestions}
        trace_score = next(
            (sc for p, sc in scores.items() if "trace-data-pipeline" in p), 0.0
        )
        planning_score = next(
            (sc for p, sc in scores.items() if "planning-flow" in p), 0.0
        )
        assert trace_score > planning_score


class TestNoMatch:
    def test_nonsense_brief_yields_nothing(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "zzz qqq xyzzy flumph", "gibberish brzzz mqqq wibble", docs_root,
        )
        assert suggestions == []

    def test_no_section_rendered_on_no_match(self, docs_root):
        assert w.format_warm_start_section([]) == ""

    def test_missing_docs_root_returns_empty(self, tmp_path):
        assert w.suggest_warm_start_docs("planning", "decompose", tmp_path / "nope") == []

    def test_none_docs_root_returns_empty(self):
        assert w.suggest_warm_start_docs("planning", "decompose", None) == []


# ---------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------

class TestOutputShape:
    def test_caps_at_three(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "planning token trace worktree merge cost",
            "spec decomposition trace pipeline state machine worktree isolation.",
            docs_root,
        )
        assert len(suggestions) <= w.MAX_SUGGESTIONS

    def test_paths_are_repo_relative(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "Token count shows 0", "trace viewer mismatch", docs_root,
        )
        assert suggestions
        for s in suggestions:
            assert s["path"].startswith("docs/")
            assert s["reason"]

    def test_results_sorted_descending(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "planning token trace", "decompose and trace pipeline", docs_root,
        )
        scores = [s["score"] for s in suggestions]
        assert scores == sorted(scores, reverse=True)

    def test_format_section_renders_bullets(self, docs_root):
        suggestions = w.suggest_warm_start_docs(
            "Token count shows 0", "trace viewer mismatch", docs_root,
        )
        section = w.format_warm_start_section(suggestions)
        assert section.startswith("## Warm start (read these first)")
        assert "`docs/" in section
        # every suggested path appears in the rendered section
        for s in suggestions:
            assert s["path"] in section


# ---------------------------------------------------------------------
# Orchestrator integration — metadata recording
# ---------------------------------------------------------------------

def _make_orchestrator(tmp_path) -> Orchestrator:
    task_dir = tmp_path / "tasks"
    log_dir = tmp_path / "logs"
    cost_dir = tmp_path / "costs"
    spec_dir = tmp_path / "specs"
    for d in (task_dir, log_dir, cost_dir, spec_dir):
        d.mkdir(parents=True, exist_ok=True)
    cfg = OdinConfig(
        base_agent="claude",
        board_backend="local",
        task_storage=str(task_dir),
        log_dir=str(log_dir),
        cost_storage=str(cost_dir),
        spec_storage=str(spec_dir),
        agents={
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["reasoning", "coding"],
                cost_tier=CostTier.HIGH,
                default_model="claude-sonnet-4-5",
            ),
        },
    )
    return Orchestrator(cfg)


class TestMetadataRecording:
    def test_records_suggestions_onto_task_metadata(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        task = orch.task_mgr.create_task(
            title="Token count shows 0",
            description="trace viewer mismatch",
            spec_id="sp_test",
        )
        suggestions = [
            {"path": "docs/breadcrumb_analysis/trace-data-pipeline/DEBUG.md",
             "reason": "Token count shows 0?", "score": 0.378},
        ]
        orch._record_warm_start_docs(task.id, suggestions, mock=False)

        reloaded = orch.task_mgr.get_task(task.id)
        assert reloaded.metadata["warm_start_docs"] == suggestions

    def test_skipped_under_mock(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        task = orch.task_mgr.create_task(
            title="Token count shows 0", description="x", spec_id="sp_test",
        )
        orch._record_warm_start_docs(
            task.id,
            [{"path": "docs/x.md", "reason": "r", "score": 0.5}],
            mock=True,
        )
        reloaded = orch.task_mgr.get_task(task.id)
        assert "warm_start_docs" not in (reloaded.metadata or {})

    def test_empty_suggestions_writes_nothing(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        task = orch.task_mgr.create_task(
            title="zzz qqq", description="flumph", spec_id="sp_test",
        )
        orch._record_warm_start_docs(task.id, [], mock=False)
        reloaded = orch.task_mgr.get_task(task.id)
        assert "warm_start_docs" not in (reloaded.metadata or {})

    def test_end_to_end_suggest_then_record(self, docs_root, tmp_path):
        """suggest_warm_start_docs → _record_warm_start_docs round-trip on a
        real-shaped brief, mirroring the orchestrator's exec_task wiring."""
        orch = _make_orchestrator(tmp_path)
        task = orch.task_mgr.create_task(
            title="Planning agent created wrong tasks",
            description="Spec decomposition produced no tasks after planning.",
            spec_id="sp_test",
        )
        suggestions = w.suggest_warm_start_docs(
            task.title, task.description, docs_root,
        )
        assert suggestions, "fixture brief should match"
        orch._record_warm_start_docs(task.id, suggestions, mock=False)

        reloaded = orch.task_mgr.get_task(task.id)
        recorded = reloaded.metadata["warm_start_docs"]
        assert recorded == suggestions
        assert any("planning-flow" in r["path"] for r in recorded)
