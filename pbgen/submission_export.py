"""Deterministic source archive export for work-trial submissions."""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from pathlib import Path


_ALLOWED_TOP_LEVEL_FILES = ("README.md", "pyproject.toml")
_ALLOWED_DIRECTORIES = ("pbgen", "tests", "prompts", "examples")
_TOP_LEVEL_INSTRUCTION_MARKDOWN = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "DEVELOPMENT.md",
    "INSTRUCTIONS.md",
)

_CLUTTER_DIR_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "htmlcov",
    "logs",
    "node_modules",
    "tasks",
    "venv",
}
_CLUTTER_FILE_NAMES = {
    ".DS_Store",
    ".coverage",
    "coverage.xml",
}
_CLUTTER_FILE_SUFFIXES = (
    ".log",
    ".pyc",
    ".pyo",
    ".jsonl",
)
_GENERATED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tgz",
    ".gz",
)
_DETERMINISTIC_ZIP_DATE = (2020, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class SubmissionExportResult:
    """Summary returned after writing a clean submission archive."""

    archive_path: Path
    included_count: int
    excluded_known_clutter: tuple[str, ...]

    @property
    def excluded_known_clutter_count(self) -> int:
        """Number of known local-development clutter paths skipped or detected."""

        return len(self.excluded_known_clutter)


def create_submission_archive(
    project_root: Path,
    archive_path: Path | None = None,
) -> SubmissionExportResult:
    """Create a deterministic zip archive from an explicit project manifest.

    The exporter intentionally includes only known review-relevant paths instead
    of copying the whole working tree. Symlinks are excluded to avoid leaking
    files outside the project root.
    """

    root = project_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {project_root}")

    destination = (
        archive_path.expanduser().resolve()
        if archive_path is not None
        else root / "programbench_generator_submission.zip"
    )
    if destination.exists() and destination.is_dir():
        raise ValueError(f"Archive path is a directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    included_files, excluded_clutter = _collect_submission_manifest(root, destination)
    with zipfile.ZipFile(destination, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in included_files:
            source_path = root / relative_path
            zip_info = zipfile.ZipInfo(relative_path.as_posix(), _DETERMINISTIC_ZIP_DATE)
            zip_info.compress_type = zipfile.ZIP_DEFLATED
            zip_info.external_attr = 0o644 << 16
            archive.writestr(zip_info, source_path.read_bytes())

    return SubmissionExportResult(
        archive_path=destination,
        included_count=len(included_files),
        excluded_known_clutter=tuple(sorted(excluded_clutter)),
    )


def _collect_submission_manifest(root: Path, destination: Path) -> tuple[list[Path], set[str]]:
    included: set[Path] = set()
    excluded_clutter = _detect_top_level_clutter(root, destination)

    for file_name in _ALLOWED_TOP_LEVEL_FILES:
        _add_manifest_file(root, root / file_name, destination, included, excluded_clutter)

    for file_name in _TOP_LEVEL_INSTRUCTION_MARKDOWN:
        _add_manifest_file(root, root / file_name, destination, included, excluded_clutter)

    for directory_name in _ALLOWED_DIRECTORIES:
        directory = root / directory_name
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            excluded_clutter.add(directory.relative_to(root).as_posix())
            continue
        _walk_allowed_directory(root, directory, destination, included, excluded_clutter)

    return sorted(included, key=Path.as_posix), excluded_clutter


def _add_manifest_file(
    root: Path,
    candidate: Path,
    destination: Path,
    included: set[Path],
    excluded_clutter: set[str],
) -> None:
    if not candidate.exists():
        return
    relative_path = candidate.relative_to(root)
    if _should_exclude(candidate, relative_path, destination, is_dir=False):
        excluded_clutter.add(relative_path.as_posix())
        return
    if candidate.is_file():
        included.add(relative_path)


def _walk_allowed_directory(
    root: Path,
    directory: Path,
    destination: Path,
    included: set[Path],
    excluded_clutter: set[str],
) -> None:
    for current_dir_name, dir_names, file_names in os.walk(directory, topdown=True, followlinks=False):
        current_dir = Path(current_dir_name)
        kept_dirs = []
        for dir_name in sorted(dir_names):
            child_dir = current_dir / dir_name
            relative_path = child_dir.relative_to(root)
            if _should_exclude(child_dir, relative_path, destination, is_dir=True):
                excluded_clutter.add(relative_path.as_posix())
            else:
                kept_dirs.append(dir_name)
        dir_names[:] = kept_dirs

        for file_name in sorted(file_names):
            child_file = current_dir / file_name
            relative_path = child_file.relative_to(root)
            if _should_exclude(child_file, relative_path, destination, is_dir=False):
                excluded_clutter.add(relative_path.as_posix())
                continue
            if child_file.is_file():
                included.add(relative_path)


def _detect_top_level_clutter(root: Path, destination: Path) -> set[str]:
    excluded_clutter: set[str] = set()
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        relative_path = child.relative_to(root)
        is_dir = child.is_dir() and not child.is_symlink()
        if _should_exclude(child, relative_path, destination, is_dir=is_dir):
            excluded_clutter.add(relative_path.as_posix())
    return excluded_clutter


def _should_exclude(path: Path, relative_path: Path, destination: Path, *, is_dir: bool) -> bool:
    name = relative_path.name
    if path.is_symlink():
        return True
    if path.resolve() == destination:
        return True
    if is_dir:
        return _is_clutter_dir(name)
    return _is_clutter_file(name)


def _is_clutter_dir(name: str) -> bool:
    return (
        name in _CLUTTER_DIR_NAMES
        or name.endswith(".egg-info")
        or (name.startswith(".") and "cache" in name)
    )


def _is_clutter_file(name: str) -> bool:
    return (
        name in _CLUTTER_FILE_NAMES
        or name.endswith(_CLUTTER_FILE_SUFFIXES)
        or name.endswith(_GENERATED_ARCHIVE_SUFFIXES)
    )
