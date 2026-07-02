"""Asset selection for cleanroom packages."""

from __future__ import annotations

import shutil
from pathlib import Path


def copy_assets(repo_path: Path, asset_paths: list[str], output_dir: Path) -> list[Path]:
    """Copy declared non-source assets into the solver package."""

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for rel in asset_paths:
        source = repo_path / rel
        if source.is_file() and source.suffix.lower() not in {".py", ".c", ".cc", ".cpp", ".h", ".rs", ".go", ".java"}:
            destination = output_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(destination)
    return copied
