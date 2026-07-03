"""Documentation filtering for solver-visible cleanroom packages."""

from __future__ import annotations

import shutil
from pathlib import Path


EXCLUDED_DOC_PATTERNS = (
    ".git",
    ".git*",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "tests",
    "test",
    "generated_tests",
    "hidden_tests",
    "artifacts",
    "*.pyc",
    "*.pyo",
    "*.egg-info",
)
SOURCE_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go", ".java"}


def copy_public_docs(repo_path: Path, docs_paths: list[str], output_dir: Path) -> list[Path]:
    """Copy docs while excluding obvious source/test directories."""

    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for rel in docs_paths:
        source = repo_path / rel
        if source.is_file():
            if _excluded_public_doc(source):
                continue
            destination = output_dir / source.name
            shutil.copy2(source, destination)
            copied.append(destination)
        elif source.is_dir():
            destination = output_dir / source.name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns(*EXCLUDED_DOC_PATTERNS),
            )
            _remove_source_like_files(destination)
            if any(destination.rglob("*")):
                copied.append(destination)
    return copied


def _excluded_public_doc(path: Path) -> bool:
    lowered = path.name.lower()
    return (
        path.suffix.lower() in SOURCE_SUFFIXES
        or lowered.startswith(".git")
        or lowered == ".ds_store"
        or lowered.startswith("test")
        or "hidden" in lowered
        or "generated" in lowered
    )


def _remove_source_like_files(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and _excluded_public_doc(path):
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
