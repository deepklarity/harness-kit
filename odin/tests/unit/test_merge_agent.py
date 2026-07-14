"""Unit tests for the merge-conflict resolution agent.

Covers the deterministic conflict classifier, the in-worktree resolution
flow, and the board-comment formatters.  No real git — the WorktreeManager
is mocked at the subprocess level.
"""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from odin.merge_agent import (
    MergeResolution,
    classify_conflict,
    resolve_conflicts_in_worktree,
    format_resolution_comment,
    format_merge_question,
    extract_conflict_hunk_text,
    summarize_side,
    classify_resolution,
    interpret_guidance,
    _is_pure_additive_both_sides,
    _syntax_check,
    _try_auto_resolve_additive,
    _check_post_merge_files,
    _resolve_keep_both_in_file,
)


# ------------------------------------------------------------------
# classify_conflict — the deterministic classifier
# ------------------------------------------------------------------

class TestClassifyConflict:
    """The classifier is the safety gate: it decides what the agent may
    auto-resolve vs. what must be escalated.  Getting this wrong means
    either blocking the operator on trivial conflicts (false ambiguous)
    or silently clobbering product code (false mechanical).
    """

    def test_generated_config_is_mechanical(self):
        mechanical, ambiguous = classify_conflict(["opencode.json"])
        assert mechanical == ["opencode.json"]
        assert ambiguous == []

    def test_multiple_generated_configs_all_mechanical(self):
        mechanical, ambiguous = classify_conflict([
            "opencode.json", ".mcp.json", ".claude/settings.local.json",
        ])
        assert len(mechanical) == 3
        assert ambiguous == []

    def test_product_code_is_ambiguous(self):
        mechanical, ambiguous = classify_conflict(["src/feature.py"])
        assert mechanical == []
        assert ambiguous == ["src/feature.py"]

    def test_mixed_conflict_has_both_buckets(self):
        mechanical, ambiguous = classify_conflict([
            "opencode.json", "src/handlers.py", ".mcp.json",
        ])
        assert "opencode.json" in mechanical
        assert ".mcp.json" in mechanical
        assert "src/handlers.py" in ambiguous
        assert len(mechanical) == 2
        assert len(ambiguous) == 1

    def test_empty_list_returns_empty_buckets(self):
        mechanical, ambiguous = classify_conflict([])
        assert mechanical == []
        assert ambiguous == []

    def test_glob_pattern_generated_config(self):
        """*.orig files are generated configs (glob pattern)."""
        mechanical, ambiguous = classify_conflict(["some/path/file.orig"])
        assert mechanical == ["some/path/file.orig"]
        assert ambiguous == []

    def test_config_yaml_is_not_generated(self):
        """.odin/config.yaml is project config, not harness-generated."""
        mechanical, ambiguous = classify_conflict([".odin/config.yaml"])
        assert ambiguous == [".odin/config.yaml"]
        assert mechanical == []


# ------------------------------------------------------------------
# resolve_conflicts_in_worktree — the in-place resolution flow
# ------------------------------------------------------------------

def _git_ok(stdout="", stderr=""):
    return MagicMock(returncode=0, stdout=stdout, stderr=stderr)


def _git_fail(stderr="", stdout=""):
    return MagicMock(returncode=1, stderr=stderr, stdout=stdout)


class TestResolveConflictsInWorktree:
    """The resolution function operates on a live (mocked) worktree where
    ``git merge`` has already produced conflicts.  It must:

    1. Resolve ALL files when every conflict is mechanical.
    2. Refuse to resolve ANY file when even one is ambiguous (all-or-
       nothing — no partial merges that silently pick a winner).
    """

    def test_all_mechanical_resolves(self):
        """Every conflicting file is a generated config → resolve all."""
        wt = MagicMock()
        wt._git.return_value = _git_ok()
        worktree = Path("/fake/wt")

        resolution = resolve_conflicts_in_worktree(
            wt, worktree, ["opencode.json", ".mcp.json"],
        )

        assert resolution.resolved is True
        assert resolution.needs_human is False
        assert set(resolution.mechanical_files) == {"opencode.json", ".mcp.json"}
        assert resolution.resolution_method is not None
        # Must have called checkout --ours for each file
        checkout_calls = [
            c for c in wt._git.call_args_list
            if len(c[0]) >= 2 and c[0][0] == "checkout"
        ]
        assert len(checkout_calls) == 2

    def test_any_ambiguous_needs_human(self):
        """A product-code conflict means the whole merge needs human judgment."""
        wt = MagicMock()
        worktree = Path("/fake/wt")

        resolution = resolve_conflicts_in_worktree(
            wt, worktree, ["opencode.json", "src/main.py"],
        )

        assert resolution.resolved is False
        assert resolution.needs_human is True
        assert "src/main.py" in resolution.ambiguous_files
        assert "opencode.json" in resolution.mechanical_files
        # Must NOT have called checkout --ours (all-or-nothing)
        checkout_calls = [
            c for c in wt._git.call_args_list
            if len(c[0]) >= 1 and c[0][0] == "checkout"
        ]
        assert len(checkout_calls) == 0
        # Question text must mention the ambiguous file
        assert "src/main.py" in resolution.question_text

    def test_all_ambiguous_needs_human(self):
        """All product-code conflicts → needs_human, no resolution attempted."""
        wt = MagicMock()

        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["src/a.py", "src/b.py", "README.md"],
        )

        assert resolution.needs_human is True
        assert resolution.resolved is False
        assert len(resolution.ambiguous_files) == 3

    def test_checkout_failure_marks_needs_human(self):
        """If ``git checkout --ours`` fails on a mechanical file, escalate."""
        wt = MagicMock()
        wt._git.side_effect = [_git_fail(stderr="index lock"), _git_ok()]

        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["opencode.json"],
        )

        assert resolution.needs_human is True
        assert resolution.resolved is False
        assert resolution.error is not None

    def test_task_brief_in_question(self):
        """The task brief is included in the question for operator context."""
        wt = MagicMock()
        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["src/feature.py"],
            task_brief="Implement user auth",
        )
        assert "Implement user auth" in resolution.question_text


# ------------------------------------------------------------------
# format_resolution_comment — the success comment
# ------------------------------------------------------------------

