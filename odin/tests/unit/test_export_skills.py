"""Pure-function tests for odin.export_skills rendering + parsing (no disk I/O)."""

import pytest

from odin.export_skills import ExportError, parse_skill_md_text, render_skill_markdown

PRESET = {
    "id": "bug-report",
    "title": "Bug Report",
    "description": "WHY: surface real bugs.\n\nScan the codebase for bugs.",
}


class TestRenderSkillMarkdown:
    def test_frontmatter_name_is_hk_prefixed(self):
        frontmatter, _ = parse_skill_md_text(render_skill_markdown(PRESET))
        assert frontmatter["name"] == "hk-bug-report"

    def test_generated_marker_present(self):
        frontmatter, _ = parse_skill_md_text(render_skill_markdown(PRESET))
        assert frontmatter["generated"] is True

    def test_generated_from_points_at_source_preset(self):
        frontmatter, _ = parse_skill_md_text(render_skill_markdown(PRESET))
        assert frontmatter["generated_from"] == "data/task_presets.json#bug-report"

    def test_body_contains_full_preset_description(self):
        _, body = parse_skill_md_text(render_skill_markdown(PRESET))
        assert "Scan the codebase for bugs." in body

    def test_description_survives_quotes_in_source_text(self):
        preset = {**PRESET, "description": 'Say "hello" to the reviewer.'}
        frontmatter, _ = parse_skill_md_text(render_skill_markdown(preset))
        assert "hello" in frontmatter["description"]

    def test_description_survives_newlines_and_dashes_in_source_text(self):
        preset = {
            **PRESET,
            "description": "Line one.\n\n---\n\nLine two — with an em dash.",
        }
        frontmatter, body = parse_skill_md_text(render_skill_markdown(preset))
        assert frontmatter["name"] == "hk-bug-report"
        assert "Line two" in body

    def test_rendering_is_deterministic(self):
        assert render_skill_markdown(PRESET) == render_skill_markdown(PRESET)


class TestParseSkillMdText:
    def test_round_trips_a_rendered_skill(self):
        frontmatter, body = parse_skill_md_text(render_skill_markdown(PRESET))
        assert frontmatter["name"] == "hk-bug-report"
        assert "Bug Report" in body

    def test_missing_frontmatter_raises(self):
        with pytest.raises(ExportError):
            parse_skill_md_text("# no frontmatter here\n")

    def test_non_mapping_frontmatter_raises(self):
        with pytest.raises(ExportError):
            parse_skill_md_text("---\n- just\n- a\n- list\n---\nbody\n")
