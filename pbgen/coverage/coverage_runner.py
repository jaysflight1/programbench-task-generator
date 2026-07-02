"""Coverage report helpers and runnable Python coverage hook."""

from __future__ import annotations

from pathlib import Path

from pbgen.coverage.adapters import PythonCoverageAdapter
from pbgen.schemas import CoverageReport


def empty_mvp_coverage_report(task_id: str, iteration: int = 0) -> CoverageReport:
    """Return a transparent placeholder coverage report for the MVP path."""

    return CoverageReport(task_id=task_id, iteration=iteration, line_coverage=None)


def run_python_coverage(
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    *,
    iteration: int = 0,
    source_roots: list[Path] | None = None,
    work_dir: Path | None = None,
    timeout_seconds: int = 120,
) -> CoverageReport:
    """Run pytest against a Python executable and return a structured report."""

    adapter = PythonCoverageAdapter(
        task_id=task_id,
        iteration=iteration,
        source_roots=source_roots,
        work_dir=work_dir,
        timeout_seconds=timeout_seconds,
    )
    return adapter.run_tests_with_coverage(tests_path, executable_path)
