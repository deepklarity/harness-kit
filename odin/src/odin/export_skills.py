"""Preset -> hk-skill exporter (task 260).

ONE canonical source: ``data/task_presets.json`` in the taskit-backend
tree. Every generated ``SKILL.md`` carries a ``generated: true`` marker in
its frontmatter — never hand-edit a generated skill; edit the preset in
``task_presets.json`` and re-run the exporter. This is how a project using
the kit's board presets can also get them as ``/hk-<id>`` slash commands in
a live Claude Code session, and how ``odin new-project`` (or a manual run
of this exporter) installs the kit's practice into any target repo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

GENERATED_MARKER = "generated: true"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


class ExportError(ValueError):
    """Raised when a preset can't be exported or a generated file can't be parsed."""


def _skill_name(preset_id: str) -> str:
    return f"hk-{preset_id}"


def _first_sentence(text: str, limit: int = 160) -> str:
    """First line of a preset description, trimmed to a frontmatter-sized blurb."""
    line = text.strip().split("\n", 1)[0].strip()
    line = line.replace('"', "'")
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def render_skill_markdown(preset: dict[str, Any]) -> str:
    """Render one preset as SKILL.md text. Pure function: same preset in, same bytes out."""
    pid = preset["id"]
    title = preset.get("title", pid)
    blurb = _first_sentence(preset.get("description", ""))
    description = (
        f"{title}. {blurb} Generated from task preset '{pid}' "
        "— edit data/task_presets.json, not this file."
    )

    frontmatter = {
        "name": _skill_name(pid),
        "description": description,
        "generated": True,
        "generated_from": f"data/task_presets.json#{pid}",
    }
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
    body = f"# {title}\n\n{preset['description']}\n"
    return f"---\n{fm_yaml}---\n\n{body}"


def load_presets(presets_path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(presets_path).read_text())
    presets = data.get("presets", [])
    if not isinstance(presets, list):
        raise ExportError(f"{presets_path}: 'presets' must be a list")
    return presets


def export_presets_to_skills(
    presets_path: str | Path, output_dir: str | Path
) -> list[Path]:
    """Render every preset to ``<output_dir>/hk-<id>/SKILL.md``.

    Refuses to overwrite a file that exists but doesn't carry the
    generated marker — that means it's hand-authored and a naming
    collision, not a stale generated file. Returns the list of written
    paths. Idempotent: re-running with the same presets produces
    byte-identical files.
    """
    presets = load_presets(presets_path)
    output_dir = Path(output_dir)
    written: list[Path] = []
    for preset in presets:
        pid = preset["id"]
        skill_dir = output_dir / _skill_name(pid)
        skill_path = skill_dir / "SKILL.md"
        if skill_path.exists() and GENERATED_MARKER not in skill_path.read_text():
            raise ExportError(
                f"refusing to overwrite {skill_path}: it exists and isn't "
                "marked 'generated: true' (looks hand-authored)"
            )
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(render_skill_markdown(preset))
        written.append(skill_path)
    return written


def parse_skill_md_text(text: str, *, label: str = "<text>") -> tuple[dict[str, Any], str]:
    """Parse SKILL.md text into ``(frontmatter, body)``. Pure — no disk I/O.

    Raises :class:`ExportError` if the frontmatter block is missing or
    doesn't parse to a YAML mapping.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ExportError(f"{label}: no frontmatter block found")
    fm_text, body = m.groups()
    frontmatter = yaml.safe_load(fm_text)
    if not isinstance(frontmatter, dict):
        raise ExportError(f"{label}: frontmatter did not parse to a mapping")
    return frontmatter, body


def parse_skill_md(path: str | Path) -> tuple[dict[str, Any], str]:
    """Parse a generated SKILL.md file into ``(frontmatter, body)``."""
    path = Path(path)
    return parse_skill_md_text(path.read_text(), label=str(path))
