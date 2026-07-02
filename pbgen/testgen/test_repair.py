"""Placeholder repair interface for weak generated tests."""

from __future__ import annotations

from pathlib import Path


def repair_or_discard_bad_tests(test_paths: list[Path]) -> list[Path]:
    """Return accepted tests; future model-backed repair can hook in here."""

    return test_paths
