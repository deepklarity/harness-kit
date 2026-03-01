"""Spec archive storage and derived status."""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from odin.taskit.models import Task, TaskStatus


class SpecArchive(BaseModel):
    """Content archive for a planned spec."""

    id: str
    title: str
    source: str  # file path or "inline"
    content: str  # full spec text, frozen at plan time
    created_at: datetime = Field(default_factory=datetime.now)
    abandoned: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SpecStore:
    """Read/write spec archives as JSON files in .odin/specs/."""

    def __init__(self, storage_dir: str):
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, spec: SpecArchive) -> None:
        path = self._dir / f"{spec.id}.json"
        path.write_text(spec.model_dump_json(indent=2))

    def load(self, spec_id: str) -> Optional[SpecArchive]:
        path = self._dir / f"{spec_id}.json"
        if not path.exists():
            return None
        return SpecArchive.model_validate_json(path.read_text())

    def load_all(self) -> List[SpecArchive]:
        specs = []
        for path in sorted(self._dir.glob("sp_*.json")):
            try:
                specs.append(SpecArchive.model_validate_json(path.read_text()))
            except Exception:
                continue
        return specs

    def set_abandoned(self, spec_id: str) -> Optional[SpecArchive]:
        spec = self.load(spec_id)
        if not spec:
            return None
        spec.abandoned = True
        self.save(spec)
        return spec

    def delete(self, spec_id: str) -> bool:
        """Delete a spec file by ID. Returns True if deleted, False if not found."""
        path = self._dir / f"{spec_id}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def resolve_spec_id(self, prefix: str) -> Optional[str]:
        """Resolve a spec ID prefix to a full ID."""
        specs = self.load_all()
        matches = [s.id for s in specs if s.id.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]
        return None


def generate_spec_id(title: Optional[str] = None) -> str:
    """Generate a spec ID with timestamp + optional slug.
    
    Format: sp_YYYYMMDD_HHMMSS[_slug]
    Example: sp_20241029_143000_user-profile
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if title:
        slug = spec_short_tag(title).replace("-", "_")
        return f"sp_{ts}_{slug}"
    return f"sp_{ts}"


def derive_spec_status(tasks: List[Task], abandoned: bool) -> str:
    """Derive spec status from its tasks. Pure function, no stored state.

    Priority order (first match wins):
    1. abandoned (flag)
    2. active (any in_progress)
    3. blocked (any failed, none in_progress)
    4. done (all completed)
    5. partial (some completed, some assigned)
    6. planned (all assigned)
    7. draft (all pending)
    """
    if abandoned:
        return "abandoned"
    if not tasks:
        return "empty"

    statuses = [t.status for t in tasks]

    if any(s == TaskStatus.IN_PROGRESS for s in statuses):
        return "active"
    if any(s == TaskStatus.FAILED for s in statuses):
        return "blocked"
    if all(s == TaskStatus.DONE for s in statuses):
        return "done"
    if any(s == TaskStatus.DONE for s in statuses):
        return "partial"
    if all(s == TaskStatus.TODO for s in statuses):
        return "planned"
    return "draft"


def spec_short_tag(title: str) -> str:
    """Derive a short display tag from a spec title or filename.

    Examples:
        "specs/user_profile_api.md" -> "profile-api"
        "Fix Auth Token Refresh" -> "auth-fix"
        "Write a haiku about technology" -> "haiku-about"
    """
    # If it looks like a file path, use the filename
    if "/" in title or title.endswith(".md"):
        name = Path(title).stem
        # Remove common prefixes like "spec_", "specs_"
        name = re.sub(r"^specs?_", "", name)
        # Convert underscores to hyphens, truncate
        tag = name.replace("_", "-")
        parts = tag.split("-")
        # Take last 2-3 meaningful parts
        if len(parts) > 3:
            parts = parts[-3:]
        return "-".join(parts)[:20]

    # For inline text, extract key words
    words = re.sub(r"[^a-zA-Z0-9\s]", "", title).lower().split()
    # Skip common filler words
    skip = {"a", "an", "the", "is", "are", "was", "were", "to", "for", "of", "in", "on", "at", "with", "and", "or", "write", "create", "build", "make", "add", "fix", "update"}
    meaningful = [w for w in words if w not in skip]
    if not meaningful:
        meaningful = words[:2]
    return "-".join(meaningful[:2])[:20]
