"""Repository candidate checks used before task initialization."""

from __future__ import annotations

from pathlib import Path


def is_supported_local_candidate(path: Path) -> bool:
    """Return whether a local path has enough content to initialize a task."""

    return path.exists() and path.is_dir() and any(child.is_file() for child in path.iterdir())
