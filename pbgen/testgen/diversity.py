"""Behavioral diversity metadata for generated test suites."""

from __future__ import annotations

from collections import Counter
from typing import Any


REQUIRED_BEHAVIOR_CATEGORIES = [
    "normal-path",
    "malformed-input",
    "flag",
    "config-file",
    "stdin",
    "file-input",
    "filesystem-output",
    "environment-variable",
    "boundary-case",
    "error-formatting",
]


def behavior_category_counts(diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    """Count accepted diagnostics by behavior category."""

    counts: Counter[str] = Counter()
    for item in diagnostics:
        if item.get("accepted") is not True:
            continue
        category = item.get("behavior_category")
        if not isinstance(category, str) or not category:
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                category = metadata.get("behavior_category")
        if isinstance(category, str) and category:
            counts[category] += 1
    return dict(sorted(counts.items()))