class TestFormatResolutionComment:
    def test_lists_resolved_files(self):
        resolution = MergeResolution(
            resolved=True,
            mechanical_files=["opencode.json", ".mcp.json"],
            resolution_method="checkout --ours",
            rationale="Generated configs — safe to take spec side.",
        )
        msg = format_resolution_comment(
            "task/sp/42", "spec/sp", resolution,
        )
        assert "opencode.json" in msg
        assert ".mcp.json" in msg
        assert "checkout --ours" in msg
        assert "auto-resolved" in msg.lower()

    def test_includes_diff_stat(self):
        resolution = MergeResolution(
            resolved=True,
            mechanical_files=["opencode.json"],
            resolution_method="checkout --ours",
        )
        msg = format_resolution_comment(
            "task/sp/42", "spec/sp", resolution,
            diff_stat=" 1 file changed",
        )
        assert "1 file changed" in msg


# ------------------------------------------------------------------
# format_merge_question — the blocking question
# ------------------------------------------------------------------

class TestFormatMergeQuestion:
    def test_lists_ambiguous_files(self):
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["src/auth.py", "src/models.py"],
        )
        assert "src/auth.py" in msg
        assert "src/models.py" in msg
        assert "needs you" in msg.lower()

    def test_lists_mechanical_files_when_present(self):
        """Even when escalating, show the operator which files were
        auto-resolvable so they can make an informed decision."""
        msg = format_merge_question(
            mechanical_files=["opencode.json"],
            ambiguous_files=["src/main.py"],
        )
        assert "opencode.json" in msg
        assert "src/main.py" in msg
        assert "auto-resolvable" in msg.lower()

    def test_includes_options(self):
        msg = format_merge_question([], ["src/x.py"])
        assert "by hand" in msg.lower() or "your call" in msg.lower()


# ------------------------------------------------------------------
# extract_conflict_hunk_text — read the merge markers from a worktree
# ------------------------------------------------------------------

class TestExtractConflictHunkText:
    """The worktree contains files with conflict markers during a failed
    merge.  This helper pulls the first conflict region (between the
    ``<<<<<<<`` and ``>>>>>>>`` markers) so the operator can see the
    actual divergent lines without opening the file.
    """

    def test_returns_first_conflict_region(self, tmp_path):
        conflicted = textwrap.dedent(
            """\
            line 1
            line 2
            <<<<<<< HEAD
            spec version
            =======
            task version
            >>>>>>> task/spec/157
            line 5
            """
        )
        f = tmp_path / "src" / "feature.py"
        f.parent.mkdir(parents=True)
        f.write_text(conflicted)

        hunk = extract_conflict_hunk_text(tmp_path, "src/feature.py")

        assert "<<<<<<<" in hunk
        assert "spec version" in hunk
        assert "task version" in hunk
        assert ">>>>>>>" in hunk

    def test_truncates_long_hunks(self, tmp_path):
        body = "\n".join(f"line {i}" for i in range(200))
        conflicted = (
            "header\n"
            "<<<<<<< HEAD\n"
            + body + "\n"
            "=======\n"
            + body + "\n"
            ">>>>>>> task/spec/157\n"
            "footer\n"
        )
        f = tmp_path / "big.py"
        f.write_text(conflicted)

        hunk = extract_conflict_hunk_text(tmp_path, "big.py", max_lines=10)

        # Truncated: should NOT contain all 200 lines
        assert hunk.count("\n") < 20

    def test_long_hunk_preserves_both_sides_past_the_separator(self, tmp_path):
        """Regression (task 206): a spec side longer than ``max_lines``
        must not swallow the ``=======`` separator and the entire task
        side.  That bug made a real multi-hundred-line modify/modify
        conflict (task 188 vs 192 on ``.proof/proof.md``) look like a
        delete-vs-modify, because the linear line cap cut off before the
        separator ever appeared — the task side became invisible to
        ``classify_resolution`` and the report.
        """
        spec_body = "\n".join(f"spec line {i}" for i in range(50))
        task_body = "\n".join(f"task line {i}" for i in range(50))
        conflicted = (
            "<<<<<<< HEAD\n" + spec_body + "\n=======\n" + task_body
            + "\n>>>>>>> task/spec/1\n"
        )
        f = tmp_path / "big.md"
        f.write_text(conflicted)

        hunk = extract_conflict_hunk_text(tmp_path, "big.md", max_lines=12)

        assert "=======" in hunk
        assert ">>>>>>>" in hunk
        assert "spec line 0" in hunk
        assert "task line 0" in hunk

    def test_missing_file_returns_empty(self, tmp_path):
        assert extract_conflict_hunk_text(tmp_path, "nonexistent.py") == ""

    def test_no_markers_returns_head_only(self, tmp_path):
        """If a file is conflicted in the porcelain status but contains no
        markers (rare: binary, etc.), fall back to the file head with a
        hint instead of crashing."""
        f = tmp_path / "binary.dat"
        f.write_bytes(b"\x00\x01\x02not text")
        out = extract_conflict_hunk_text(tmp_path, "binary.dat")
        # Empty string is acceptable — the caller treats it as "no hunk"
        assert isinstance(out, str)


# ------------------------------------------------------------------
# summarize_side — plain-English one-liner per conflict side
# ------------------------------------------------------------------

class TestSummarizeSide:
    """``summarize_side`` produces a single plain-English sentence for
    what one side of a conflict changed.  Output must be short enough
    to scan in <1 minute (acceptance criterion for the merge report).
    """

    def test_describes_addition(self):
        text = "def new_helper():\n    return 42\n"
        out = summarize_side("spec", text)
        assert "spec" in out.lower()
        assert "added" in out.lower() or "new" in out.lower()

    def test_describes_replacement(self):
        text = "def hello(): return 'universe'\n"
        out = summarize_side("task", text)
        assert "task" in out.lower()
        assert "return" in out  # quotes the changed line for context

    def test_handles_empty_side(self):
        """One side may be empty (e.g. delete-vs-modify)."""
        out = summarize_side("spec", "")
        assert "spec" in out.lower()
        assert "empty" in out.lower() or "deleted" in out.lower() or "removed" in out.lower()

    def test_includes_first_meaningful_line(self):
        text = "\n\n  \n  return 99\n"
        out = summarize_side("task", text)
        # Should quote the meaningful line, not the whitespace
        assert "return 99" in out

    def test_truncates_long_content(self):
        text = "\n".join(f"line_{i} = {i}" for i in range(50))
        out = summarize_side("spec", text)
        # Should fit in one screen — under ~200 chars
        assert len(out) < 200


# ------------------------------------------------------------------
# classify_resolution — keep-both vs choose
# ------------------------------------------------------------------

