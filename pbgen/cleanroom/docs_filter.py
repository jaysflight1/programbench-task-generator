"""Documentation filtering for solver-visible cleanroom packages."""

from __future__ import annotations

import shutil
from pathlib import Path


def copy_public_docs(repo_path: Path, docs_paths: list[str], output_dir: Path) -> list[Path]:
    """Copy docs while excluding obvious source/test directories."""

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for rel in docs_paths:
        source = repo_path / rel
        if source.is_file():
            destination = output_dir / source.name
            shutil.copy2(source, destination)
            copied.append(destination)
        elif source.is_dir():
            destination = output_dir / source.name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns("tests", "test", "__pycache__"))
            copied.append(destination)
    return copied
