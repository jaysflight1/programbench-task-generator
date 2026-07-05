from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from pbgen.cli import _build_parser, _profile_with_generation_args, main
from pbgen.schemas import TaskProfile


def test_export_submission_cli_writes_clean_archive(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    project = tmp_path / "project"
    (project / "pbgen").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "prompts").mkdir()
    (project / "examples").mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (project / "pbgen" / "__init__.py").write_text("", encoding="utf-8")
    (project / "artifacts").mkdir()
    (project / "artifacts" / "local.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "submission.zip"

    exit_code = main(
        [
            "export-submission",
            "--project-root",
            str(project),
            "--output",
            str(archive),
        ]
    )

    assert exit_code == 0
    assert "Wrote clean submission archive" in capsys.readouterr().out
    with ZipFile(archive) as zip_file:
        names = set(zip_file.namelist())
    assert "README.md" in names
    assert "pyproject.toml" in names
    assert "pbgen/__init__.py" in names
    assert "artifacts/local.json" not in names


def test_run_task_generation_cli_args_override_profile_defaults() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run-task",
            "--profile",
            "pbgen_task.yaml",
            "--generation-backend",
            "model",
            "--model-command",
            "fake-model --json",
            "--model-name",
            "review-model",
            "--model-temperature",
            "0.05",
            "--model-timeout-seconds",
            "900",
            "--model-max-output-chars",
            "2000000",
            "--model-require-structured-cases",
        ]
    )
    profile = TaskProfile(
        generation_backend="local",
        model_command=["old-model"],
        model_name="old",
        model_temperature=0.9,
        model_timeout_seconds=120,
        model_max_output_chars=200_000,
        model_require_structured_cases=False,
    )

    updated = _profile_with_generation_args(profile, args)

    assert updated.generation_backend == "model"
    assert updated.model_command == ["fake-model", "--json"]
    assert updated.model_name == "review-model"
    assert updated.model_temperature == 0.05
    assert updated.model_timeout_seconds == 900
    assert updated.model_max_output_chars == 2_000_000
    assert updated.model_require_structured_cases is True


def test_product_workflow_cli_commands_parse() -> None:
    parser = _build_parser()

    construct = parser.parse_args(
        [
            "construct-task",
            "--profile",
            "pbgen_task.yaml",
            "--iterations",
            "2",
            "--build-system",
            "python-package",
        ]
    )
    release = parser.parse_args(["release-package", "--task-id", "demo"])
    evaluate = parser.parse_args(
        [
            "evaluate-submission",
            "--package",
            "artifacts/demo/packages/evaluator",
            "--submission-source",
            "candidate",
            "--build-script",
            "candidate/build.sh",
            "--trusted-local",
            "--model-name",
            "model-a",
            "--attempt-id",
            "attempt-1",
            "--api-calls",
            "12",
            "--cost-usd",
            "0.5",
            "--docker-image",
            "pbgen-eval:py-c",
        ]
    )
    solver = parser.parse_args(
        [
            "solve-with-openai",
            "--solver-package",
            "artifacts/demo/packages/solver",
            "--output-dir",
            "solver_runs/gpt-5.5/demo",
            "--model-name",
            "gpt-5.5",
            "--attempt-id",
            "attempt-1",
            "--max-rounds",
            "3",
            "--reasoning-effort",
            "xhigh",
        ]
    )
    performance = parser.parse_args(
        [
            "write-performance-report",
            "--candidate-report",
            "reports/a/candidate_evaluation_report.json",
            "--candidate-report",
            "reports/b/candidate_evaluation_report.json",
            "--output",
            "PROGRAMBENCH_PERFORMANCE.md",
            "--model-name",
            "model-a",
        ]
    )

    assert construct.command == "construct-task"
    assert construct.profile == "pbgen_task.yaml"
    assert construct.iterations == 2
    assert construct.build_system == "python-package"
    assert release.command == "release-package"
    assert release.task_id == "demo"
    assert evaluate.command == "evaluate-submission"
    assert evaluate.submission_source == "candidate"
    assert evaluate.build_script == "candidate/build.sh"
    assert evaluate.trusted_local is True
    assert evaluate.model_name == "model-a"
    assert evaluate.attempt_id == "attempt-1"
    assert evaluate.api_calls == 12
    assert evaluate.cost_usd == 0.5
    assert evaluate.docker_image == "pbgen-eval:py-c"
    assert solver.command == "solve-with-openai"
    assert solver.solver_package == "artifacts/demo/packages/solver"
    assert solver.output_dir == "solver_runs/gpt-5.5/demo"
    assert solver.model_name == "gpt-5.5"
    assert solver.reasoning_effort == "xhigh"
    assert performance.command == "write-performance-report"
    assert performance.candidate_report == [
        "reports/a/candidate_evaluation_report.json",
        "reports/b/candidate_evaluation_report.json",
    ]
    assert performance.model_name == "model-a"


def test_write_model_run_report_cli_command_parses() -> None:
    parser = _build_parser()

    parsed = parser.parse_args(
        [
            "write-model-run-report",
            "--artifact-pair",
            "artifacts/local_py",
            "artifacts/model_py",
            "--artifact-pair",
            "artifacts/local_c",
            "artifacts/model_c",
            "--output",
            "MODEL_RUN_REPORT.md",
        ]
    )

    assert parsed.command == "write-model-run-report"
    assert parsed.artifact_pair == [
        ["artifacts/local_py", "artifacts/model_py"],
        ["artifacts/local_c", "artifacts/model_c"],
    ]
    assert parsed.output == "MODEL_RUN_REPORT.md"
