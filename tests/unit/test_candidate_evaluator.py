from __future__ import annotations

from pathlib import Path

import pytest

from pbgen.errors import PBGenError
from pbgen.candidate_evaluator import evaluate_source_submission
from pbgen.config import PBGenConfig
from pbgen.schemas import (
    CandidateSubmission,
    ExecutableTestCase,
    ExecutableTestSuite,
    ExpectedOutput,
    ReleasedTaskPackageManifest,
)
from pbgen.serialization import read_data, write_data


def test_source_submission_passes_all_hidden_tests(tmp_path: Path) -> None:
    evaluator = _write_evaluator_package(
        tmp_path,
        [
            _case("test_help", ["--help"], "Usage: candidate\n"),
            _case("test_version", ["--version"], "candidate 1.0\n"),
        ],
    )
    source, build_script = _write_candidate_source(tmp_path, program_body=_passing_program())

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
        ),
        _trusted_config(tmp_path),
    )

    assert report.resolved is True
    assert report.build_success is True
    assert report.tests_passed == 2
    assert report.total_tests == 2
    assert report.pass_rate == pytest.approx(1.0)
    assert report.programbench_metrics is not None
    assert report.programbench_metrics.resolved is True
    assert report.programbench_metrics.almost_resolved is True
    assert report.executable_path is not None
    assert report.executable_path.name == "program"

    persisted = read_data(evaluator / "reports" / "candidate_evaluation_report.json")
    assert persisted["resolved"] is True
    assert persisted["tests_passed"] == 2
    metrics = read_data(evaluator / "reports" / "programbench_metrics.json")
    assert metrics["resolved"] is True
    assert metrics["almost_resolved"] is True
    assert metrics["test_pass_rate"] == pytest.approx(1.0)


def test_source_submission_reports_partial_hidden_test_pass_rate(tmp_path: Path) -> None:
    evaluator = _write_evaluator_package(
        tmp_path,
        [
            _case("test_help", ["--help"], "Usage: candidate\n"),
            _case("test_version", ["--version"], "candidate 1.0\n"),
        ],
    )
    source, build_script = _write_candidate_source(tmp_path, program_body=_partial_program())

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
        ),
        _trusted_config(tmp_path),
    )

    assert report.resolved is False
    assert report.build_success is True
    assert report.tests_passed == 1
    assert report.total_tests == 2
    assert report.pass_rate == pytest.approx(0.5)
    assert report.programbench_metrics is not None
    assert report.programbench_metrics.resolved is False
    assert report.programbench_metrics.almost_resolved is False


def test_source_submission_tracks_programbench_model_metadata_and_disqualification(
    tmp_path: Path,
) -> None:
    evaluator = _write_evaluator_package(tmp_path, [_case("test_help", ["--help"], "Usage: candidate\n")])
    source, build_script = _write_candidate_source(tmp_path, program_body=_passing_program())

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
            model_name="model-a",
            attempt_id="run-001",
            api_calls=42,
            cost_usd=1.25,
            cheating_flagged=True,
            disqualification_reason="source lookup",
        ),
        _trusted_config(tmp_path),
    )

    assert report.pass_rate == pytest.approx(1.0)
    assert report.programbench_metrics is not None
    assert report.programbench_metrics.model_name == "model-a"
    assert report.programbench_metrics.attempt_id == "run-001"
    assert report.programbench_metrics.api_calls == 42
    assert report.programbench_metrics.cost_usd == pytest.approx(1.25)
    assert report.programbench_metrics.disqualified is True
    assert report.programbench_metrics.resolved is False
    assert report.programbench_metrics.raw_test_pass_rate == pytest.approx(1.0)
    assert report.programbench_metrics.test_pass_rate == pytest.approx(0.0)


def test_source_submission_reports_build_failure(tmp_path: Path) -> None:
    evaluator = _write_evaluator_package(tmp_path, [_case("test_help", ["--help"], "Usage\n")])
    source, build_script = _write_candidate_source(
        tmp_path,
        build_body="raise SystemExit(7)\n",
    )

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
        ),
        _trusted_config(tmp_path),
    )

    assert report.resolved is False
    assert report.build_success is False
    assert report.total_tests == 0
    assert report.pass_rate == 0.0
    assert report.build_log_path is not None
    assert report.build_log_path.exists()
    assert report.reason == "candidate build script exited with status 7"


def test_source_submission_requires_out_program(tmp_path: Path) -> None:
    evaluator = _write_evaluator_package(tmp_path, [_case("test_help", ["--help"], "Usage\n")])
    source, build_script = _write_candidate_source(
        tmp_path,
        build_body="from pathlib import Path\nPath('out').mkdir()\n",
    )

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
        ),
        _trusted_config(tmp_path),
    )

    assert report.resolved is False
    assert report.build_success is False
    assert report.reason == "candidate build script did not produce out/program"


