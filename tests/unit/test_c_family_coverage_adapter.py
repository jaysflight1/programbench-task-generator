from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from pbgen.config import PBGenConfig
from pbgen.coverage.adapters import CFamilyCoverageAdapter
from pbgen.coverage.coverage_runner import run_c_family_coverage
from pbgen.repo_discovery.checkout import init_task
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput, TaskSpec
from pbgen.serialization import read_data, write_data


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_c_coverage_reports_unavailable_when_gcov_missing(tmp_path: Path) -> None:
    if shutil.which("make") is None or shutil.which("cc") is None:
        pytest.skip("make and cc are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-c", config=config, local_path=FIXTURES / "make_c")
    paths = tmp_path / "artifacts" / "make-c"
    spec = TaskSpec.model_validate(read_data(paths / "task_spec.yaml"))
    tests_path = paths / "generated_tests"
    _write_canonical_cases(tests_path)
    adapter = CFamilyCoverageAdapter(
        spec,
        iteration=0,
        work_dir=paths / "reports" / "coverage_iteration_0",
        gcov_executable="pbgen-missing-gcov",
    )

    executable = adapter.instrument_build(paths / "repo")
    report = adapter.run_tests_with_coverage(tests_path, executable)

    assert report.coverage_available is False
    assert report.coverage_backend == "c-family-gcov"
    assert "pbgen-missing-gcov is not available" == report.coverage_unavailable_reason


def test_c_coverage_collects_gcov_report_when_tools_are_available(tmp_path: Path) -> None:
    if shutil.which("make") is None or shutil.which("cc") is None or shutil.which("gcov") is None:
        pytest.skip("make, cc, and gcov are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-c", config=config, local_path=FIXTURES / "make_c")
    paths = tmp_path / "artifacts" / "make-c"
    spec = TaskSpec.model_validate(read_data(paths / "task_spec.yaml"))
    tests_path = paths / "generated_tests"
    _write_canonical_cases(tests_path)

    report = run_c_family_coverage(
        spec,
        paths / "repo",
        tests_path,
        iteration=0,
        work_dir=paths / "reports" / "coverage_iteration_0",
        config=config,
    )

    assert report.coverage_available is True
    assert report.coverage_backend == "c-family-gcov"
    assert report.line_coverage is not None
    assert 0.0 <= report.line_coverage <= 1.0


def _write_canonical_cases(tests_path: Path) -> None:
    suite = ExecutableTestSuite(
        task_id="make-c",
        iteration=0,
        generator="fixture",
        cases=[
            ExecutableTestCase(
                test_id="add",
                task_id="make-c",
                args=["add", "2", "5"],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(exact="7\n"),
                source="fixture",
            ),
            ExecutableTestCase(
                test_id="bad-command",
                task_id="make-c",
                args=["wat"],
                expected_exit_code=2,
                expected_stderr=ExpectedOutput(contains=["unknown command"]),
                source="fixture",
            ),
        ],
    )
    write_data(tests_path / "test_cases_iteration_0.json", suite.model_dump(mode="json"))
