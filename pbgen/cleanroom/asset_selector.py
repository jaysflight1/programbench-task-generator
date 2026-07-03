"""Asset selection for cleanroom packages."""

from __future__ import annotations

import re
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
    "build",
    "generated_tests",
    "hidden_tests",
    "artifacts",
}
SOURCE_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go", ".java"}
TEXT_SUFFIXES = {
    ".cfg",
    ".cmake",
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_CONTENT_PATTERNS = [
    re.compile(r"test_cases_iteration_\d+", re.IGNORECASE),
    re.compile(r"generated_tests", re.IGNORECASE),
    re.compile(r"hidden_tests", re.IGNORECASE),
    re.compile(r"generation_events", re.IGNORECASE),
    re.compile(r"/(?:private/)?(?:tmp|var/folders)/", re.IGNORECASE),
    re.compile(r"/Users/[^/\s]+/", re.IGNORECASE),
]


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
    return (
        source.suffix.lower() not in SOURCE_SUFFIXES
        and not parts & FORBIDDEN_ASSET_PARTS
        and not _asset_content_leaks(source)
    )


def _asset_content_leaks(source: Path) -> bool:
    data = source.read_bytes()
    if b"\0" in data[:4096]:
        return False
    if source.suffix.lower() not in TEXT_SUFFIXES and len(data) > 200_000:
        return False
    text = data.decode("utf-8", errors="ignore")
    return any(pattern.search(text) for pattern in FORBIDDEN_CONTENT_PATTERNS)
