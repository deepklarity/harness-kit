"""Durable per-project notes (PROJECT_NOTES.md) injected into every task's
effective input.

One notes file per project so every agent starts from the same hard-won
context — a feed quirk, a command, a gotcha — instead of re-deriving it on
every run. The path is configurable per board (``OdinConfig.project_notes_path``);
the default is ``PROJECT_NOTES.md`` at the project root. Content is capped so
it never crowds the task brief.

The cap keeps the newest entries (bottom of the file) and drops the oldest
(top) with a "consolidate me" note, matching the append convention where
newer facts land at the end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

#: Hard cap on injected content (chars). Older entries fall off first.
PROJECT_NOTES_MAX_CHARS = 4000

#: Default file name relative to the project working directory.
_DEFAULT_REL = "PROJECT_NOTES.md"


def read_project_notes(
    working_dir: Optional[str],
    notes_rel_path: Optional[str] = None,
) -> str:
    """Return the capped, trimmed contents of the project notes file.

    Resolves *notes_rel_path* (or the default ``PROJECT_NOTES.md``) under
    *working_dir*. Returns ``""`` when the directory or file is absent,
    empty, or unreadable — injection is best-effort and never fatal.

    When content exceeds :data:`PROJECT_NOTES_MAX_CHARS` the oldest entries
    (top of file) are dropped and a consolidation note is prepended so a
    future reviewer knows the file needs pruning.
    """
    if not working_dir:
        return ""
    rel = notes_rel_path or _DEFAULT_REL
    path = Path(working_dir) / rel
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    content = content.strip()
    if not content:
        return ""
    if len(content) > PROJECT_NOTES_MAX_CHARS:
        note = "[… older entries truncated — consolidate PROJECT_NOTES.md …]\n\n"
        keep = PROJECT_NOTES_MAX_CHARS - len(note)
        content = note + content[-keep:]
    return content