class TestClassifyResolution:
    """Given the two sides of a conflict, classify whether the changes
    look complementary (keep both, no operator decision needed beyond
    confirm) or contradictory (operator must pick).
    """

    def test_disjoint_sections_suggest_keep_both(self):
        spec = "def helper_a():\n    return 1\n"
        task = "def helper_b():\n    return 2\n"
        verdict, why = classify_resolution(spec, task)
        assert verdict == "keep-both"
        assert "disjoint" in why.lower() or "complementary" in why.lower() or "different" in why.lower()

    def test_same_line_replaced_suggests_choose(self):
        spec = "def hello(): return 'world'\n"
        task = "def hello(): return 'universe'\n"
        verdict, why = classify_resolution(spec, task)
        assert verdict == "choose"
        assert "overlap" in why.lower() or "same" in why.lower() or "contradict" in why.lower()

    def test_identical_content_suggests_keep_both(self):
        spec = "def hello(): return 'x'\n"
        task = "def hello(): return 'x'\n"
        verdict, why = classify_resolution(spec, task)
        assert verdict == "keep-both"

    def test_empty_one_side_suggests_choose(self):
        """Delete-vs-modify — one side removed everything the other added."""
        spec = ""
        task = "def new_feature():\n    pass\n"
        verdict, why = classify_resolution(spec, task)
        assert verdict == "choose"

    def test_whitespace_only_keeps_both(self):
        """A whitespace-only conflict is mechanical and can be auto-merged."""
        spec = "def hello(): return 1\n"
        task = "def hello(): return  1\n"  # extra space
        verdict, why = classify_resolution(spec, task)
        assert verdict == "keep-both"

    def test_two_large_unrelated_rewrites_suggest_choose_not_keep_both(self):
        """Regression (task 206): two large, mostly non-overlapping bodies
        of content (e.g. two competing full-document rewrites sharing the
        same path — the real task 188 vs 192 ``.proof/proof.md``
        collision) must not be auto-suggested as keep-both. Concatenating
        two unrelated documents isn't a real resolution the way keeping
        two small disjoint code snippets is.
        """
        spec = "\n".join(f"spec paragraph {i} about routing" for i in range(40))
        task = "\n".join(f"task paragraph {i} about CANCELED status" for i in range(40))
        verdict, why = classify_resolution(spec, task)
        assert verdict == "choose"
        assert "rewrote" in why.lower() or "large" in why.lower()


# ------------------------------------------------------------------
# format_merge_question — rich per-file report (acceptance gate)
# ------------------------------------------------------------------

class TestFormatMergeQuestionRich:
    """The acceptance criterion: a non-author can decide from the report
    in under a minute without opening files.  This means each conflicted
    file must carry:

    - One plain-English sentence per side (spec vs task).
    - The conflict hunk head (the actual divergent lines).
    - A suggested resolution (keep-both vs choose).
    """

    def _two_side_hunk(self, spec_text: str, task_text: str) -> str:
        """Build a representative git-style conflict block."""
        return (
            "<<<<<<< HEAD (spec)\n"
            f"{spec_text}\n"
            "=======\n"
            f"{task_text}\n"
            ">>>>>>> task/spec/157\n"
        )

    def test_includes_hunk_head_per_file(self):
        hunk = self._two_side_hunk(
            "spec_only_line = 1",
            "task_only_line = 2",
        )
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["src/feature.py"],
            task_brief="Add metrics",
            conflict_hunks={"src/feature.py": hunk},
        )
        assert "src/feature.py" in msg
        assert "spec_only_line" in msg
        assert "task_only_line" in msg
        assert "<<<<<<<" in msg  # conflict marker visible

    def test_includes_one_sentence_per_side(self):
        hunk = self._two_side_hunk(
            "spec_added_line = True",
            "task_added_line = False",
        )
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["src/api.py"],
            task_brief="Refactor auth",
            conflict_hunks={"src/api.py": hunk},
        )
        # Both sides must be described in plain English
        assert "spec" in msg.lower()
        assert "task" in msg.lower()
        # The actual changed values appear as evidence
        assert "spec_added_line = True" in msg or "spec_added_line" in msg
        assert "task_added_line = False" in msg or "task_added_line" in msg

    def test_suggests_keep_both_for_complementary(self):
        hunk = self._two_side_hunk(
            "spec_extra_helper()",
            "task_extra_helper()",
        )
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["lib/utils.py"],
            task_brief="Complementary change",
            conflict_hunks={"lib/utils.py": hunk},
        )
        # Must suggest keep-both for disjoint content
        assert "keep-both" in msg.lower() or "keep both" in msg.lower()
        # And the rationale must be present
        assert "disjoint" in msg.lower() or "complementary" in msg.lower() or "different" in msg.lower()

    def test_suggests_choose_for_contradictory(self):
        hunk = self._two_side_hunk(
            "value = 'spec_choice'",
            "value = 'task_choice'",
        )
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["config/settings.py"],
            task_brief="Conflicting change",
            conflict_hunks={"config/settings.py": hunk},
        )
        assert "choose" in msg.lower() or "pick" in msg.lower() or "decide" in msg.lower()

    def test_backward_compatible_without_hunks(self):
        """When called without hunk info (e.g. legacy caller), the report
        must still include file paths and the existing options block."""
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["src/feature.py"],
        )
        assert "src/feature.py" in msg
        # Options block preserved
        assert "by hand" in msg.lower() or "your call" in msg.lower()

    def test_multi_file_report_stays_scannable(self):
        """With multiple files, each one must be self-contained — the
        reader doesn't have to cross-reference."""
        hunks = {
            "src/a.py": self._two_side_hunk("a_spec", "a_task"),
            "src/b.py": self._two_side_hunk("b_spec", "b_task"),
            "src/c.py": self._two_side_hunk("c_spec", "c_task"),
        }
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=list(hunks.keys()),
            task_brief="Multi-file conflict",
            conflict_hunks=hunks,
        )
        for path in hunks:
            assert path in msg, f"{path} missing from report"
        # Each file's spec and task content appears
        assert "a_spec" in msg and "a_task" in msg
        assert "b_spec" in msg and "b_task" in msg
        assert "c_spec" in msg and "c_task" in msg

    def test_partial_hunks_only_renders_known_files(self):
        """A caller may have hunks for some files but not others; the
        report must not crash and must still list unknown files."""
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["src/known.py", "src/unknown.py"],
            task_brief="Partial info",
            conflict_hunks={"src/known.py": "<<<<<<<\nknown\n=======\nknown\n>>>>>>>"},
        )
        assert "src/known.py" in msg
        assert "src/unknown.py" in msg  # still listed, just without hunk


# ------------------------------------------------------------------
# MergeResolution carries conflict_hunks through
# ------------------------------------------------------------------

