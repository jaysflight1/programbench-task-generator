from __future__ import annotations

from pbgen.config import PBGenConfig
from pbgen.coverage.registry import (
    CFamilyGcovCoverageBackend,
    CoverageBackendRegistry,
    PythonCoverageBackend,
    UnavailableCoverageBackend,
    write_coverage_artifacts,
)
from pbgen.schemas import CoverageReport, TaskSpec
from pbgen.serialization import read_data


def test_registry_selects_python_backend(tmp_path) -> None:
    spec = TaskSpec(task_id="py", repo_url="local", commit_sha="local", language="python")

    backend = CoverageBackendRegistry().select(spec, PBGenConfig(workspace_root=tmp_path))

    assert isinstance(backend, PythonCoverageBackend)
    assert backend.name == "python-coverage.py"


def test_registry_selects_c_family_backend_for_make(tmp_path) -> None:
    spec = TaskSpec(task_id="c", repo_url="local", commit_sha="local", build_system="make")

    backend = CoverageBackendRegistry().select(spec, PBGenConfig(workspace_root=tmp_path))

    assert isinstance(backend, CFamilyGcovCoverageBackend)
    assert backend.name == "c-family-gcov"


def test_registry_returns_placeholder_for_future_languages(tmp_path) -> None:
    spec = TaskSpec(task_id="go", repo_url="local", commit_sha="local", language="go")
    config = PBGenConfig(workspace_root=tmp_path)
    backend = CoverageBackendRegistry().select(spec, config)

    assert isinstance(backend, UnavailableCoverageBackend)
    report = backend.run(
        type(
            "Context",
            (),
            {"spec": spec, "iteration": 2},
        )()
    )
    assert report.coverage_available is False
    assert report.coverage_backend == "go-coverage-placeholder"
    assert report.coverage_unavailable_reason == "go coverage is not implemented yet"


def test_unavailable_coverage_artifacts_are_written(tmp_path) -> None:
    report = CoverageReport(
        task_id="demo",
        iteration=3,
        coverage_backend="rust-coverage-placeholder",
        coverage_available=False,
        coverage_unavailable_reason="rust coverage is not implemented yet",
    )

    write_coverage_artifacts(report, tmp_path)

    assert (tmp_path / "coverage_report_iteration_3.json").exists()
    assert (tmp_path / "coverage_unavailable_report_iteration_3.json").exists()
    latest = read_data(tmp_path / "coverage_unavailable_report.json")
    assert latest["coverage_backend"] == "rust-coverage-placeholder"
