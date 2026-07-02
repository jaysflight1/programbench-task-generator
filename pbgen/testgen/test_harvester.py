"""Native test harvesting placeholder."""

from __future__ import annotations

from pathlib import Path


def harvest_existing_tests(repo_path: Path) -> list[Path]:
    """Return existing tests that could inform generation without packaging them."""

    candidates: list[Path] = []
    for dirname in ("test", "tests"):
        path = repo_path / dirname
        if path.exists():
            candidates.extend(sorted(child for child in path.rglob("*") if child.is_file()))
    return candidates
