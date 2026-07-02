"""Mutation-style rejection placeholder."""

from __future__ import annotations


def mutation_rejection_score_available() -> bool:
    """Return false for the MVP; mutation testing is a future extension."""

    return False
