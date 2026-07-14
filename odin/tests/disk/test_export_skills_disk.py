"""Disk-level tests for odin.export_skills: real preset file, real scratch dir.

Scenario matrix:
  - exporting the shipped, curated task_presets.json writes one hk-<id>/SKILL.md
    per preset into a scratch dir
  - every written file parses (frontmatter + body) via parse_skill_md
  - every written file's frontmatter carries the generated marker and an
    hk-prefixed name matching its directory
  - regenerating into the same directory is idempotent (byte-identical files,
    same file set)
  - exporting refuses to clobber a hand-authored file that collides on name
    and lacks the generated marker
"""

from pathlib import Path

import pytest

from odin.export_skills import ExportError, export_presets_to_skills, parse_skill_md

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESETS_PATH = REPO_ROOT / "taskit" / "taskit-backend" / "data" / "task_presets.json"


class TestExportShippedPresets:
    def test_writes_one_skill_per_preset(self, tmp_path):
        import json

        preset_count = len(json.loads(PRESETS_PATH.read_text())["presets"])
        written = export_presets_to_skills(PRESETS_PATH, tmp_path)
        assert len(written) == preset_count
        for path in written:
            assert path.name == "SKILL.md"
            assert path.parent.parent == tmp_path

    def test_every_generated_skill_parses(self, tmp_path):
        written = export_presets_to_skills(PRESETS_PATH, tmp_path)
        assert written, "expected at least one generated skill"
        for path in written:
            frontmatter, body = parse_skill_md(path)
            assert frontmatter["generated"] is True
            assert frontmatter["name"] == path.parent.name
            assert frontmatter["name"].startswith("hk-")
            assert body.strip(), f"{path}: empty body"

    def test_regeneration_is_idempotent(self, tmp_path):
        first = export_presets_to_skills(PRESETS_PATH, tmp_path)
        first_contents = {p: p.read_text() for p in first}

        second = export_presets_to_skills(PRESETS_PATH, tmp_path)
        second_contents = {p: p.read_text() for p in second}

        assert {p for p in first} == {p for p in second}
        assert first_contents == second_contents


class TestExportOverwriteGuard:
    def test_refuses_to_clobber_hand_authored_collision(self, tmp_path):
        collision_dir = tmp_path / "hk-bug-report"
        collision_dir.mkdir()
        (collision_dir / "SKILL.md").write_text("hand-authored, no marker\n")

        preset_file = tmp_path / "presets.json"
        preset_file.write_text(
            '{"presets": [{"id": "bug-report", "title": "Bug Report", '
            '"description": "scan for bugs"}]}'
        )

        with pytest.raises(ExportError):
            export_presets_to_skills(preset_file, tmp_path)

    def test_overwrites_a_previously_generated_file(self, tmp_path):
        preset_file = tmp_path / "presets.json"
        preset_file.write_text(
            '{"presets": [{"id": "bug-report", "title": "Bug Report", '
            '"description": "v1"}]}'
        )
        export_presets_to_skills(preset_file, tmp_path)

        preset_file.write_text(
            '{"presets": [{"id": "bug-report", "title": "Bug Report", '
            '"description": "v2"}]}'
        )
        export_presets_to_skills(preset_file, tmp_path)

        _, body = parse_skill_md(tmp_path / "hk-bug-report" / "SKILL.md")
        assert "v2" in body
        assert "v1" not in body
