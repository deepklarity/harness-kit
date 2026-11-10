"""Tests for the audit-preset contract (task 217).

The contract extends the existing task_presets.json mechanism: a preset may
carry an optional `audit` block declaring evidence needs, checks, report
shape, and finding-to-task mapping. See docs/patterns/preset-inventory.md.

Scenario matrix:
  Schema validation (validate_audit_block):
    - preset without `audit` is valid (backward compat for all 27 existing presets)
    - fully-valid audit block passes
    - missing apiVersion rejected
    - wrong apiVersion rejected
    - empty triggers list rejected
    - check missing a required field rejected
    - check invalid severity rejected
    - check default severity (P2) accepted when omitted
    - duplicate check id rejected
    - invalid report section kind rejected
    - duplicate report section id rejected
    - findings missing task_template rejected
    - invalid severity_scale entry rejected
    - invalid script parse strategy rejected
    - invalid endpoint auth rejected
    - endpoint missing path rejected
    - file evidence missing check rejected
    - database evidence missing tool rejected
  Loader (load_presets):
    - the shipped file loads and validates, contains the 3 audit presets + category
    - duplicate preset id across the file rejected
    - bad audit block in the file rejected
  API:
    - GET /api/presets/ returns the audit category + the three presets
    - each shipped audit preset's contract block validates
  Preset->task creation:
    - instantiating static-autolint (CreateTaskModal path: POST /tasks/ with the
      preset's description) yields a task whose brief is self-contained
      (WHY + Scope + Acceptance + Verify present) so a fresh agent can execute
      without asking questions.
"""

import json
import tempfile
import unittest

from tasks.audit_presets import (
    AUDIT_API_VERSION,
    PresetValidationError,
    load_presets,
    validate_audit_block,
)

from .base import APITestCase


def _valid_audit():
    """A minimal-but-complete audit contract block; tests mutate copies."""
    return {
        "apiVersion": AUDIT_API_VERSION,
        "triggers": [{"keyword": "audit"}, {"manual": True}],
        "spec": {
            "evidence": {
                "scripts": [
                    {
                        "name": "board",
                        "command": "echo hi",
                        "cwd": "taskit/taskit-backend",
                        "parse": "json",
                    }
                ],
                "endpoints": [
                    {"method": "GET", "path": "/api/presets/", "auth": "env_jwt", "parse": "json"}
                ],
                "files": [{"path": "docs/x.md", "check": "exists"}],
                "databases": [
                    {"tool": "testing_tools/board_overview.py", "args": ["5"], "sections": ["json"]}
                ],
            },
            "checks": [
                {
                    "id": "c1",
                    "description": "check one",
                    "source": "board",
                    "expects": "truthy",
                    "severity_on_fail": "P1",
                    "finding_template": "f1",
                }
            ],
            "report": {
                "path": "out/{date}.md",
                "shape": [{"id": "verdict", "kind": "line", "format": "verdict line"}],
                "max_lines": 80,
                "prose_first": True,
            },
            "findings": {
                "severity_scale": ["P0", "P1", "P2", "P3", "P4"],
                "default_bucket_why": "trust",
                "task_template": {
                    "title_prefix": "AUDIT-",
                    "description_template": "WHY: ...",
                    "default_agent": "claude",
                    "proof_path": ".proof/task-<id>/",
                },
                "artifact_paths": [".proof/audit-{date}/x.txt"],
            },
        },
    }


