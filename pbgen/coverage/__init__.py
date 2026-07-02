"""Coverage interfaces, adapters, and gap analysis."""

from pbgen.coverage.adapters import (
    CFamilyCoverageAdapter,
    CoverageAdapter,
    CoverageWrapper,
    PlaceholderCoverageAdapter,
    PythonCoverageAdapter,
    coverage_unavailable_report,
    coverage_report_from_json,
    create_coverage_wrapper,
    run_python_coverage,
)
from pbgen.coverage.coverage_runner import empty_mvp_coverage_report, run_c_family_coverage

__all__ = [
    "CFamilyCoverageAdapter",
    "CoverageAdapter",
    "CoverageWrapper",
    "PlaceholderCoverageAdapter",
    "PythonCoverageAdapter",
    "coverage_unavailable_report",
    "coverage_report_from_json",
    "create_coverage_wrapper",
    "empty_mvp_coverage_report",
    "run_c_family_coverage",
    "run_python_coverage",
]
