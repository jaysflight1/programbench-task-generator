from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from pbgen.config import PBGenConfig
from pbgen.pipeline import run_task_profile
from pbgen.schemas import ExecutableTestSuite, TaskProfile
from pbgen.serialization import read_data


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_python_cli_fixture_produces_programbench_artifacts(tmp_path: Path) -> None:
    task_id = "case_python_cli"
    profile = TaskProfile(
        task_id=task_id,
        local_path=FIXTURES / "python_package_cli",
        primary_binary="pkgcalc",
        expected_language="python",
        iterations=1,
        coverage_target=0.5,
        benchmark_commands=[["--version"], ["echo", "hello"]],
        trusted_local=True,
    )

    summary = run_task_profile(profile, _fast_config(tmp_path))

    _assert_case_study_artifacts(tmp_path, task_id)
    assert summary.task_id == task_id
    assert summary.language == "python"
    assert summary.generated_tests > 0
    assert summary.gold_pass_rate == 1.0

    coverage = read_data(tmp_path / "artifacts" / task_id / "reports" / "coverage_report_iteration_0.json")
    assert coverage["coverage_backend"] in {"python-coverage.py", "coverage-error"}


@pytest.mark.skipif(
    shutil.which("make") is None or (shutil.which("cc") is None and shutil.which("gcc") is None),
    reason="C/Make fixture requires make and a C compiler",
)
def test_c_make_fixture_produces_programbench_artifacts(tmp_path: Path) -> None:
    task_id = "case_c_make"
    profile = TaskProfile(
        task_id=task_id,
        local_path=FIXTURES / "make_c_cli",
        primary_binary="ccalc",
        expected_language="c/c++",
        iterations=1,
        coverage_target=0.2,
        benchmark_commands=[["--version"]],
        trusted_local=True,
    )

    summary = run_task_profile(profile, _fast_config(tmp_path), build_system="make")

    _assert_case_study_artifacts(tmp_path, task_id)
    assert summary.task_id == task_id
    assert summary.language == "c/c++"
    assert summary.generated_tests > 0
    assert summary.gold_pass_rate == 1.0

    coverage_path = tmp_path / "artifacts" / task_id / "reports" / "coverage_report_iteration_0.json"
    coverage = read_data(coverage_path)
    assert coverage["coverage_backend"] in {"c-family-gcov", "coverage-error"}
    if not coverage["coverage_available"]:
        assert (coverage_path.parent / "coverage_unavailable_report.json").exists()


def _fast_config(tmp_path: Path) -> PBGenConfig:
    return PBGenConfig(
        workspace_root=tmp_path,
        determinism_runs=2,
        benchmark_trials=1,
        benchmark_warmups=0,
    )


def _assert_case_study_artifacts(tmp_path: Path, task_id: str) -> None:
    root = tmp_path / "artifacts" / task_id
    generated = root / "generated_tests"
    reports = root / "reports"
    qc = root / "qc"
    packages = root / "packages"

    expected_paths = [
        root / "task_spec.yaml",
        root / "behavior_surface.yaml",
        generated / "test_behavior_iter_0.py",
        generated / "test_cases_iteration_0.json",
        reports / "coverage_report_iteration_0.json",
        reports / "lint_report_iteration_0.json",
        reports / "mutation_lite_report_iteration_0.json",
        reports / "redundancy_report_iteration_0.json",
        reports / "suite_quality_report.json",
        reports / "mutation_lite_report.json",
        reports / "reward_shape_report.json",
        reports / "efficiency_manifest.json",
        qc / "qc_queue.json",
        qc / "qc_queue.md",
        root / "RUN_SUMMARY.md",
        reports / "RUN_SUMMARY.json",
        packages / "solver" / "TASK.md",
        packages / "solver" / "SUBMISSION.md",
        packages / "solver" / "SOLVER_MANIFEST.json",
        packages / "solver" / "task.yaml",
        packages / "evaluator" / "task.yaml",
        packages / "evaluator" / "EVALUATOR_MANIFEST.json",
        packages / "evaluator" / "gold" / "program",
        packages / "evaluator" / "hidden_tests" / "test_cases_iteration_0.json",
        packages / "evaluator" / "reports" / "suite_quality_report.json",
    ]
    missing = [path for path in expected_paths if not path.exists()]
    assert missing == []
    assert not (packages / "solver" / "executable" / "program").exists()

    suite = ExecutableTestSuite.model_validate(read_data(generated / "test_cases_iteration_0.json"))
    assert suite.cases

    suite_quality = read_data(reports / "suite_quality_report.json")
    reward_shape = read_data(reports / "reward_shape_report.json")
    assert suite_quality["gold_pass_rate"] == 1.0
    assert reward_shape["correctness_gate_passed"]