class TestAuditPresetValidation(unittest.TestCase):
    def test_preset_without_audit_block_is_valid(self):
        validate_audit_block({"id": "x", "title": "X", "description": "d"})

    def test_valid_audit_block_passes(self):
        validate_audit_block({"id": "x", "audit": _valid_audit()})

    def test_missing_api_version_rejected(self):
        audit = _valid_audit()
        del audit["apiVersion"]
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_wrong_api_version_rejected(self):
        audit = _valid_audit()
        audit["apiVersion"] = "audit.harness.kit/v2"
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_empty_triggers_rejected(self):
        audit = _valid_audit()
        audit["triggers"] = []
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_check_missing_field_rejected(self):
        audit = _valid_audit()
        del audit["spec"]["checks"][0]["expects"]
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_check_invalid_severity_rejected(self):
        audit = _valid_audit()
        audit["spec"]["checks"][0]["severity_on_fail"] = "P9"
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_check_default_severity_p2_valid(self):
        audit = _valid_audit()
        del audit["spec"]["checks"][0]["severity_on_fail"]
        validate_audit_block({"id": "x", "audit": audit})

    def test_duplicate_check_id_rejected(self):
        audit = _valid_audit()
        audit["spec"]["checks"].append(dict(audit["spec"]["checks"][0]))
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_invalid_report_kind_rejected(self):
        audit = _valid_audit()
        audit["spec"]["report"]["shape"][0]["kind"] = "paragraph"
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_duplicate_report_section_id_rejected(self):
        audit = _valid_audit()
        audit["spec"]["report"]["shape"].append({"id": "verdict", "kind": "table"})
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_findings_missing_task_template_rejected(self):
        audit = _valid_audit()
        del audit["spec"]["findings"]["task_template"]
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_invalid_severity_scale_entry_rejected(self):
        audit = _valid_audit()
        audit["spec"]["findings"]["severity_scale"] = ["P0", "P9"]
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_invalid_script_parse_rejected(self):
        audit = _valid_audit()
        audit["spec"]["evidence"]["scripts"][0]["parse"] = "yaml"
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_invalid_endpoint_auth_rejected(self):
        audit = _valid_audit()
        audit["spec"]["evidence"]["endpoints"][0]["auth"] = "basic"
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_endpoint_missing_path_rejected(self):
        audit = _valid_audit()
        audit["spec"]["evidence"]["endpoints"] = [{"method": "GET"}]
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_file_missing_check_rejected(self):
        audit = _valid_audit()
        audit["spec"]["evidence"]["files"] = [{"path": "x.md"}]
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})

    def test_database_missing_tool_rejected(self):
        audit = _valid_audit()
        audit["spec"]["evidence"]["databases"] = [{"args": []}]
        with self.assertRaises(PresetValidationError):
            validate_audit_block({"id": "x", "audit": audit})


def _write_temp(payload):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, f)
    f.close()
    return f.name


class TestLoadPresets(unittest.TestCase):
    def _payload(self, presets):
        return {
            "version": 1,
            "categories": [
                {"slug": "audit", "name": "Audit", "description": "x", "icon": "i", "sort_order": 1}
            ],
            "presets": presets,
        }

    def test_load_real_file_validates(self):
        data = load_presets()
        ids = {p["id"] for p in data["presets"]}
        self.assertIn("hygiene-audit", ids)
        self.assertIn("static-autolint", ids)
        self.assertIn("security-audit", ids)
        cat_slugs = {c["slug"] for c in data["categories"]}
        self.assertIn("audit", cat_slugs)

    def test_load_duplicate_preset_id_rejected(self):
        path = _write_temp(
            self._payload(
                [
                    {
                        "id": "dup",
                        "title": "A",
                        "description": "d",
                        "category": "audit",
                        "icon": "i",
                        "suggested_priority": "MEDIUM",
                        "source": "s",
                        "sort_order": 1,
                    },
                    {
                        "id": "dup",
                        "title": "B",
                        "description": "d",
                        "category": "audit",
                        "icon": "i",
                        "suggested_priority": "MEDIUM",
                        "source": "s",
                        "sort_order": 2,
                    },
                ]
            )
        )
        with self.assertRaises(PresetValidationError):
            load_presets(path)

    def test_load_bad_audit_block_rejected(self):
        audit = _valid_audit()
        del audit["apiVersion"]
        path = _write_temp(
            self._payload(
                [
                    {
                        "id": "bad",
                        "title": "A",
                        "description": "d",
                        "category": "audit",
                        "icon": "i",
                        "suggested_priority": "MEDIUM",
                        "source": "s",
                        "sort_order": 1,
                        "audit": audit,
                    }
                ]
            )
        )
        with self.assertRaises(PresetValidationError):
            load_presets(path)


class TestPresetsAPI(APITestCase):
    def test_api_returns_audit_category_and_presets(self):
        resp = self.client.get("/api/presets/")
        self.assertEqual(resp.status_code, 200)
        cats = {c["slug"] for c in resp.data["categories"]}
        self.assertIn("audit", cats)
        ids = {p["id"] for p in resp.data["presets"]}
        self.assertIn("hygiene-audit", ids)
        self.assertIn("static-autolint", ids)
        self.assertIn("security-audit", ids)

    def test_shipped_audit_presets_validate(self):
        resp = self.client.get("/api/presets/")
        by_id = {p["id"]: p for p in resp.data["presets"]}
        for pid in ("hygiene-audit", "static-autolint", "security-audit"):
            self.assertIn("audit", by_id[pid], f"{pid} missing audit block")
            validate_audit_block(by_id[pid])