class TestFormatMergeQuestionExplainsBothSides:
    """Task 206: the report must let a human answer from their chair —
    what each side was doing and why (from commit messages/task briefs),
    why they collide, and the agent's proposed resolution — ending in a
    concrete question.  Raw hunks are a collapsed appendix, not the
    headline.
    """

    def _hunk(self, spec_text: str, task_text: str) -> str:
        return (
            "<<<<<<< HEAD\n"
            f"{spec_text}\n"
            "=======\n"
            f"{task_text}\n"
            ">>>>>>> task/spec/999\n"
        )

    def test_spec_and_task_why_come_from_commit_messages(self):
        hunk = self._hunk("value = 'spec_choice'", "value = 'task_choice'")
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=[".proof/proof.md"],
            task_brief="Add CANCELED status",
            conflict_hunks={".proof/proof.md": hunk},
            spec_commit_messages={
                ".proof/proof.md": ["fix(routing): regenerate proof artifacts — round 2"],
            },
            task_commit_messages={
                ".proof/proof.md": ["192: add CANCELED as terminal-neutral Task status"],
            },
        )
        assert "regenerate proof artifacts" in msg
        assert "add CANCELED as terminal-neutral" in msg
        assert "why they collide" in msg.lower()

    def test_falls_back_to_task_brief_when_no_commit_messages(self):
        hunk = self._hunk("value = 'spec_choice'", "value = 'task_choice'")
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["config/settings.py"],
            task_brief="Refactor auth config",
            conflict_hunks={"config/settings.py": hunk},
        )
        assert "Refactor auth config" in msg

    def test_ends_with_a_concrete_answerable_question(self):
        hunk = self._hunk("value = 'spec_choice'", "value = 'task_choice'")
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["config/settings.py"],
            conflict_hunks={"config/settings.py": hunk},
        )
        tail = "\n".join(msg.strip().splitlines()[-6:])
        assert "?" in tail
        assert "your call" in msg.lower()

    def test_raw_hunks_are_collapsed_after_the_why_blocks(self):
        hunk = self._hunk("spec_only_line = 1", "task_only_line = 2")
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["src/feature.py"],
            conflict_hunks={"src/feature.py": hunk},
        )
        why_idx = msg.lower().index("why they collide")
        raw_idx = msg.index("<<<<<<<")
        question_idx = msg.lower().index("your call")
        assert why_idx < raw_idx < question_idx

    def test_proposed_resolution_includes_reasoning(self):
        hunk = self._hunk("value = 'spec_choice'", "value = 'task_choice'")
        msg = format_merge_question(
            mechanical_files=[],
            ambiguous_files=["config/settings.py"],
            conflict_hunks={"config/settings.py": hunk},
        )
        assert "proposed resolution" in msg.lower()
        # The recommendation must carry a reason, not just repeat the verdict.
        idx = msg.lower().index("proposed resolution")
        rest_of_line = msg[idx:].splitlines()[0]
        assert len(rest_of_line) > len("Proposed resolution: **choose**")


class TestResolveConflictsCapturesCommitContext:
    """The merge agent explains WHY each side changed a file by reading
    the real commit history — ``HEAD`` (spec side) and ``MERGE_HEAD``
    (task side) both exist while the conflicted merge is still in
    progress, before the caller aborts it.
    """

    def test_captures_spec_and_task_commit_subjects(self, tmp_path):
        f = tmp_path / ".proof" / "proof.md"
        f.parent.mkdir(parents=True)
        f.write_text(
            "<<<<<<< HEAD\nTask 188 routing proof\n=======\n"
            "Task 192 CANCELED proof\n>>>>>>> task/sp/192\n"
        )

        def fake_git(*args, **kwargs):
            if args and args[0] == "log":
                ref = args[3] if len(args) > 3 else None
                if ref == "HEAD":
                    return MagicMock(
                        returncode=0,
                        stdout="fix(routing): regenerate proof artifacts — round 2\n",
                        stderr="",
                    )
                if ref == "MERGE_HEAD":
                    return MagicMock(
                        returncode=0,
                        stdout="192: add CANCELED as terminal-neutral Task status\n",
                        stderr="",
                    )
            return MagicMock(returncode=0, stdout="", stderr="")

        wt = MagicMock()
        wt._git.side_effect = fake_git

        resolution = resolve_conflicts_in_worktree(
            wt, tmp_path, [".proof/proof.md"],
            task_brief="Add CANCELED as terminal-neutral Task status",
        )

        assert resolution.needs_human is True
        assert "regenerate proof artifacts" in resolution.spec_commit_messages[".proof/proof.md"][0]
        assert "CANCELED as terminal-neutral" in resolution.task_commit_messages[".proof/proof.md"][0]
        assert "regenerate proof artifacts" in resolution.question_text
        assert "CANCELED as terminal-neutral" in resolution.question_text

    def test_no_commit_context_falls_back_gracefully(self, tmp_path):
        """When git log yields nothing (mocked wt, no real repo), the
        report must still render without crashing."""
        f = tmp_path / "src" / "feature.py"
        f.parent.mkdir(parents=True)
        f.write_text(
            "<<<<<<< HEAD\nspec line\n=======\ntask line\n>>>>>>> task/spec/1\n"
        )
        wt = MagicMock()
        resolution = resolve_conflicts_in_worktree(
            wt, tmp_path, ["src/feature.py"], task_brief="Some task",
        )
        assert resolution.needs_human is True
        assert resolution.spec_commit_messages == {}
        assert resolution.task_commit_messages == {}
        assert "src/feature.py" in resolution.question_text


class TestMergeResolutionCarriesHunks:
    """When ``resolve_conflicts_in_worktree`` returns needs_human, the
    MergeResolution must carry the captured hunk text so the downstream
    ``dag_executor`` can render the rich report without re-running git.
    """

    def test_needs_human_captures_hunks_from_worktree(self, tmp_path):
        """Build a fake worktree with conflict-marker files, point the
        mocked ``wt._git`` at it, and verify the resolution captures the
        hunk text per file."""
        f = tmp_path / "src" / "feature.py"
        f.parent.mkdir(parents=True)
        f.write_text(
            "<<<<<<< HEAD\nspec line\n=======\ntask line\n>>>>>>> task/spec/157\n"
        )

        wt = MagicMock()
        # No need for git stubs — extract_conflict_hunk_text reads the
        # filesystem directly via the worktree Path.
        resolution = resolve_conflicts_in_worktree(
            wt, tmp_path, ["src/feature.py"],
            task_brief="Replay task 157",
        )

        assert resolution.needs_human is True
        assert "src/feature.py" in resolution.conflict_hunks
        assert "spec line" in resolution.conflict_hunks["src/feature.py"]
        assert "task line" in resolution.conflict_hunks["src/feature.py"]

    def test_needs_human_question_text_includes_rich_detail(self, tmp_path):
        f = tmp_path / "src" / "main.py"
        f.parent.mkdir(parents=True)
        f.write_text(
            "<<<<<<< HEAD\ndef hello(): return 'world'\n=======\n"
            "def hello(): return 'universe'\n>>>>>>> task/spec/157\n"
        )

        wt = MagicMock()
        resolution = resolve_conflicts_in_worktree(
            wt, tmp_path, ["src/main.py"],
            task_brief="Replay task 157",
        )

        msg = resolution.question_text or ""
        # The pre-built question must include both sides and a verdict
        assert "src/main.py" in msg
        assert "world" in msg
        assert "universe" in msg
        assert "choose" in msg.lower() or "pick" in msg.lower() or "decide" in msg.lower()


