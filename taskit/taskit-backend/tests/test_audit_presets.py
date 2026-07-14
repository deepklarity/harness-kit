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
        self.assertIn("roadmap-audit", ids)
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
        self.assertIn("roadmap-audit", ids)
        self.assertIn("hygiene-audit", ids)
        self.assertIn("static-autolint", ids)
        self.assertIn("security-audit", ids)

    def test_shipped_audit_presets_validate(self):
        resp = self.client.get("/api/presets/")
        by_id = {p["id"]: p for p in resp.data["presets"]}
        for pid in ("roadmap-audit", "hygiene-audit", "static-autolint", "security-audit"):
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
        for required in ("WHY", "Scope", "Acceptance", "Verify"):
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
