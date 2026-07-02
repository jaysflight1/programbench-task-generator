"""Executable hashing helpers."""

from __future__ import annotations

from pathlib import Path

from pbgen.logging.provenance import sha256_file


def hash_executable(path: Path) -> str:
    """Compute a stable SHA256 hash for an executable."""

    return sha256_file(path)
