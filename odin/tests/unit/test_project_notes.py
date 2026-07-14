"""Unit tests for odin.project_notes — durable per-project notes reader.

Pure logic + disk I/O (tmp_path), no network, no subprocesses.
"""

from odin.project_notes import (
    PROJECT_NOTES_MAX_CHARS,
    read_project_notes,
)


class TestReadProjectNotes:
    """read_project_notes() resolves, reads, and caps the project notes file."""

    def test_returns_empty_when_no_working_dir(self):
        assert read_project_notes(None) == ""

    def test_returns_empty_when_file_absent(self, tmp_path):
        assert read_project_notes(str(tmp_path)) == ""

    def test_reads_default_path(self, tmp_path):
        (tmp_path / "PROJECT_NOTES.md").write_text("# Notes\n\n- use foo --bar\n")
        out = read_project_notes(str(tmp_path))
        assert "use foo --bar" in out

    def test_respects_configured_relative_path(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "PROJECT_NOTES.md").write_text("# project notes\n\nfreshness gate\n")
        out = read_project_notes(str(tmp_path), "docs/PROJECT_NOTES.md")
        assert "freshness gate" in out
        assert "project notes" in out

    def test_returns_empty_for_empty_file(self, tmp_path):
        (tmp_path / "PROJECT_NOTES.md").write_text("   \n\n  ")
        assert read_project_notes(str(tmp_path)) == ""

    def test_strips_surrounding_whitespace(self, tmp_path):
        (tmp_path / "PROJECT_NOTES.md").write_text("\n\n# Notes\n\n")
        out = read_project_notes(str(tmp_path))
        assert out == "# Notes"

    def test_caps_at_max_chars_keeping_newest(self, tmp_path):
        """When over the cap, oldest entries (top) fall off; newest survive."""
        # Newest entry at the bottom with a unique marker.
        newest = "LATEST_UNIQUE_MARKER_ENTRY"
        body = "x" * (PROJECT_NOTES_MAX_CHARS + 500) + "\n" + newest
        (tmp_path / "PROJECT_NOTES.md").write_text(body)
        out = read_project_notes(str(tmp_path))
        assert len(out) <= PROJECT_NOTES_MAX_CHARS  # cap includes the note
        assert newest in out

    def test_truncation_adds_consolidate_note(self, tmp_path):
        body = "a" * (PROJECT_NOTES_MAX_CHARS + 100)
        (tmp_path / "PROJECT_NOTES.md").write_text(body)
        out = read_project_notes(str(tmp_path))
        assert "consolidate" in out.lower()

    def test_under_cap_passes_through_unchanged(self, tmp_path):
        content = "# Short notes\n\n- fact one\n- fact two\n"
        (tmp_path / "PROJECT_NOTES.md").write_text(content)
        out = read_project_notes(str(tmp_path))
        assert out == content.strip()
