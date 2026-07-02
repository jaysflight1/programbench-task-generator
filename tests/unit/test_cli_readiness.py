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
        ]
    )
    profile = TaskProfile(
        generation_backend="local",
        model_command=["old-model"],
        model_name="old",
        model_temperature=0.9,
    )

    updated = _profile_with_generation_args(profile, args)

    assert updated.generation_backend == "model"
    assert updated.model_command == ["fake-model", "--json"]
    assert updated.model_name == "review-model"
    assert updated.model_temperature == 0.05
