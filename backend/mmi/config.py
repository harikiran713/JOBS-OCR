"""Application paths and process-level configuration.

All filesystem locations derive from a single data directory so nothing is
hard-coded. The Electron shell passes ``MMI_DATA_DIR`` (inside the user's
profile); developers can override it to keep test data isolated.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "MarineMaintenanceIntelligence"


def default_data_dir() -> Path:
    env = os.environ.get("MMI_DATA_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME / "data"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / APP_NAME / "data"


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "mmi.sqlite3"

    @property
    def documents(self) -> Path:
        """Managed copies of imported PDFs (originals are never modified)."""
        return self.root / "documents"

    @property
    def renders(self) -> Path:
        """Disk cache of rendered page images for the viewer."""
        return self.root / "cache" / "renders"

    @property
    def evidence(self) -> Path:
        """Image crops preserved as extraction evidence."""
        return self.root / "evidence"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def models(self) -> Path:
        return self.root / "models"

    def ensure(self) -> None:
        for p in (self.root, self.documents, self.renders, self.evidence, self.logs, self.backups, self.models):
            p.mkdir(parents=True, exist_ok=True)


_paths: AppPaths | None = None


def get_paths() -> AppPaths:
    global _paths
    if _paths is None:
        _paths = AppPaths(default_data_dir())
        _paths.ensure()
    return _paths


def set_paths(root: Path) -> AppPaths:
    """Used by tests and the CLI to point the backend at a specific data dir."""
    global _paths
    _paths = AppPaths(root)
    _paths.ensure()
    return _paths
