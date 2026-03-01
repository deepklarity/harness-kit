"""Board registry — maps board IDs to project paths.

Stores a thin JSON index at ~/.odin/boards.json so commands like
``odin logs -b <board_id>`` can resolve a project's log directory
without requiring the user to cd into the project first.

The registry is updated automatically by ``odin init --board-id``.
"""

import json
from pathlib import Path
from typing import Optional

REGISTRY_PATH = Path.home() / ".odin" / "boards.json"


def load_registry() -> dict:
    """Load the board registry, returning {} if it doesn't exist."""
    if not REGISTRY_PATH.exists():
        return {}
    return json.loads(REGISTRY_PATH.read_text())


def save_registry(data: dict) -> None:
    """Write the board registry to disk."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, indent=2) + "\n")


def register_board(board_id: int, path: str, name: str = "") -> None:
    """Register (or update) a board ID → project path mapping."""
    registry = load_registry()
    registry[str(board_id)] = {"path": path, "name": name}
    save_registry(registry)


def resolve_board_path(board_id: int) -> Optional[Path]:
    """Look up the project path for a board ID, or None if unknown."""
    entry = load_registry().get(str(board_id))
    if entry is None:
        return None
    p = Path(entry["path"])
    return p if p.exists() else None
