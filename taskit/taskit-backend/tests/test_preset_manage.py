"""Tests for POST /api/presets/manage/ (task 260, preset UX scope addition).

data/task_presets.json stays the ONLY canonical source — every add / update /
delete / disable / enable writes straight to that file (no DB shadow copy),
so every preset change shows up as a git diff. One schema
(validate_preset_shape) gates both hand-added and UI-added presets, the same
one the skill exporter relies on, so every preset stays exportable by
construction.

Scenario matrix:
  add:
    - valid preset appended, appears in a fresh load
    - duplicate id rejected (409)
    - missing required field rejected (400)
    - preset carrying a bad audit block rejected (400)
  update:
    - existing preset's fields replaced
    - unknown id rejected (404)
    - malformed replacement rejected (400)
  delete:
    - preset removed from the file entirely
    - unknown id rejected (404)
  disable / enable:
    - disable sets disabled=true and hides it from the default GET
    - GET ?include_disabled=1 still shows it
    - enable clears the flag and it reappears by default
  contract:
    - invalid action rejected (400)
    - every mutation is a literal write to disk (re-reading the file off
      disk, not just the in-memory response, reflects the change)
"""

import json
from pathlib import Path
from unittest import mock

from tasks import audit_presets
from tests.base import APITestCase


def _seed_data():
    return {
        "version": 1,
        "categories": [
            {"slug": "code-review", "name": "Code Review", "description": "d", "icon": "code", "sort_order": 1},
        ],
        "presets": [
            {
                "id": "seed-one",
                "title": "Seed One",
                "description": "d1",
                "category": "code-review",
                "icon": "i",
                "suggested_priority": "MEDIUM",
                "source": "s",
                "sort_order": 1,
            }
        ],
    }


class PresetManageTestCase(APITestCase):
    """Points audit_presets._default_path() at a scratch file for the duration of each test."""

    def setUp(self):
        super().setUp()
        self._tmpdir = self.enterContext(mock.patch.object(audit_presets, "_default_path"))
        import tempfile

        self._scratch_dir = tempfile.mkdtemp()
        self._scratch_path = Path(self._scratch_dir) / "task_presets.json"
        self._scratch_path.write_text(json.dumps(_seed_data(), indent=2))
        audit_presets._default_path.return_value = self._scratch_path

    def on_disk(self):
        return json.loads(self._scratch_path.read_text())


class TestAddPreset(PresetManageTestCase):
    def test_valid_preset_appended(self):
        resp = self.client.post(
            "/api/presets/manage/",
            {
                "action": "add",
                "preset": {
                    "id": "new-one",
                    "title": "New One",
                    "description": "does a thing",
                    "category": "code-review",
                },
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        ids = {p["id"] for p in self.on_disk()["presets"]}
        self.assertEqual(ids, {"seed-one", "new-one"})

    def test_duplicate_id_rejected(self):
        resp = self.client.post(
            "/api/presets/manage/",
            {
                "action": "add",
                "preset": {
                    "id": "seed-one",
                    "title": "Dup",
                    "description": "d",
                    "category": "code-review",
                },
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(len(self.on_disk()["presets"]), 1)

    def test_missing_required_field_rejected(self):
        resp = self.client.post(
            "/api/presets/manage/",
            {"action": "add", "preset": {"id": "no-desc", "title": "T", "category": "code-review"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(self.on_disk()["presets"]), 1)

    def test_bad_audit_block_rejected(self):
        resp = self.client.post(
            "/api/presets/manage/",
            {
                "action": "add",
                "preset": {
                    "id": "bad-audit",
                    "title": "T",
                    "description": "d",
                    "category": "audit",
                    "audit": {"apiVersion": "wrong/v0", "metadata": {}, "spec": {}},
                },
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(self.on_disk()["presets"]), 1)


class TestUpdatePreset(PresetManageTestCase):
    def test_existing_preset_replaced(self):
        resp = self.client.post(
            "/api/presets/manage/",
            {
                "action": "update",
                "preset": {
                    "id": "seed-one",
                    "title": "Seed One Renamed",
                    "description": "d1 updated",
                    "category": "code-review",
                },
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        on_disk = self.on_disk()["presets"][0]
        self.assertEqual(on_disk["title"], "Seed One Renamed")
        self.assertEqual(on_disk["description"], "d1 updated")

    def test_unknown_id_rejected(self):
        resp = self.client.post(
            "/api/presets/manage/",
            {
                "action": "update",
                "preset": {"id": "ghost", "title": "T", "description": "d", "category": "code-review"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_malformed_replacement_rejected(self):
        resp = self.client.post(
            "/api/presets/manage/",
            {"action": "update", "preset": {"id": "seed-one", "title": "T"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.on_disk()["presets"][0]["title"], "Seed One")


class TestDeletePreset(PresetManageTestCase):
    def test_preset_removed_entirely(self):
        resp = self.client.post("/api/presets/manage/", {"action": "delete", "id": "seed-one"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.on_disk()["presets"], [])

    def test_unknown_id_rejected(self):
        resp = self.client.post("/api/presets/manage/", {"action": "delete", "id": "ghost"}, format="json")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(len(self.on_disk()["presets"]), 1)


class TestDisableEnablePreset(PresetManageTestCase):
    def test_disable_hides_from_default_list_but_not_include_disabled(self):
        resp = self.client.post("/api/presets/manage/", {"action": "disable", "id": "seed-one"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(self.on_disk()["presets"][0]["disabled"])

        default_resp = self.client.get("/api/presets/")
        self.assertEqual([p["id"] for p in default_resp.data["presets"]], [])

        all_resp = self.client.get("/api/presets/?include_disabled=1")
        self.assertEqual([p["id"] for p in all_resp.data["presets"]], ["seed-one"])

    def test_enable_clears_flag_and_it_reappears(self):
        self.client.post("/api/presets/manage/", {"action": "disable", "id": "seed-one"}, format="json")
        resp = self.client.post("/api/presets/manage/", {"action": "enable", "id": "seed-one"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertNotIn("disabled", self.on_disk()["presets"][0])

        default_resp = self.client.get("/api/presets/")
        self.assertEqual([p["id"] for p in default_resp.data["presets"]], ["seed-one"])


class TestManageContract(PresetManageTestCase):
    def test_invalid_action_rejected(self):
        resp = self.client.post("/api/presets/manage/", {"action": "explode", "id": "seed-one"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_mutation_is_a_literal_disk_write(self):
        """Re-reading the file off disk (not just the HTTP response) must
        reflect the change — no DB-backed shadow copy."""
        self.client.post(
            "/api/presets/manage/",
            {
                "action": "add",
                "preset": {"id": "disk-check", "title": "T", "description": "d", "category": "code-review"},
            },
            format="json",
        )
        raw = self._scratch_path.read_text()
        parsed = json.loads(raw)
        self.assertIn("disk-check", {p["id"] for p in parsed["presets"]})
