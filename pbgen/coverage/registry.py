"""Language-aware coverage backend registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.coverage.adapters import coverage_unavailable_report
from pbgen.coverage.coverage_runner import run_c_family_coverage, run_python_coverage
from pbgen.schemas import CoverageReport, TaskSpec


@dataclass(frozen=True)
class CoverageRunContext:
    """Inputs needed to run one coverage backend for one generation iteration."""

    spec: TaskSpec
    paths: ArtifactPaths
    config: PBGenConfig
    iteration: int


class CoverageBackend(ABC):
    """Executable coverage backend selected by language/build metadata."""

    name: str

    @abstractmethod
    def run(self, context: CoverageRunContext) -> CoverageReport:
        """Run coverage or return an explicit unavailable report."""


class PythonCoverageBackend(CoverageBackend):
    name = "python-coverage.py"

    def run(self, context: CoverageRunContext) -> CoverageReport:
        return run_python_coverage(
            context.spec.task_id,
            context.paths.generated_tests,
            context.paths.executable,
            iteration=context.iteration,
            source_roots=[context.paths.repo, context.paths.gold],
            work_dir=context.paths.reports / f"coverage_iteration_{context.iteration}",
            timeout_seconds=120,
        )


class CFamilyGcovCoverageBackend(CoverageBackend):
    name = "c-family-gcov"

    def run(self, context: CoverageRunContext) -> CoverageReport:
        return run_c_family_coverage(
            context.spec,
            context.paths.repo,
            context.paths.generated_tests,
            iteration=context.iteration,
            work_dir=context.paths.reports / f"coverage_iteration_{context.iteration}",
            config=context.config,
        )


class UnavailableCoverageBackend(CoverageBackend):
    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def run(self, context: CoverageRunContext) -> CoverageReport:
        return coverage_unavailable_report(
            context.spec.task_id,
            context.iteration,
            self.name,
            self.reason,
        )


class CoverageBackendRegistry:
    """Deterministic coverage backend selection."""

    def select(self, spec: TaskSpec, config: PBGenConfig) -> CoverageBackend:
        del config
        language = (spec.language or "").lower()
        build_system = (spec.build_system or "").lower()
        if language == "python":
            return PythonCoverageBackend()
        if language in {"c", "c++", "cpp", "c/c++"} or build_system in {
            "make",
            "cmake",
            "c-single",
        }:
            return CFamilyGcovCoverageBackend()
        if language in {"go", "rust", "java"}:
            return UnavailableCoverageBackend(
                f"{language}-coverage-placeholder",
                f"{language} coverage is not implemented yet",
            )
        return UnavailableCoverageBackend(
            "coverage-unavailable",
            _unsupported_reason(language, build_system),
        )


def run_registered_coverage(
    spec: TaskSpec,
    paths: ArtifactPaths,
    config: PBGenConfig,
    *,
    iteration: int,
) -> CoverageReport:
    """Run the selected language-aware coverage backend."""

    context = CoverageRunContext(spec=spec, paths=paths, config=config, iteration=iteration)
    return CoverageBackendRegistry().select(spec, config).run(context)


def write_coverage_artifacts(report: CoverageReport, reports_dir: Path) -> None:
    """Persist coverage report and explicit unavailable marker when appropriate."""

    from pbgen.serialization import write_data

    write_data(
        reports_dir / f"coverage_report_iteration_{report.iteration}.json",
        report.model_dump(mode="json"),
    )
    if not report.coverage_available:
        payload = report.model_dump(mode="json")
        write_data(
            reports_dir / f"coverage_unavailable_report_iteration_{report.iteration}.json",
            payload,
        )
        write_data(reports_dir / "coverage_unavailable_report.json", payload)


def _unsupported_reason(language: str, build_system: str) -> str:
    if language or build_system:
        return (
            "coverage backend is unavailable for "
            f"language={language or 'unknown'} build_system={build_system or 'unknown'}"
        )
    return "coverage backend is unavailable because language/build system is unknown"