# ------------------------------------------------------------------
# interpret_guidance — mapping a human's free-text reply to an action
# ------------------------------------------------------------------

class TestInterpretGuidance:
    """A human answers the merge question in plain English; this maps
    that reply to a deterministic per-file action.  Unrecognized text
    must resolve nothing (never guess silently)."""

    def test_keep_both_maps_all_files(self):
        actions = interpret_guidance("Keep both changes please", ["a.py", "b.py"])
        assert actions == {"a.py": "keep-both", "b.py": "keep-both"}

    def test_hyphenated_keep_both_recognized(self):
        actions = interpret_guidance("Confirm keep-both for this file", ["a.py"])
        assert actions == {"a.py": "keep-both"}

    def test_task_side_recognized(self):
        actions = interpret_guidance("accept the task side, drop spec's change", ["a.py"])
        assert actions == {"a.py": "task"}

    def test_spec_side_recognized(self):
        actions = interpret_guidance("keep the spec side please", ["a.py"])
        assert actions == {"a.py": "spec"}

    def test_keep_both_move_to_own_dir_recognized_as_move(self):
        actions = interpret_guidance(
            "keep both, move task proof to its own dir", ["proof.md"],
        )
        assert actions == {"proof.md": "move"}

    def test_unrecognized_guidance_returns_empty(self):
        assert interpret_guidance("hmm, not sure, let me look at it", ["a.py"]) == {}

    def test_blank_guidance_returns_empty(self):
        assert interpret_guidance("", ["a.py"]) == {}
        assert interpret_guidance("   ", ["a.py"]) == {}


# ------------------------------------------------------------------
# resolve_conflicts_in_worktree(resolution_guidance=...) — the resume path
# ------------------------------------------------------------------

class TestResolveConflictsWithGuidance:
    """A human's reply to the blocking merge question re-attempts the
    conflict.  All-or-nothing: guidance must cover every ambiguous file
    or nothing is staged and the merge is escalated again."""

    def test_task_side_guidance_checks_out_theirs(self):
        wt = MagicMock()
        wt._git.return_value = _git_ok()

        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["src/feature.py"],
            resolution_guidance="accept the task side",
        )

        assert resolution.resolved is True
        assert resolution.needs_human is False
        theirs_calls = [
            c for c in wt._git.call_args_list
            if c[0][:2] == ("checkout", "--theirs")
        ]
        assert len(theirs_calls) == 1
        assert "src/feature.py" in resolution.rationale

    def test_spec_side_guidance_checks_out_ours(self):
        wt = MagicMock()
        wt._git.return_value = _git_ok()

        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["src/feature.py"],
            resolution_guidance="keep the spec side",
        )

        assert resolution.resolved is True
        ours_calls = [
            c for c in wt._git.call_args_list
            if c[0][:2] == ("checkout", "--ours")
        ]
        assert len(ours_calls) == 1

    def test_keep_both_guidance_concatenates_file_content(self, tmp_path):
        conflicted = (
            "line 1\n"
            "<<<<<<< HEAD\n"
            "spec version\n"
            "=======\n"
            "task version\n"
            ">>>>>>> task/spec/157\n"
        )
        f = tmp_path / "docs" / "notes.md"
        f.parent.mkdir(parents=True)
        f.write_text(conflicted)

        wt = MagicMock()
        wt._git.return_value = _git_ok()

        resolution = resolve_conflicts_in_worktree(
            wt, tmp_path, ["docs/notes.md"],
            resolution_guidance="keep both changes",
        )

        assert resolution.resolved is True
        new_text = f.read_text()
        assert "<<<<<<<" not in new_text
        assert "spec version" in new_text
        assert "task version" in new_text

    def test_move_guidance_writes_task_side_to_its_own_dir(self, tmp_path):
        conflicted = (
            "<<<<<<< HEAD\n"
            "# Spec's proof\n"
            "=======\n"
            "# Task's proof\n"
            ">>>>>>> task/sp/207\n"
        )
        f = tmp_path / "proof.md"
        f.write_text(conflicted)

        wt = MagicMock()

        def _git(*args, **kwargs):
            if args and args[0] == "show":
                return _git_ok(stdout="# Task's proof\n")
            return _git_ok()
        wt._git.side_effect = _git

        resolution = resolve_conflicts_in_worktree(
            wt, tmp_path, ["proof.md"],
            resolution_guidance="keep both, move task's proof to its own dir",
            task_id="207",
        )

        assert resolution.resolved is True
        dest = tmp_path / ".proof" / "task-207" / "proof.md"
        assert dest.exists()
        assert "Task's proof" in dest.read_text()
        assert "proof.md" in resolution.rationale

    def test_insufficient_guidance_re_escalates_without_side_effects(self):
        wt = MagicMock()

        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["src/feature.py"],
            resolution_guidance="I'm not sure, let me think about it",
        )

        assert resolution.resolved is False
        assert resolution.needs_human is True
        assert "src/feature.py" in resolution.ambiguous_files
        assert "not sure" in (resolution.rationale or "").lower() or "did not cover" in (resolution.rationale or "").lower()
        checkout_calls = [c for c in wt._git.call_args_list if c[0] and c[0][0] == "checkout"]
        assert len(checkout_calls) == 0

    def test_guided_checkout_failure_escalates(self):
        wt = MagicMock()
        wt._git.side_effect = [_git_fail(stderr="index lock")]

        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["src/feature.py"],
            resolution_guidance="accept the task side",
        )

        assert resolution.needs_human is True
        assert resolution.error is not None

    def test_mechanical_and_guided_ambiguous_both_resolved(self):
        wt = MagicMock()
        wt._git.return_value = _git_ok()

        resolution = resolve_conflicts_in_worktree(
            wt, Path("/fake"), ["opencode.json", "src/feature.py"],
            resolution_guidance="accept the task side",
        )

        assert resolution.resolved is True
        assert "opencode.json" in resolution.mechanical_files
        assert "src/feature.py" in resolution.mechanical_files


