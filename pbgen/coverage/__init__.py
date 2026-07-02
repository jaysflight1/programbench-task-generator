"""Coverage interfaces, adapters, and gap analysis."""

from pbgen.coverage.adapters import (
    CoverageAdapter,
    CoverageWrapper,
    PlaceholderCoverageAdapter,
    PythonCoverageAdapter,
    coverage_report_from_json,
    create_coverage_wrapper,
    run_python_coverage,
)
from pbgen.coverage.coverage_runner import empty_mvp_coverage_report

__all__ = [
    "CoverageAdapter",
    "CoverageWrapper",
    "PlaceholderCoverageAdapter",
    "PythonCoverageAdapter",
    "coverage_report_from_json",
    "create_coverage_wrapper",
    "empty_mvp_coverage_report",
    "run_python_coverage",
]
