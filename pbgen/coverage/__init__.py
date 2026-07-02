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
from pbgen.coverage.coverage_runner import (
    empty_coverage_report,
    empty_mvp_coverage_report,
    run_c_family_coverage,
)
from pbgen.coverage.registry import (
    CoverageBackendRegistry,
    CoverageRunContext,
    run_registered_coverage,
    write_coverage_artifacts,
)

__all__ = [
    "CFamilyCoverageAdapter",
    "CoverageAdapter",
    "CoverageBackendRegistry",
    "CoverageRunContext",
    "CoverageWrapper",
    "PlaceholderCoverageAdapter",
    "PythonCoverageAdapter",
    "coverage_unavailable_report",
    "coverage_report_from_json",
    "create_coverage_wrapper",
    "empty_coverage_report",
    "empty_mvp_coverage_report",
    "run_c_family_coverage",
    "run_python_coverage",
    "run_registered_coverage",
    "write_coverage_artifacts",
]