class TestPresetToTaskCreation(APITestCase):
    def test_instantiate_static_autolint_creates_dispatchable_brief(self):
        resp = self.client.get("/api/presets/")
        presets = {p["id"]: p for p in resp.data["presets"]}
        preset = presets["static-autolint"]
        board = self.make_board(name="Audits")
        body = {
            "board_id": board.id,
            "title": preset["title"],
            "description": preset["description"],
            "priority": preset["suggested_priority"],
            "created_by": "operator@test.com",
        }
        r = self.client.post("/tasks/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        desc = r.data["description"]
        # The brief must be self-contained: a fresh agent can execute without
        # asking questions. WHY + Scope + Acceptance + Verify are the contract.
        for required in ("WHY", "Scope", "Acceptance", "Verify"):
            self.assertIn(required, desc, f"static-autolint brief missing {required}")

    def test_instantiate_test_case_audit(self):
        resp = self.client.get("/api/presets/")
        presets = {p["id"]: p for p in resp.data["presets"]}
        self.assertIn("test-case-audit", presets)
        preset = presets["test-case-audit"]
        board = self.make_board(name="Audits")
        body = {
            "board_id": board.id,
            "title": preset["title"],
            "description": preset["description"],
            "priority": preset["suggested_priority"],
            "created_by": "operator@test.com",
        }
        r = self.client.post("/tasks/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        desc = r.data["description"]
        # The skill writes one HTML page into writeup/, not a markdown report at the root.
        for required in ("test suite", "test coverage", "gaps", "writeup/"):
            self.assertIn(required, desc.lower())

    def test_instantiate_security_audit_creates_dispatchable_brief(self):
        resp = self.client.get("/api/presets/")
        presets = {p["id"]: p for p in resp.data["presets"]}
        preset = presets["security-audit"]
        board = self.make_board(name="Audits")
        body = {
            "board_id": board.id,
            "title": preset["title"],
            "description": preset["description"],
            "priority": preset["suggested_priority"],
            "created_by": "operator@test.com",
        }
        r = self.client.post("/tasks/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        desc = r.data["description"]
        for required in ("Objective", "Scope", "Findings", "Report"):
            self.assertIn(required, desc, f"security-audit brief missing {required}")
        # The brief must name the four harness-risk-surface areas from the
        # Endor Labs wiki (security/endorlabs-gpt55-cursor-code-security.md):
        # credential mounts, token handling, sandbox egress, subprocess
        # invocations. A preset that omits any is incomplete.
        for surface in ("credential", "token", "egress", "subprocess"):
            self.assertIn(
                surface, desc.lower(),
                f"security-audit brief must name the '{surface}' surface area",
            )

    def test_security_audit_contract_requires_baseline_artifact(self):
        """The contract MUST declare a baseline artifact path so the runner
        can store + load the count and report deltas across waves."""
        resp = self.client.get("/api/presets/")
        presets = {p["id"]: p for p in resp.data["presets"]}
        preset = presets["security-audit"]
        artifacts = preset["audit"]["spec"]["findings"]["artifact_paths"]
        joined = "\n".join(artifacts)
        self.assertIn("baseline", joined,
                      "security-audit must declare a baseline artifact path")

    def test_security_audit_evidence_includes_bandit_and_harness_sweep(self):
        """The two evidence scripts named by the task (bandit for Python,
        harness-risk-surface sweep) must both appear in the preset's evidence."""
        resp = self.client.get("/api/presets/")
        presets = {p["id"]: p for p in resp.data["presets"]}
        preset = presets["security-audit"]
        scripts = preset["audit"]["spec"]["evidence"]["scripts"]
        cmds = " | ".join(s.get("command", "") for s in scripts)
        self.assertIn("bandit", cmds, "security-audit evidence must invoke bandit")
        # The harness sweep covers the four surface areas named in scope.
        self.assertTrue(
            any("microsandbox" in s.get("command", "") or "harness" in s.get("name", "").lower()
                for s in scripts),
            "security-audit evidence must include a harness-risk-surface sweep",
        )

    def test_compile_generated_markdown_reports_names_outputs_correctly(self):
        import sys
        from pathlib import Path
        harness_kit_dir = str(Path(__file__).resolve().parents[3])
        if harness_kit_dir not in sys.path:
            sys.path.insert(0, harness_kit_dir)
            
        from unittest.mock import patch, MagicMock
        from tasks.views import _compile_generated_markdown_reports
        
        task = MagicMock()
        task.title = "Test Case Audit"
        task.description = "Audit our test cases."
        task.schedule = None
        
        # Mock board and working directories
        task.board = MagicMock()
        task.board.allow_project_root_execution = True
        
        with patch("tasks.execution.utils.resolve_working_dir") as mock_resolve_dir, \
             patch("tasks.views._board_project_root") as mock_board_root, \
             patch("os.path.isdir") as mock_isdir, \
             patch("os.path.isfile") as mock_isfile, \
             patch("os.walk") as mock_walk, \
             patch("os.path.exists") as mock_exists, \
             patch("os.path.getmtime") as mock_getmtime, \
             patch("compile_audit.parse_markdown") as mock_parse, \
             patch("compile_audit.generate_html_report") as mock_gen_report, \
             patch("os.remove") as mock_remove:
             
            mock_walk.return_value = []
             
            mock_resolve_dir.return_value = "/tmp/fake-working-dir"
            mock_board_root.return_value = "/tmp/fake-board-root"
            mock_isdir.return_value = True
            
            # 1. Test case audit report path
            mock_isfile.side_effect = lambda path: path in (
                "/tmp/fake-board-root/TEST_CASE_AUDIT_REPORT.md",
            )
            mock_exists.return_value = False
            mock_parse.return_value = ("Title", {}, "Disclaimer", {})
            task.status = "DONE"
            _compile_generated_markdown_reports(task)
            
            # Verify it generated the correct html filename
            mock_gen_report.assert_any_call(
                "Title", {}, "Disclaimer", {},
                "/tmp/fake-board-root/Audit Reports/Test Case/test_case_audit_1.html",
                previous_score=None,
                previous_findings=[],
                next_idx=1
            )
            
            # Reset mocks
            mock_gen_report.reset_mock()
            mock_isfile.reset_mock()
            
            # 2. Slop audit report path
            mock_isfile.side_effect = lambda path: path in (
                "/tmp/fake-board-root/AI_SLOP_AUDIT_REPORT.md",
            )
            task.status = "DONE"
            _compile_generated_markdown_reports(task)
            
            # Verify it generated the correct html filename for slop report
            mock_gen_report.assert_any_call(
                "Title", {}, "Disclaimer", {},
                "/tmp/fake-board-root/Audit Reports/AI Slop/ai_slop_audit_1.html",
                previous_score=None,
                previous_findings=[],
                next_idx=1
            )

class TestAuditSkipGuard(unittest.TestCase):
    """Cost guard: skip re-auditing an unchanged codebase.

    Covers the matrix:
      - no report, no baseline              -> run (first audit)
      - report present, no baseline         -> seed baseline + skip (existing board)
      - report present, baseline, unchanged -> skip
      - report present, baseline, changed   -> run
      - report deleted (baseline exists)    -> run (deletion forces regeneration)
      - committed audit report              -> does not count as a code change
    """

    def _new_repo(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        def git(*a):
            subprocess.run(["git", "-C", d, *a], capture_output=True, check=True)
        git("init", "-q")
        git("config", "user.email", "t@t.com")
        git("config", "user.name", "t")
        Path(d, "a.py").write_text("x = 1\n")
        git("add", "-A")
        git("commit", "-qm", "init")
        return d

    def _board(self, wd):
        from types import SimpleNamespace
        return SimpleNamespace(working_dir=wd, id=1)

    def _write_report(self, wd):
        from pathlib import Path
        d = Path(wd, "Audit Reports", "AI Slop")
        d.mkdir(parents=True, exist_ok=True)
        (d / "ai_slop_audit_1.html").write_text("<html></html>")

    def _record_baseline(self, wd):
        import subprocess
        from pathlib import Path
        sha = subprocess.run(
            ["git", "-C", wd, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        odin = Path(wd, ".odin")
        odin.mkdir(exist_ok=True)
        (odin / ".last_audit_sha").write_text(sha)

    def _commit_change(self, wd):
        import subprocess
        from pathlib import Path
        Path(wd, "a.py").write_text("x = 2\n")
        subprocess.run(["git", "-C", wd, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", wd, "commit", "-qm", "change"], capture_output=True)

    def test_no_report_no_baseline_runs(self):
        from tasks.dag_executor import _audit_codebase_unchanged_since_last
        wd = self._new_repo()
        self.assertFalse(_audit_codebase_unchanged_since_last(self._board(wd)))

    def test_report_no_baseline_seeds_and_skips(self):
        from tasks.dag_executor import (
            _audit_codebase_unchanged_since_last,
            _audit_last_baseline_sha,
        )
        wd = self._new_repo()
        self._write_report(wd)
        self.assertTrue(_audit_codebase_unchanged_since_last(self._board(wd)))
        # Seeding recorded a baseline so the next call stays consistent.
        self.assertIsNotNone(_audit_last_baseline_sha(self._board(wd)))

    def test_report_baseline_unchanged_skips(self):
        from tasks.dag_executor import _audit_codebase_unchanged_since_last
        wd = self._new_repo()
        self._write_report(wd)
        self._record_baseline(wd)
        self.assertTrue(_audit_codebase_unchanged_since_last(self._board(wd)))

    def test_report_baseline_changed_runs(self):
        from tasks.dag_executor import _audit_codebase_unchanged_since_last
        wd = self._new_repo()
        self._write_report(wd)
        self._record_baseline(wd)
        self._commit_change(wd)
        self.assertFalse(_audit_codebase_unchanged_since_last(self._board(wd)))

    def test_report_deleted_runs_even_if_unchanged(self):
        """Deleting the HTML report forces regeneration even with unchanged code."""
        from tasks.dag_executor import _audit_codebase_unchanged_since_last
        wd = self._new_repo()
        self._record_baseline(wd)  # baseline exists, code unchanged...
        # ...but NO report present -> must run (regenerate), not skip.
        self.assertFalse(_audit_codebase_unchanged_since_last(self._board(wd)))

    def test_committed_audit_report_does_not_count_as_change(self):
        """An audit report committed to git must not look like a code change."""
        import subprocess
        from pathlib import Path
        from tasks.dag_executor import _audit_codebase_unchanged_since_last
        wd = self._new_repo()
        self._record_baseline(wd)
        rep = Path(wd, "Audit Reports", "AI Slop")
        rep.mkdir(parents=True)
        (rep / "ai_slop_audit_1.html").write_text("<html></html>")
        subprocess.run(["git", "-C", wd, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", wd, "commit", "-qm", "report"], capture_output=True)
        self.assertTrue(_audit_codebase_unchanged_since_last(self._board(wd)))

    def test_audit_category_cost_guard_isolation(self):
        """AI Slop audit report should NOT cause Test Case audit task to be skipped."""
        from pathlib import Path
        from tasks.dag_executor import _audit_codebase_unchanged_since_last
        wd = self._new_repo()
        self._record_baseline(wd)
        ai_rep = Path(wd, "Audit Reports", "AI Slop")
        ai_rep.mkdir(parents=True)
        (ai_rep / "ai_slop_audit_1.html").write_text("<html></html>")

        board = self._board(wd)
        test_case_task = type("TaskMock", (), {"title": "Test Case Audit", "description": "Use skill /hk-test-case-audit."})()
        ai_slop_task = type("TaskMock", (), {"title": "AI Slop Detection", "description": "Use skill /hk-ai-slop-detection."})()

        # AI Slop task HAS a report and baseline -> should skip
        self.assertTrue(_audit_codebase_unchanged_since_last(board, ai_slop_task))

        # Test Case task HAS NO report -> must NOT skip
        self.assertFalse(_audit_codebase_unchanged_since_last(board, test_case_task))

        # Now create a Test Case report
        tc_rep = Path(wd, "Audit Reports", "Test Case")
        tc_rep.mkdir(parents=True)
        (tc_rep / "test_case_audit_1.html").write_text("<html></html>")

        # Now Test Case task SHOULD skip
        self.assertTrue(_audit_codebase_unchanged_since_last(board, test_case_task))

    def test_parse_findings_strips_letter_prefixes(self):
        """Test that parse_findings strips prefixes like B1 —, S1 —, F1 — from title text."""
        import sys
        from pathlib import Path
        harness_kit_dir = str(Path(__file__).resolve().parents[2])
        if harness_kit_dir not in sys.path:
            sys.path.insert(0, harness_kit_dir)
        from compile_audit import parse_findings
        body = (
            "### B1 — Entire LocalRelay message path is dead\n"
            "Location: tasks/relay.py\n"
            "Likelihood: High\n"
            "Risk: High\n\n"
            "**Reasoning:** Dead code.\n"
            "**Impact:** Maintenance burden.\n\n"
            "### S2: Unprotected endpoint\n"
            "Location: tasks/views.py\n"
            "Likelihood: Medium\n"
            "Risk: Medium\n\n"
            "**Reasoning:** Missing decorator.\n"
            "**Impact:** Unauthorized access.\n"
        )
        findings = parse_findings(body)
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0]["title"], "Entire LocalRelay message path is dead")
        self.assertEqual(findings[1]["title"], "Unprotected endpoint")

    def test_remediation_prompt_unwraps_hard_newlines(self):
        """Test that generate_html_report unwraps mid-sentence line breaks in remediation prompt."""
        import sys
        from pathlib import Path
        harness_kit_dir = str(Path(__file__).resolve().parents[2])
        if harness_kit_dir not in sys.path:
            sys.path.insert(0, harness_kit_dir)
        from compile_audit import generate_html_report
        from pathlib import Path
        import tempfile
        import os
        
        rem_text = (
            "You are fixing verified frontend slop in the codebase.\n"
            "Do NOT change visual behavior. Work through\n"
            "issues in order.\n\n"
            "- Extract shared `WordSelect`. Create `shared/components/WordSelect.tsx`\n"
            "containing the single canonical view. Move\n"
            "the shared styles into it."
        )
        sections = {
            "Executive Overview": "Overview text.",
            "Detailed Findings": "### F1 Title\nLocation: a.ts\nLikelihood: High\nRisk: High\n\n```ts\ncode\n```\n**Reasoning:** r\n**Impact:** i\n",
            "AI Remediation Prompt": rem_text
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "test_report.html")
            generate_html_report("Title", {}, "Disclaimer", sections, out_file)
            html_content = Path(out_file).read_text()
            
            # Check that line breaks were unwrapped mid-sentence
            self.assertIn("Do NOT change visual behavior. Work through issues in order.", html_content)
            self.assertIn("containing the single canonical view. Move the shared styles into it.", html_content)
            self.assertIn('word-break: break-word;', html_content)


class TestSpecPresetsAPI(APITestCase):
    def test_list_spec_presets_returns_categories_and_presets(self):
        """GET /api/spec-presets/ should return 200 with categories and skill directive presets."""
        res = self.client.get("/api/spec-presets/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("version"), 1)
        categories = data.get("categories", [])
        presets = data.get("presets", [])
        self.assertTrue(len(categories) >= 4)
        self.assertTrue(len(presets) >= 16)

        # Check that directives carry skill references like /hk-
        for p in presets:
            self.assertIn("directive", p)
            self.assertIn("/hk-", p["directive"])


class TestUserControlledAuditCompilation(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board(working_dir="/tmp/test-audit-wd")
        self.board.allow_project_root_execution = True
        self.board.save()
        self.task = self.make_task(self.board, title="Security Audit", description="Run security audit")

    def test_request_audit_compilation_confirmation_posts_question(self):
        from unittest.mock import patch
        from tasks.views import _request_audit_compilation_confirmation
        from tasks.models import TaskComment, CommentType

        with patch("tasks.views._find_generated_markdown_reports") as mock_find:
            mock_find.return_value = ["/tmp/test-audit-wd/SECURITY_AUDIT_REPORT.md"]
            _request_audit_compilation_confirmation(self.task)

            self.task.refresh_from_db()
            self.assertTrue(self.task.metadata.get("has_pending_question"))
            self.assertIn("pending_compile_audit", self.task.metadata)

            comments = TaskComment.objects.filter(task=self.task, comment_type=CommentType.QUESTION)
            self.assertEqual(comments.count(), 1)
            q_comment = comments.first()
            self.assertIn("SECURITY_AUDIT_REPORT.md", q_comment.content)
            self.assertIn("Would you like to compile this into an interactive HTML report?", q_comment.content)

    def test_reply_yes_triggers_compilation_and_clears_pending(self):
        from unittest.mock import patch
        from tasks.views import _request_audit_compilation_confirmation
        from tasks.models import TaskComment, CommentType

        with patch("tasks.views._find_generated_markdown_reports") as mock_find:
            mock_find.return_value = ["/tmp/test-audit-wd/SECURITY_AUDIT_REPORT.md"]
            _request_audit_compilation_confirmation(self.task)

        self.task.refresh_from_db()
        q_comment = TaskComment.objects.filter(task=self.task, comment_type=CommentType.QUESTION).first()

        with patch("tasks.views._compile_generated_markdown_reports") as mock_compile:
            mock_compile.return_value = ["/tmp/test-audit-wd/Audit Reports/Security Audit/security_audit_1.html"]
            resp = self.client.post(
                f"/tasks/{self.task.id}/comments/{q_comment.id}/reply/",
                {
                    "author_email": "human@example.com",
                    "content": "Yes, please compile it to HTML",
                },
                format="json",
            )
            self.assertEqual(resp.status_code, 201)
            mock_compile.assert_called_once()

            self.task.refresh_from_db()
            self.assertFalse(self.task.metadata.get("has_pending_question", False))
            self.assertNotIn("pending_compile_audit", self.task.metadata)

            status_comment = TaskComment.objects.filter(
                task=self.task, author_label="audit-compiler", comment_type=CommentType.STATUS_UPDATE
            ).first()
            self.assertIsNotNone(status_comment)
            self.assertIn("security_audit_1.html", status_comment.content)

    def test_reply_no_skips_compilation(self):
        from unittest.mock import patch
        from tasks.views import _request_audit_compilation_confirmation
        from tasks.models import TaskComment, CommentType

        with patch("tasks.views._find_generated_markdown_reports") as mock_find:
            mock_find.return_value = ["/tmp/test-audit-wd/SECURITY_AUDIT_REPORT.md"]
            _request_audit_compilation_confirmation(self.task)

        self.task.refresh_from_db()
        q_comment = TaskComment.objects.filter(task=self.task, comment_type=CommentType.QUESTION).first()

        with patch("tasks.views._compile_generated_markdown_reports") as mock_compile:
            resp = self.client.post(
                f"/tasks/{self.task.id}/comments/{q_comment.id}/reply/",
                {
                    "author_email": "human@example.com",
                    "content": "No, keep markdown only",
                },
                format="json",
            )
            self.assertEqual(resp.status_code, 201)
            mock_compile.assert_not_called()

            self.task.refresh_from_db()
            self.assertFalse(self.task.metadata.get("has_pending_question", False))
            self.assertNotIn("pending_compile_audit", self.task.metadata)

            status_comment = TaskComment.objects.filter(
                task=self.task, author_label="audit-compiler", comment_type=CommentType.STATUS_UPDATE
            ).first()
            self.assertIsNotNone(status_comment)
            self.assertIn("skipped", status_comment.content.lower())


class CompileAuditQualityTests(unittest.TestCase):
    """Tests for compile_audit root formatting, cleaning, and extraction quality."""

    def test_clean_finding_title(self):
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        sys.path.insert(0, str(repo_root))
        from compile_audit import clean_finding_title

        cases = [
            ("CRITICAL: Synchronous file I/O blocks main thread", "Synchronous file I/O blocks main thread"),
            ("HIGH: N+1 DB queries in user loops", "N+1 DB queries in user loops"),
            ("P0: Database credential leakage in public endpoint", "Database credential leakage in public endpoint"),
            ("[F1] CRITICAL - Unbounded memory allocation", "Unbounded memory allocation"),
            ("F2: MEDIUM: Dead state variables", "Dead state variables"),
            ("`Blocker:` Heavy computation inside render hook", "Heavy computation inside render hook"),
            ("### F3: Low: Unused import statement", "Unused import statement"),
        ]
        for raw, expected in cases:
            self.assertEqual(clean_finding_title(raw), expected)

    def test_clean_finding_location(self):
        from compile_audit import clean_finding_location

        cases = [
            (
                "DrawingCanvas.tsx:215-222 (Multiple points processed in tight loop)",
                ("DrawingCanvas.tsx:215-222", "Multiple points processed in tight loop"),
            ),
            (
                "src/api/auth.py:45-50 // Insecure token generation",
                ("src/api/auth.py:45-50", "Insecure token generation"),
            ),
            (
                "components/Sidebar.tsx:10-25 - Comment about navigation",
                ("components/Sidebar.tsx:10-25", "Comment about navigation"),
            ),
            (
                "utils/helpers.ts:100",
                ("utils/helpers.ts:100", ""),
            ),
        ]
        for raw, (expected_loc, expected_cmt) in cases:
            loc, cmt = clean_finding_location(raw)
            self.assertEqual(loc, expected_loc)
            self.assertEqual(cmt, expected_cmt)

    def test_extract_code_from_file_if_needed(self):
        import tempfile
        import os
        from compile_audit import extract_code_from_file_if_needed

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = os.path.join(tmpdir, "sample.ts")
            with open(sample_file, "w") as f:
                f.write("\n".join([f"line_{i} = {i};" for i in range(1, 30)]))

            # Case 1: Existing snippet is a comment placeholder -> should extract from disk
            placeholder = "// Location: sample.ts:5-8\n// Evidence pattern: something"
            extracted = extract_code_from_file_if_needed("sample.ts:5-8", placeholder, tmpdir)
            self.assertIn("line_5 = 5;", extracted)
            self.assertIn("line_8 = 8;", extracted)

            # Case 2: Existing snippet is genuine code -> should keep existing snippet
            genuine = "const x = compute();\nreturn x;"
            kept = extract_code_from_file_if_needed("sample.ts:5-8", genuine, tmpdir)
            self.assertEqual(kept, genuine)

    def test_findings_cap_at_ten_and_html_layout(self):
        import tempfile
        import os
        from compile_audit import generate_html_report

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "performance_audit_1.html")
            
            # Generate 14 findings in markdown body
            findings_md = ""
            for i in range(1, 15):
                findings_md += f"""
### F{i} HIGH: Test bottleneck number {i}
Location: src/app/perf_{i}.ts:10-20 (Comment for finding {i})
Likelihood: High
Risk: High

```typescript
// Location: src/app/perf_{i}.ts:10-20
// Evidence pattern: heavy loop
```

**Reasoning:** Reasoning for finding {i}
**Impact:** Latency impact for finding {i}
"""
            sections = {
                "Executive Overview": "### 1.1 Runtime Overview\nReal performance audit of the application assessing runtime characteristics.\n\nOverall score: 6/10",
                "Detailed Findings": findings_md,
                "Cross-Repository Pattern Observations": "",
                "Stable or Intentional Areas": "",
                "AI Remediation Prompt": "Fix all findings in order.",
            }
            generate_html_report(
                "Performance Audit Report",
                {},
                "Disclaimer",
                sections,
                out_file,
            )

            with open(out_file, "r") as f:
                content = f.read()

            # Verify Findings Cap of 10
            self.assertIn("F10", content)
            self.assertNotIn("F11", content)
            self.assertNotIn("F12", content)
            self.assertNotIn("F13", content)
            self.assertNotIn("F14", content)

            # Verify Title Cleanliness (no "HIGH: Test bottleneck")
            self.assertNotIn("HIGH: Test bottleneck", content)
            self.assertIn("Test bottleneck number 1", content)

            # Verify Executive Overview stripped subheaders
            self.assertNotIn("### 1.1", content)
            self.assertNotIn("Runtime Overview", content)
            self.assertIn("Real performance audit of the application", content)

            # Verify Scorecard Table Layout fixed and clean widths
            self.assertIn('table style="table-layout: fixed; width: 100%;"', content)
            self.assertIn('style="width: 42%;', content)
            self.assertIn('style="width: 28%;', content)

    def test_all_11_audit_presets_recognized_by_is_auditing_task(self):
        from tasks.audit_presets import is_auditing_task
        all_presets = [
            {"title": "Web App UI Validation", "description": "Use skill /hk-web-app-ui-validation..."},
            {"title": "Mobile App UI Validation", "description": "Use skill /hk-mobile-app-ui-validation..."},
            {"title": "Chart Validation", "description": "Use skill /hk-chart-validation..."},
            {"title": "UI Gaps Analysis", "description": "Use skill /hk-ui-gaps-analysis..."},
            {"title": "Website UX Audit", "description": "Use skill /hk-website-ux-audit..."},
            {"title": "AI Slop Detection", "description": "Use skill /hk-ai-slop-detection..."},
            {"title": "Backend Slop Review", "description": "Use skill /hk-backend-slop-review..."},
            {"title": "Frontend Slop Review", "description": "Use skill /hk-frontend-slop-review..."},
            {"title": "Performance Audit", "description": "Use skill /hk-performance-audit..."},
            {"title": "Security Audit", "description": "Use skill /hk-security-audit..."},
            {"title": "Test Case Audit", "description": "Use skill /hk-test-case-audit..."},
        ]
        for p in all_presets:
            self.assertTrue(is_auditing_task(p), f"Preset {p['title']} must be recognized as an audit task")
            self.assertTrue(is_auditing_task({"title": p["title"]}), f"Title {p['title']} alone must be recognized")



