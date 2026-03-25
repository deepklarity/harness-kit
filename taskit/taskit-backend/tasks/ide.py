"""IDE detection and launch helpers for supported local editors."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DetectedIde:
    id: str
    label: str
    icon_key: str


@dataclass(frozen=True)
class SupportedIde:
    id: str
    label: str
    icon_key: str
    cli_candidates: tuple[str, ...]
    macos_app_name: str | None = None

    def detect_launcher(self) -> tuple[str, str] | None:
        for candidate in self.cli_candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return ("cli", resolved)
        if sys.platform == "darwin" and self.macos_app_name:
            app_path = Path("/Applications") / f"{self.macos_app_name}.app"
            if app_path.exists():
                return ("macos_app", self.macos_app_name)
        return None

    def launch(self, project_root: str) -> None:
        launch = self.detect_launcher()
        if launch is None:
            raise FileNotFoundError(f"{self.label} is not detected on this machine.")
        launch_type, target = launch
        if launch_type == "cli":
            subprocess.Popen(
                [target, project_root],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        if launch_type == "macos_app":
            subprocess.Popen(
                ["open", "-a", target, project_root],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        raise RuntimeError(f"Unsupported launch type: {launch_type}")


SUPPORTED_IDES: tuple[SupportedIde, ...] = (
    SupportedIde(
        id="cursor",
        label="Cursor",
        icon_key="cursor",
        cli_candidates=("cursor", "cursor.cmd", "cursor.exe"),
        macos_app_name="Cursor",
    ),
    SupportedIde(
        id="vscode",
        label="VS Code",
        icon_key="vscode",
        cli_candidates=("code", "code.cmd", "code.exe"),
        macos_app_name="Visual Studio Code",
    ),
    SupportedIde(
        id="zed",
        label="Zed",
        icon_key="zed",
        cli_candidates=("zed", "zed.exe"),
        macos_app_name="Zed",
    ),
)


def detect_supported_ides() -> list[DetectedIde]:
    detected = []
    for ide in SUPPORTED_IDES:
        if ide.detect_launcher() is None:
            continue
        detected.append(DetectedIde(id=ide.id, label=ide.label, icon_key=ide.icon_key))
    return detected


def get_supported_ide(ide_id: str) -> SupportedIde | None:
    needle = (ide_id or "").strip().lower()
    for ide in SUPPORTED_IDES:
        if ide.id == needle:
            return ide
    return None