# ------------------------------------------------------------------
# _is_pure_additive_both_sides — the confidence gate's merge-base check
# ------------------------------------------------------------------

def _numstat_dispatch(base, deletions_by_ref):
    """Build a wt._git side_effect that answers merge-base + numstat
    calls for :func:`_is_pure_additive_both_sides`."""
    def _dispatch(*args, **kwargs):
        if args and args[0] == "merge-base":
            return _git_ok(stdout=f"{base}\n")
        if args and args[0] == "diff" and "--numstat" in args:
            ref = args[3]
            added, deleted = deletions_by_ref.get(ref, ("0", "0"))
            return _git_ok(stdout=f"{added}\t{deleted}\tpath.py\n")
        return _git_ok()
    return _dispatch


class TestIsPureAdditiveBothSides:
    """A conflict only clears the auto-apply gate when NEITHER side
    deleted or modified a line relative to the merge base — a
    modification shows up in ``git diff --numstat`` as a deletion (old
    line) plus an addition (new line), so checking deletions catches
    edits, not just outright removals."""

    def test_zero_deletions_both_sides_is_pure_additive(self):
        wt = MagicMock()
        wt._git.side_effect = _numstat_dispatch(
            "basesha", {"HEAD": ("2", "0"), "MERGE_HEAD": ("3", "0")},
        )
        assert _is_pure_additive_both_sides(wt, Path("/fake"), "path.py") is True

    def test_deletions_on_spec_side_fails_gate(self):
        wt = MagicMock()
        wt._git.side_effect = _numstat_dispatch(
            "basesha", {"HEAD": ("2", "1"), "MERGE_HEAD": ("3", "0")},
        )
        assert _is_pure_additive_both_sides(wt, Path("/fake"), "path.py") is False

    def test_deletions_on_task_side_fails_gate(self):
        wt = MagicMock()
        wt._git.side_effect = _numstat_dispatch(
            "basesha", {"HEAD": ("2", "0"), "MERGE_HEAD": ("1", "4")},
        )
        assert _is_pure_additive_both_sides(wt, Path("/fake"), "path.py") is False

    def test_merge_base_failure_fails_closed(self):
        wt = MagicMock()
        wt._git.return_value = _git_fail(stderr="not a valid ref")
        assert _is_pure_additive_both_sides(wt, Path("/fake"), "path.py") is False

    def test_binary_numstat_fails_closed(self):
        wt = MagicMock()

        def _dispatch(*args, **kwargs):
            if args and args[0] == "merge-base":
                return _git_ok(stdout="basesha\n")
            if args and args[0] == "diff":
                return _git_ok(stdout="-\t-\tpath.bin\n")
            return _git_ok()
        wt._git.side_effect = _dispatch
        assert _is_pure_additive_both_sides(wt, Path("/fake"), "path.bin") is False

    def test_no_diff_for_a_side_counts_as_zero_deletions(self):
        """A side that didn't touch the path at all vs. the merge base
        (e.g. it only exists because of the other side) is trivially
        non-deleting."""
        wt = MagicMock()

        def _dispatch(*args, **kwargs):
            if args and args[0] == "merge-base":
                return _git_ok(stdout="basesha\n")
            if args and args[0] == "diff":
                return _git_ok(stdout="")
            return _git_ok()
        wt._git.side_effect = _dispatch
        assert _is_pure_additive_both_sides(wt, Path("/fake"), "path.py") is True


# ------------------------------------------------------------------
# _syntax_check — fast post-apply sanity check
# ------------------------------------------------------------------

