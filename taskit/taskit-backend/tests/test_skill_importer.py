"""Tests for Skill Importer and dynamic preset synchronization (tasks/skill_importer.py)."""

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from tasks import skill_importer


class SkillImporterTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = Path(self.temp_dir) / ".claude" / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        self.patcher = mock.patch.object(skill_importer, "get_skills_dir", return_value=self.skills_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_classify_skill_relevance(self):
        # 1. Explicit frontmatter override
        task_override = {"frontmatter": {"preset_target": "task"}, "name": "foo"}
        self.assertEqual(skill_importer.classify_skill_relevance(task_override)["preset_target"], "task")

        none_override = {"frontmatter": {"preset_target": "none"}, "name": "foo"}
        self.assertEqual(skill_importer.classify_skill_relevance(none_override)["preset_target"], "none")

        disable_override = {"frontmatter": {"disable-model-invocation": True}, "name": "foo"}
        self.assertEqual(skill_importer.classify_skill_relevance(disable_override)["preset_target"], "none")

        # 2. Automated heuristics
        ux_audit = {
            "name": "hk-website-ux-audit",
            "title": "Website UX Audit",
            "description": "Perform UX audit of website and generate report.",
            "frontmatter": {},
        }
        ux_class = skill_importer.classify_skill_relevance(ux_audit)
        self.assertEqual(ux_class["preset_target"], "task")
        self.assertTrue(ux_class["is_task_preset"])
        self.assertFalse(ux_class["is_spec_preset"])

        prevent_slop = {
            "name": "hk-code-quality-audit",
            "title": "Code Quality Audit",
            "description": "Audit the code quality of a directory and write up dead code and generated-looking code.",
            "frontmatter": {},
        }
        slop_class = skill_importer.classify_skill_relevance(prevent_slop)
        self.assertEqual(slop_class["preset_target"], "task")
        self.assertTrue(slop_class["is_task_preset"])

        interactive_skill = {
            "name": "hk-codebase-design",
            "title": "Codebase Design",
            "description": "Shared vocabulary for designing deep modules.",
            "frontmatter": {},
        }
        interactive_class = skill_importer.classify_skill_relevance(interactive_skill)
        self.assertEqual(interactive_class["preset_target"], "none")
        self.assertTrue(interactive_class["is_backend_only"])

    def test_score_skill_usefulness(self):
        # 1. Valid software engineering skill -> High score, is_useful = True
        tdd_skill = {
            "name": "test-driven-development",
            "title": "Test Driven Development",
            "description": "Enforce strict TDD discipline and automated testing for feature implementation.",
            "body": "## Process\nWrite failing test first.\n```python\ndef test_foo(): assert True\n```",
            "frontmatter": {},
        }
        is_useful, score, details = skill_importer.score_skill_usefulness(tdd_skill)
        self.assertTrue(is_useful)
        self.assertGreaterEqual(score, 45)
        self.assertIn("testing", details["high_matches"])

        # 2. Non-engineering anti-pattern (Recipe / Workout) -> is_useful = False
        recipe_skill = {
            "name": "pasta-recipe-generator",
            "title": "Pasta Recipe Generator",
            "description": "Generate delicious cooking recipes and ingredients rules for pasta.",
            "body": "## Process\nStep 1: Boil water and add salt.",
            "frontmatter": {},
        }
        is_useful_recipe, score_recipe, details_recipe = skill_importer.score_skill_usefulness(recipe_skill)
        self.assertFalse(is_useful_recipe)
        self.assertEqual(score_recipe, 0)
        self.assertIn("anti-pattern", details_recipe.get("reason", ""))

        # 3. Personal scratch prompt (ask-matt) -> is_useful = False
        matt_skill = {
            "name": "ask-matt",
            "title": "Ask Matt",
            "description": "Personal helper prompt.",
            "body": "Ask questions.",
            "frontmatter": {},
        }
        is_useful_matt, score_matt, details_matt = skill_importer.score_skill_usefulness(matt_skill)
        self.assertFalse(is_useful_matt)
        self.assertEqual(score_matt, 0)
        self.assertIn("Blacklisted", details_matt.get("reason", ""))

        # 4. Explicit frontmatter overrides
        useful_override = {"frontmatter": {"useful": True}, "name": "custom-skill"}
        self.assertTrue(skill_importer.is_useful_skill(useful_override))

        skip_override = {"frontmatter": {"skip_import": True}, "name": "custom-skill"}
        self.assertFalse(skill_importer.is_useful_skill(skip_override))

    def test_derive_source_prefix(self):
        # GitHub repo URLs
        self.assertEqual(skill_importer.derive_source_prefix("https://github.com/mattpocock/skills"), "matt-")
        self.assertEqual(skill_importer.derive_source_prefix("https://github.com/anthropics/anthropic-quickstarts"), "anthropic-")
        self.assertEqual(skill_importer.derive_source_prefix("https://github.com/vercel/ai"), "vercel-")
        self.assertEqual(skill_importer.derive_source_prefix("https://github.com/openai/swarm"), "openai-")

        # Single markdown file uploads / URLs
        self.assertEqual(skill_importer.derive_source_prefix("File Upload: SKILL.md", filename="SKILL.md"), "md-")
        self.assertEqual(skill_importer.derive_source_prefix("https://raw.githubusercontent.com/user/repo/main/SKILL.md"), "md-")

        # Uploaded zip archives
        self.assertEqual(skill_importer.derive_source_prefix("File Upload: archive.zip", filename="archive.zip"), "zip-")
        self.assertEqual(skill_importer.derive_source_prefix("File Upload: react-skills.zip", filename="react-skills.zip"), "react-")

    def test_apply_skill_prefix(self):
        self.assertEqual(skill_importer.apply_skill_prefix("ts-reset", "matt-"), "matt-ts-reset")
        self.assertEqual(skill_importer.apply_skill_prefix("hk-ts-reset", "matt-"), "matt-ts-reset")
        self.assertEqual(skill_importer.apply_skill_prefix("matt-ts-reset", "matt-"), "matt-ts-reset")
        self.assertEqual(skill_importer.apply_skill_prefix("pdf-parser", "md-"), "md-pdf-parser")

    def test_import_and_duplicate_detection(self):
        source_dir = Path(self.temp_dir) / "source_skills"
        my_skill = source_dir / "custom-security-audit"
        my_skill.mkdir(parents=True, exist_ok=True)
        (my_skill / "SKILL.md").write_text(
            "---\nname: custom-security-audit\ndescription: Perform security audit on codebase.\n---\n# Security Audit\nRun security audit.",
            encoding="utf-8",
        )

        # 1st import -> should succeed with prefix from source_skills folder ('source-')
        res = skill_importer.import_skills_from_source(str(source_dir), skills_dir=self.skills_dir)
        self.assertEqual(res["status"], "success")
        self.assertIn("source-custom-security-audit", res["imported"])
        self.assertEqual(len(res["skipped"]), 0)
        self.assertTrue((self.skills_dir / "source-custom-security-audit" / "SKILL.md").exists())

        # 2nd import without force -> should skip with duplicate message
        res2 = skill_importer.import_skills_from_source(str(source_dir), skills_dir=self.skills_dir)
        self.assertEqual(res2["status"], "success")
        self.assertEqual(len(res2["imported"]), 0)
        self.assertEqual(len(res2["skipped"]), 1)
        self.assertEqual(res2["skipped"][0]["name"], "source-custom-security-audit")
        self.assertIn("already exists", res2["skipped"][0]["reason"])

    def test_list_and_delete_skills_api(self):
        # Create 2 test skills marked imported: true
        s1 = self.skills_dir / "custom-security-audit"
        s1.mkdir(parents=True, exist_ok=True)
        (s1 / "SKILL.md").write_text(
            "---\nname: custom-security-audit\ndescription: Perform security audit.\nimported: true\n---\n# Security Audit\nAudit.",
            encoding="utf-8",
        )

        s2 = self.skills_dir / "custom-subagent-handoff"
        s2.mkdir(parents=True, exist_ok=True)
        (s2 / "SKILL.md").write_text(
            "---\nname: custom-subagent-handoff\ndescription: Internal handoff helper.\nimported: true\n---\n# Handoff\nHelper.",
            encoding="utf-8",
        )

        # GET /api/skills/
        resp = self.client.get("/api/skills/")
        self.assertEqual(resp.status_code, 200)
        skills = resp.data["skills"]
        skill_names = {s["name"] for s in skills}
        self.assertIn("custom-security-audit", skill_names)
        self.assertIn("custom-subagent-handoff", skill_names)

        # DELETE /api/skills/custom-security-audit/
        del_resp = self.client.delete("/api/skills/custom-security-audit/")
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse((self.skills_dir / "custom-security-audit").exists())

    def test_dynamic_preset_filtering(self):
        # Create a skill (by default preset_enabled is False)
        task_skill = self.skills_dir / "custom-performance-audit"
        task_skill.mkdir(parents=True, exist_ok=True)
        (task_skill / "SKILL.md").write_text(
            "---\nname: custom-performance-audit\ndescription: Perform performance audit on application.\n---\n# Performance Audit",
            encoding="utf-8",
        )

        # Initially unchecked/disabled -> Not in Task Presets or Spec Presets
        resp_presets = self.client.get("/api/presets/")
        self.assertEqual(resp_presets.status_code, 200)
        preset_ids = {p["id"] for p in resp_presets.data["presets"]}
        self.assertNotIn("custom-performance-audit", preset_ids)

        resp_spec = self.client.get("/api/spec-presets/")
        self.assertEqual(resp_spec.status_code, 200)
        spec_preset_ids = {p["id"] for p in resp_spec.data["presets"]}
        self.assertNotIn("custom-performance-audit", spec_preset_ids)

        # Toggle preset_enabled to True via POST /api/skills/custom-performance-audit/toggle-preset/
        toggle_resp = self.client.post("/api/skills/custom-performance-audit/toggle-preset/", {"enabled": True}, format="json")
        self.assertEqual(toggle_resp.status_code, 200)
        self.assertTrue(toggle_resp.data["preset_enabled"])

        # Now skill should be added to BOTH Task Presets and Spec Presets
        resp_presets_enabled = self.client.get("/api/presets/")
        preset_ids_enabled = {p["id"] for p in resp_presets_enabled.data["presets"]}
        self.assertIn("custom-performance-audit", preset_ids_enabled)

        resp_spec_enabled = self.client.get("/api/spec-presets/")
        spec_preset_ids_enabled = {p["id"] for p in resp_spec_enabled.data["presets"]}
        self.assertIn("custom-performance-audit", spec_preset_ids_enabled)

        # Toggle back to False
        toggle_off = self.client.post("/api/skills/custom-performance-audit/toggle-preset/", {"enabled": False}, format="json")
        self.assertEqual(toggle_off.status_code, 200)
        self.assertFalse(toggle_off.data["preset_enabled"])

        # Removed from both lists again
        resp_presets_off = self.client.get("/api/presets/")
        self.assertNotIn("custom-performance-audit", {p["id"] for p in resp_presets_off.data["presets"]})

        resp_spec_off = self.client.get("/api/spec-presets/")
        self.assertNotIn("custom-performance-audit", {p["id"] for p in resp_spec_off.data["presets"]})

    def test_import_non_audit_skill_and_reimport_after_deletion(self):
        source_dir = Path(self.temp_dir) / "mattpocock_skills"
        s1 = source_dir / "ts-reset"
        s1.mkdir(parents=True, exist_ok=True)
        (s1 / "SKILL.md").write_text(
            "---\nname: ts-reset\ndescription: Improve TypeScript default rules.\n---\n# TS Reset",
            encoding="utf-8",
        )
        s2 = source_dir / "react-best-practices"
        s2.mkdir(parents=True, exist_ok=True)
        (s2 / "SKILL.md").write_text(
            "---\nname: react-best-practices\ndescription: Modern React guidelines.\n---\n# React Best Practices",
            encoding="utf-8",
        )

        # 1. Initial import of both skills (prefixed with matt-)
        res = skill_importer.import_skills_from_source(str(source_dir), skills_dir=self.skills_dir)
        self.assertEqual(res["status"], "success")
        self.assertIn("matt-ts-reset", res["imported"])
        self.assertIn("matt-react-best-practices", res["imported"])
        self.assertTrue((self.skills_dir / "matt-ts-reset").exists())
        self.assertTrue((self.skills_dir / "matt-react-best-practices").exists())

        # 2. Delete 1 skill (matt-ts-reset)
        del_res = skill_importer.delete_installed_skill("matt-ts-reset", skills_dir=self.skills_dir)
        self.assertEqual(del_res["status"], "success")
        self.assertFalse((self.skills_dir / "matt-ts-reset").exists())

        # 3. Re-run import without force -> matt-ts-reset should re-download cleanly, matt-react-best-practices should skip as existing
        res2 = skill_importer.import_skills_from_source(str(source_dir), skills_dir=self.skills_dir)
        self.assertEqual(res2["status"], "success")
        self.assertIn("matt-ts-reset", res2["imported"])
        self.assertTrue((self.skills_dir / "matt-ts-reset").exists())
        self.assertEqual(len(res2["skipped"]), 1)
        self.assertEqual(res2["skipped"][0]["name"], "matt-react-best-practices")

    def test_parse_github_url(self):
        base, subpath = skill_importer._parse_github_url("https://github.com/mattpocock/skills/tree/main/skills/ts-reset")
        self.assertEqual(base, "https://github.com/mattpocock/skills.git")
        self.assertEqual(subpath, "skills/ts-reset")

        base2, subpath2 = skill_importer._parse_github_url("https://github.com/mattpocock/skills")
        self.assertEqual(base2, "https://github.com/mattpocock/skills")
        self.assertIsNone(subpath2)

    def test_upload_md_file_api(self):
        from io import BytesIO
        md_file = BytesIO(b"---\nname: custom-pdf-skill\ndescription: Parse PDFs\n---\n# PDF Skill")
        md_file.name = "pdf_skill.md"

        resp = self.client.post("/api/skills/upload/", {"file": md_file}, format="multipart")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "success")
        self.assertIn("md-custom-pdf-skill", resp.data["imported"])
        self.assertTrue((self.skills_dir / "md-custom-pdf-skill" / "SKILL.md").exists())

    def test_detect_and_install_dependencies_api(self):
        skill_dir = self.skills_dir / "custom-pdf-exporter"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: custom-pdf-exporter\ndescription: Export PDFs\n---\n# PDF Exporter\nRun pip install pypdf",
            encoding="utf-8",
        )
        (skill_dir / "requirements.txt").write_text("pypdf\n", encoding="utf-8")

        deps = skill_importer.detect_skill_dependencies(skill_dir)
        self.assertIn("pypdf", deps["python"])

        resp = self.client.post("/api/skills/custom-pdf-exporter/install-deps/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ready")

    def test_zip_slip_prevention(self):
        import zipfile
        from io import BytesIO

        # Construct malicious zip containing path traversal entry
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as z:
            z.writestr("../../malicious_file.txt", "evil payload")
        zip_buf.seek(0)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                with zipfile.ZipFile(zip_buf, "r") as z:
                    skill_importer.safe_extract_zip(z, Path(tmp))
            self.assertIn("Security Exception", str(ctx.exception))

    def test_upload_file_size_limit_exceeded(self):
        from io import BytesIO
        big_file = BytesIO(b"0" * (15 * 1024 * 1024 + 1))
        big_file.name = "large_archive.zip"
        big_file.size = 15 * 1024 * 1024 + 1

        resp = self.client.post("/api/skills/upload/", {"file": big_file}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("exceeds maximum allowed upload size of 15 MB", resp.data["error"])

    def test_sanitize_skill_name(self):
        self.assertEqual(skill_importer.sanitize_skill_name("../../etc/passwd"), "passwd")
        self.assertEqual(skill_importer.sanitize_skill_name("../../../malicious_name"), "malicious_name")
        self.assertEqual(skill_importer.sanitize_skill_name("valid-skill-name"), "valid-skill-name")

    def test_force_install_purges_rejected_skills(self):
        # 1. Save a rejected skill in cache
        rejected = [{
            "name": "setup-pre-commit",
            "title": "Setup Pre-Commit Hooks",
            "description": "Set up pre-commit hooks",
            "directory": "setup-pre-commit",
            "source": "https://github.com/mattpocock/skills",
            "category": "General Skill",
            "reason": "Test rejected",
            "usefulness_score": 10
        }]
        skill_importer.save_rejected_skills(rejected, self.skills_dir)
        self.assertEqual(len(skill_importer.load_rejected_skills(self.skills_dir)), 1)

        # 2. Simulate force installing this skill (creates hk-setup-pre-commit directory)
        dest = self.skills_dir / "hk-setup-pre-commit"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text("---\nname: setup-pre-commit\n---\n# Pre-Commit", encoding="utf-8")

        # 3. Sync cache
        skill_importer.save_rejected_skills([], self.skills_dir)

        # 4. Verify rejected list no longer contains setup-pre-commit
        rejected_after = skill_importer.load_rejected_skills(self.skills_dir)
        self.assertEqual(len(rejected_after), 0)

    def test_delete_all_installed_skills(self):
        # Create multiple installed skills
        s1 = self.skills_dir / "custom-security-audit"
        s1.mkdir(parents=True, exist_ok=True)
        (s1 / "SKILL.md").write_text("---\nname: custom-security-audit\nimported: true\n---\n# Sec", encoding="utf-8")

        s2 = self.skills_dir / "custom-subagent-handoff"
        s2.mkdir(parents=True, exist_ok=True)
        (s2 / "SKILL.md").write_text("---\nname: custom-subagent-handoff\nimported: true\n---\n# Handoff", encoding="utf-8")

        self.assertTrue(s1.exists())
        self.assertTrue(s2.exists())

        # DELETE /api/skills/delete-all/
        resp = self.client.delete("/api/skills/delete-all/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "success")
        self.assertEqual(resp.data["deleted_count"], 2)
        self.assertFalse(s1.exists())
        self.assertFalse(s2.exists())
        self.assertEqual(len(resp.data["skills"]), 0)

    def test_clear_rejected_skills(self):
        # Seed rejected skills cache
        rejected = [{
            "name": "sample-rejected",
            "title": "Sample Rejected",
            "source": "https://github.com/example/skills",
            "usefulness_score": 5
        }]
        skill_importer.save_rejected_skills(rejected, self.skills_dir)
        self.assertEqual(len(skill_importer.load_rejected_skills(self.skills_dir)), 1)

        # DELETE /api/skills/clear-rejected/
        resp = self.client.delete("/api/skills/clear-rejected/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "success")
        self.assertEqual(len(resp.data["rejected_skills"]), 0)
        self.assertEqual(len(skill_importer.load_rejected_skills(self.skills_dir)), 0)

