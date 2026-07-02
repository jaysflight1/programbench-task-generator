"""Coverage gap ranking helpers."""

from __future__ import annotations

from pbgen.schemas import CoverageGap, CoverageReport


def prioritize_gaps(report: CoverageReport) -> list[CoverageGap]:
    """Return gaps sorted by descending priority."""

    return sorted(report.gaps, key=lambda gap: gap.priority, reverse=True)