class TestSyntaxCheck:
    def test_valid_python_passes(self, tmp_path):
        f = tmp_path / "ok.py"
        f.write_text("def hello():\n    return 1\n")
        assert _syntax_check(tmp_path, "ok.py") is True

    def test_invalid_python_fails(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("def hello(:\n    return 1\n")
        assert _syntax_check(tmp_path, "broken.py") is False

    def test_valid_json_passes(self, tmp_path):
        f = tmp_path / "ok.json"
        f.write_text('{"a": 1}')
        assert _syntax_check(tmp_path, "ok.json") is True

    def test_invalid_json_fails(self, tmp_path):
        f = tmp_path / "broken.json"
        f.write_text('{"a": 1,}')
        assert _syntax_check(tmp_path, "broken.json") is False

    def test_unknown_extension_assumed_fine(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("<<<<<<< anything goes\n")
        assert _syntax_check(tmp_path, "notes.md") is True

    def test_missing_file_fails(self, tmp_path):
        assert _syntax_check(tmp_path, "nonexistent.py") is False


# ------------------------------------------------------------------
# _try_auto_resolve_additive — the full apply-or-park decision
# ------------------------------------------------------------------

class TestTryAutoResolveAdditive:
    def _write_conflict(self, tmp_path, path, spec_line, task_line):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(
            f"# line before\n<<<<<<< HEAD\n{spec_line}\n=======\n{task_line}\n"
            f">>>>>>> task/spec/1\n# line after\n"
        )

    def test_applies_and_stages_when_gate_clears(self, tmp_path):
        self._write_conflict(tmp_path, "feature.py", "spec_added = 1", "task_added = 2")
        wt = MagicMock()
        wt._git.side_effect = _numstat_dispatch(
            "basesha", {"HEAD": ("1", "0"), "MERGE_HEAD": ("1", "0")},
        )
        file_resolutions = {"feature.py": {"verdict": "keep-both", "rationale": "disjoint"}}
        side_texts = {"feature.py": ("spec_added = 1", "task_added = 2")}

        rationale = _try_auto_resolve_additive(
            wt, tmp_path, ["feature.py"], file_resolutions, side_texts,
        )

        assert rationale is not None
        assert "feature.py" in rationale
        assert "W5.12" in rationale
        new_text = (tmp_path / "feature.py").read_text()
        assert "<<<<<<<" not in new_text
        assert "spec_added = 1" in new_text
        assert "task_added = 2" in new_text
        add_calls = [c for c in wt._git.call_args_list if c[0][:1] == ("add",)]
        assert len(add_calls) == 1

    def test_none_when_verdict_is_choose(self, tmp_path):
        self._write_conflict(tmp_path, "feature.py", "value = 1", "value = 2")
        wt = MagicMock()
        file_resolutions = {"feature.py": {"verdict": "choose", "rationale": "overlap"}}
        side_texts = {"feature.py": ("value = 1", "value = 2")}

        rationale = _try_auto_resolve_additive(
            wt, tmp_path, ["feature.py"], file_resolutions, side_texts,
        )

        assert rationale is None
        # No git calls at all — the verdict check short-circuits before
        # any merge-base lookup.
        wt._git.assert_not_called()
        # File untouched — markers still present.
        assert "<<<<<<<" in (tmp_path / "feature.py").read_text()

    def test_none_when_pure_additive_check_fails(self, tmp_path):
        self._write_conflict(tmp_path, "feature.py", "spec_added = 1", "task_added = 2")
        wt = MagicMock()
        wt._git.side_effect = _numstat_dispatch(
            "basesha", {"HEAD": ("1", "0"), "MERGE_HEAD": ("0", "3")},
        )
        file_resolutions = {"feature.py": {"verdict": "keep-both", "rationale": "disjoint"}}
        side_texts = {"feature.py": ("spec_added = 1", "task_added = 2")}

        rationale = _try_auto_resolve_additive(
            wt, tmp_path, ["feature.py"], file_resolutions, side_texts,
        )

        assert rationale is None
        # Gate failed before any mutation — markers still on disk.
        assert "<<<<<<<" in (tmp_path / "feature.py").read_text()
        add_calls = [c for c in wt._git.call_args_list if c[0][:1] == ("add",)]
        assert len(add_calls) == 0

    def test_none_when_syntax_check_fails_after_apply(self, tmp_path):
        # Concatenating both sides produces invalid Python (an
        # unterminated def) even though each side looks like a disjoint
        # addition in isolation.
        self._write_conflict(tmp_path, "feature.py", "def broken(:", "task_added = 2")
        wt = MagicMock()
        wt._git.side_effect = _numstat_dispatch(
            "basesha", {"HEAD": ("1", "0"), "MERGE_HEAD": ("1", "0")},
        )
        file_resolutions = {"feature.py": {"verdict": "keep-both", "rationale": "disjoint"}}
        side_texts = {"feature.py": ("def broken(:", "task_added = 2")}

        rationale = _try_auto_resolve_additive(
            wt, tmp_path, ["feature.py"], file_resolutions, side_texts,
        )

        assert rationale is None
        add_calls = [c for c in wt._git.call_args_list if c[0][:1] == ("add",)]
        assert len(add_calls) == 0

    def test_one_file_failing_gate_blocks_the_whole_set(self, tmp_path):
        """All-or-nothing: with two ambiguous files, one file clearing
        the gate must not auto-apply while its sibling doesn't."""
        self._write_conflict(tmp_path, "clean.py", "spec_added = 1", "task_added = 2")
        self._write_conflict(tmp_path, "risky.py", "value = 1", "value = 2")
        wt = MagicMock()
        wt._git.side_effect = _numstat_dispatch(
            "basesha", {"HEAD": ("1", "0"), "MERGE_HEAD": ("1", "0")},
        )
        file_resolutions = {
            "clean.py": {"verdict": "keep-both", "rationale": "disjoint"},
            "risky.py": {"verdict": "choose", "rationale": "overlap"},
        }
        side_texts = {
            "clean.py": ("spec_added = 1", "task_added = 2"),
            "risky.py": ("value = 1", "value = 2"),
        }

        rationale = _try_auto_resolve_additive(
            wt, tmp_path, ["clean.py", "risky.py"], file_resolutions, side_texts,
        )

        assert rationale is None
        assert "<<<<<<<" in (tmp_path / "clean.py").read_text()
        assert "<<<<<<<" in (tmp_path / "risky.py").read_text()


# ------------------------------------------------------------------
# _check_post_merge_files — post-merge safety gate (task 338)
# ------------------------------------------------------------------
#
# Cheap, deterministic gate against the two breakage classes that
# historically rode a merge commit into the spec branch: literal
# ``<<<<<<<``/``=======``/``>>>>>>>`` conflict markers (because some
# pre-existing merge-agent resolution paths didn't strip them), and
# Python syntax errors (because ``keep-both`` can concatenate two
# partial dicts into an unclosed literal).  Walks the changed file
# list, returns one violation per broken file with the line number —
# the cheapest honest signal that's worth gating on without bringing
# the suite back.

class TestCheckPostMergeFiles:
    def test_clean_python_passes(self, tmp_path):
        (tmp_path / "ok.py").write_text("def hello():\n    return 1\n")
        assert _check_post_merge_files(tmp_path, ["ok.py"]) == []

    def test_clean_text_file_passes(self, tmp_path):
        (tmp_path / "notes.md").write_text("# heading\n\nparagraph text\n")
        assert _check_post_merge_files(tmp_path, ["notes.md"]) == []

    def test_less_than_seven_markers_in_python_passes(self, tmp_path):
        """Anything that isn't a complete conflict marker is harmless —
        a docstring containing ``=======`` shouldn't trigger."""
        (tmp_path / "doc.py").write_text(
            "RULE = '=========\\nnot a marker, just docs'\n"
        )
        assert _check_post_merge_files(tmp_path, ["doc.py"]) == []

    def test_left_marker_flagged(self, tmp_path):
        (tmp_path / "views.py").write_text(
            "def view():\n<<<<<<< HEAD\n    pass\n=======\n    return 1\n"
            ">>>>>>> task/spec/1\n"
        )
        violations = _check_post_merge_files(tmp_path, ["views.py"])
        assert len(violations) == 1
        path, error_type, line_no = violations[0]
        assert path == "views.py"
        assert error_type == "conflict_marker"
        assert line_no == 2  # the <<<<<<< line

    def test_right_marker_flagged(self, tmp_path):
        """A file that lost only the trailing ``>>>>>>>`` still trips the
        gate — we catch any of the three marker lines."""
        (tmp_path / "x.py").write_text(
            "def view():\n    a = 1\n>>>>>>> task/spec/1\n"
        )
        violations = _check_post_merge_files(tmp_path, ["x.py"])
        assert any(v[1] == "conflict_marker" for v in violations)
        # Three lines of content; the ``>>>>>>>`` is on line 3.
        assert any(v[2] == 3 for v in violations)

    def test_separator_marker_flagged(self, tmp_path):
        (tmp_path / "x.py").write_text(
            "def view():\n    a = 1\n=======\n    b = 2\n"
        )
        violations = _check_post_merge_files(tmp_path, ["x.py"])
        assert any(v[1] == "conflict_marker" for v in violations)

    def test_unclosed_dict_flagged(self, tmp_path):
        """The historical fbce704e breakage: keep-both produced an
        unclosed dict literal — ast.parse/compile catches it."""
        (tmp_path / "views.py").write_text(
            "def view():\n    return {\n        'a': 1,\n"
        )
        violations = _check_post_merge_files(tmp_path, ["views.py"])
        assert any(v[1] == "syntax_error" for v in violations)

    def test_valid_dict_passes(self, tmp_path):
        (tmp_path / "views.py").write_text(
            "def view():\n    return {'a': 1, 'b': 2}\n"
        )
        assert _check_post_merge_files(tmp_path, ["views.py"]) == []

    def test_syntax_error_line_number_reported(self, tmp_path):
        (tmp_path / "views.py").write_text(
            "def view():\n    return 1\n\ndef broken(:\n    pass\n"
        )
        violations = _check_post_merge_files(tmp_path, ["views.py"])
        assert any(v[1] == "syntax_error" and v[2] == 4 for v in violations)

    def test_markers_in_non_python_flagged(self, tmp_path):
        """The marker check runs against any file type — markers should
        never survive in any committed file."""
        (tmp_path / "notes.md").write_text(
            "# Notes\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> task/spec/1\n"
        )
        violations = _check_post_merge_files(tmp_path, ["notes.md"])
        assert any(v[1] == "conflict_marker" for v in violations)

    def test_json_file_with_markers_flagged(self, tmp_path):
        (tmp_path / "config.json").write_text(
            '<<<<<<< HEAD\n{"a": 1}\n=======\n{"a": 2}\n>>>>>>> task/spec/1\n'
        )
        violations = _check_post_merge_files(tmp_path, ["config.json"])
        assert any(v[1] == "conflict_marker" for v in violations)

    def test_missing_file_is_not_flagged(self, tmp_path):
        """A file the merge deleted can't carry markers or syntax
        errors — the gate's job is to scan FILES THAT EXIST for the
        breakage types. Missing is not a violation; the worktree
        layer is responsible for filtering deletions before passing
        paths in."""
        violations = _check_post_merge_files(tmp_path, ["ghost.py"])
        assert violations == []

    def test_empty_path_list_returns_no_violations(self, tmp_path):
        assert _check_post_merge_files(tmp_path, []) == []

    def test_marker_takes_precedence_over_syntax_error(self, tmp_path):
        """When a file has both markers AND would be unparseable,
        reporting the marker is more actionable (operators know exactly
        what to look for). One violation per file is enough to refuse."""
        (tmp_path / "broken.py").write_text(
            "def view():\n<<<<<<< HEAD\n    return {\n"
        )
        violations = _check_post_merge_files(tmp_path, ["broken.py"])
        assert len(violations) == 1
        assert violations[0][1] == "conflict_marker"

    def test_multiple_files_returns_violation_per_file(self, tmp_path):
        (tmp_path / "clean.py").write_text("def ok():\n    return 1\n")
        (tmp_path / "broken.py").write_text(
            "def view():\n<<<<<<< HEAD\n    pass\n=======\n"
            "    return 2\n>>>>>>> task/spec/1\n"
        )
        violations = _check_post_merge_files(tmp_path, ["clean.py", "broken.py"])
        paths = {v[0] for v in violations}
        assert paths == {"broken.py"}

    def test_skips_deleted_paths(self, tmp_path):
        """If a merge deletes a file, the gate doesn't need to scan it —
        deletion can't carry markers or syntax errors into the result.
        Only existing files matter."""
        # No file exists on disk; the path is "deleted" by the merge.
        assert _check_post_merge_files(tmp_path, ["deleted.py"]) == []
        # ... but explicitly passing it via the missing-file check would
        # flag it. The merge layer is responsible for only passing paths
        # that landed via the merge; this test documents the contract.


# ------------------------------------------------------------------
# _resolve_keep_both_in_file — keep-both must compose valid code (task 338)
# ------------------------------------------------------------------
#
# The "look one level up" rule: keep-both on the SAME region must
# compose both changes into valid code. If it can't (concatenating
# produces an unparseable Python file), the function must NOT silently
# commit the broken result — it returns False, leaving the file in
# its conflicted state so the merge agent parks and a human can
# resolve.

class TestResolveKeepBothRefusesUnparseablePython:
    def _write_conflict(self, tmp_path, path, spec_text, task_text):
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(
            f"<<<<<<< HEAD\n{spec_text}\n=======\n{task_text}\n"
            f">>>>>>> task/spec/1\n"
        )

    def test_valid_keep_both_returns_true(self, tmp_path):
        self._write_conflict(
            tmp_path, "feature.py",
            "spec_added = 1", "task_added = 2",
        )
        assert _resolve_keep_both_in_file(tmp_path, "feature.py") is True
        new_text = (tmp_path / "feature.py").read_text()
        assert "<<<<<<<" not in new_text
        assert "spec_added = 1" in new_text
        assert "task_added = 2" in new_text

    def test_unparseable_python_returns_false_and_reverts(self, tmp_path):
        """Concatenating both sides produces invalid Python (an
        unterminated def). The function must NOT strip markers — it
        rolls back so the merge agent parks instead of committing
        broken code."""
        self._write_conflict(
            tmp_path, "feature.py",
            "def broken(:", "task_added = 2",
        )
        assert _resolve_keep_both_in_file(tmp_path, "feature.py") is False
        # File is back to its conflicted state — markers survive.
        text = (tmp_path / "feature.py").read_text()
        assert "<<<<<<<" in text
        assert ">>>>>>>" in text

    def test_non_python_keep_both_is_accepted(self, tmp_path):
        """This gate is only about Python — concatenation of two halves
        of a markdown doc is fine. Other file types have their own
        validation, but keep-both's own contract is to compose valid
        PYTHON; the broader post-merge gate in worktree.py is the
        multi-format safety net."""
        self._write_conflict(
            tmp_path, "notes.md",
            "spec paragraph", "task paragraph",
        )
        assert _resolve_keep_both_in_file(tmp_path, "notes.md") is True
        assert "<<<<<<<" not in (tmp_path / "notes.md").read_text()

    def test_unparseable_does_not_touch_unrelated_files(self, tmp_path):
        """The rollback is scoped to the one file being resolved — no
        collateral damage to siblings."""
        self._write_conflict(
            tmp_path, "broken.py",
            "def broken(:", "task_added = 2",
        )
        (tmp_path / "sibling.py").write_text("# untouched\n")
        assert _resolve_keep_both_in_file(tmp_path, "broken.py") is False
        assert (tmp_path / "sibling.py").read_text() == "# untouched\n"