def test_source_submission_reports_hidden_test_execution_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = _write_evaluator_package(tmp_path, [_case("test_help", ["--help"], "Usage\n")])
    source, build_script = _write_candidate_source(tmp_path, program_body=_passing_program())

    def fail_generated_suite(*args: object, **kwargs: object) -> object:
        raise PBGenError("hidden tests require canonical cases")

    monkeypatch.setattr(
        "pbgen.candidate_evaluator.run_generated_suite",
        fail_generated_suite,
    )

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
        ),
        _trusted_config(tmp_path),
    )

    assert report.resolved is False
    assert report.build_success is True
    assert report.reason == "hidden tests require canonical cases"


def test_source_submission_can_write_candidate_artifacts_outside_evaluator_package(
    tmp_path: Path,
) -> None:
    evaluator = _write_evaluator_package(
        tmp_path,
        [_case("test_help", ["--help"], "Usage: candidate\n")],
    )
    source, build_script = _write_candidate_source(tmp_path, program_body=_passing_program())
    output_dir = tmp_path / "candidate-output"

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
            output_dir=output_dir,
        ),
        _trusted_config(tmp_path),
    )

    assert report.resolved is True
    assert report.build_log_path == output_dir / "candidate_runs" / "latest" / "build.log"
    assert (output_dir / "candidate_runs" / "latest" / "source" / "out" / "program").exists()
    assert (output_dir / "reports" / "candidate_evaluation_report.json").exists()
    assert not (evaluator / "candidate_runs").exists()
    assert not (evaluator / "reports" / "candidate_evaluation_report.json").exists()


def test_docker_policy_reports_unavailable_docker_without_host_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pbgen.security.command_executor.shutil.which", lambda _: None)
    evaluator = _write_evaluator_package(tmp_path, [_case("test_help", ["--help"], "Usage\n")])
    source, build_script = _write_candidate_source(tmp_path, program_body=_passing_program())

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
        ),
        PBGenConfig(workspace_root=tmp_path, execution_policy="docker-no-network"),
    )

    assert report.resolved is False
    assert report.build_success is False
    assert "Docker executable is not available" in (report.reason or "")
    assert report.build_log_path is not None
    assert "Docker executable is not available" in report.build_log_path.read_text(
        encoding="utf-8"
    )
    validation = read_data(evaluator / "reports" / "no_network_validation_report.json")
    assert validation["status"] == "blocked"
    assert validation["runtime_policy"] == "docker-no-network"
    assert validation["validated"] is False
    assert "Docker executable is not available" in validation["reason"]
    assert not (evaluator / "candidate_runs" / "latest" / "source" / "out" / "program").exists()


def _trusted_config(tmp_path: Path) -> PBGenConfig:
    return PBGenConfig(
        workspace_root=tmp_path,
        execution_policy="trusted-local",
        trusted_local_execution=True,
        build_timeout_seconds=10,
    )


def _write_evaluator_package(
    tmp_path: Path,
    cases: list[ExecutableTestCase],
) -> Path:
    evaluator = tmp_path / "evaluator"
    hidden_tests = evaluator / "hidden_tests"
    hidden_tests.mkdir(parents=True)
    suite = ExecutableTestSuite(task_id="demo", iteration=0, cases=cases)
    write_data(hidden_tests / "test_cases_iteration_0.json", suite.model_dump(mode="json"))
    manifest = ReleasedTaskPackageManifest(
        task_id="demo",
        language="python",
        build_system="script",
        solver_package=tmp_path / "solver",
        evaluator_package=evaluator,
        hidden_tests_path=hidden_tests,
        runtime_policy="trusted-local",
        accepted_test_count=len(cases),
        package_hash="test-hash",
    )
    write_data(evaluator / "release_manifest.json", manifest.model_dump(mode="json"))
    return evaluator


def _case(test_id: str, args: list[str], stdout: str) -> ExecutableTestCase:
    return ExecutableTestCase(
        test_id=test_id,
        task_id="demo",
        args=args,
        expected_exit_code=0,
        expected_stdout=ExpectedOutput(exact=stdout),
        expected_stderr=ExpectedOutput(exact=""),
        source="unit",
    )


def _write_candidate_source(
    tmp_path: Path,
    *,
    program_body: str | None = None,
    build_body: str | None = None,
) -> tuple[Path, Path]:
    source = tmp_path / "candidate"
    source.mkdir()
    build_script = source / "build.py"
    if build_body is None:
        assert program_body is not None
        build_body = (
            "from pathlib import Path\n"
            "out = Path('out')\n"
            "out.mkdir(exist_ok=True)\n"
            "program = out / 'program'\n"
            f"program.write_text({program_body!r}, encoding='utf-8')\n"
            "program.chmod(0o755)\n"
        )
    build_script.write_text(build_body, encoding="utf-8")
    return source, build_script


def _passing_program() -> str:
    return """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--help"]:
    print("Usage: candidate")
    raise SystemExit(0)
if sys.argv[1:] == ["--version"]:
    print("candidate 1.0")
    raise SystemExit(0)
raise SystemExit(2)
"""


def _partial_program() -> str:
    return """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--help"]:
    print("Usage: candidate")
    raise SystemExit(0)
raise SystemExit(2)
"""
