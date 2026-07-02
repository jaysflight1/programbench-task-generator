"""Asset selection for cleanroom packages."""

from __future__ import annotations

import shutil
from pathlib import Path


FORBIDDEN_ASSET_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "tests",
    "test",
    "generated_tests",
    "hidden_tests",
    "artifacts",
}
SOURCE_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go", ".java"}


def copy_assets(repo_path: Path, asset_paths: list[str], output_dir: Path) -> list[Path]:
    """Copy declared non-source assets into the solver package."""

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for rel in asset_paths:
        source = repo_path / rel
        if source.is_file() and _asset_allowed(rel, source):
            destination = output_dir / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(destination)
    return copied


def _asset_allowed(rel: str, source: Path) -> bool:
    if source.is_symlink():
        return False
    parts = {part.lower() for part in Path(rel).parts}
    return source.suffix.lower() not in SOURCE_SUFFIXES and not parts & FORBIDDEN_ASSET_PARTS
