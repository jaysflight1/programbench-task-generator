from __future__ import annotations

from pathlib import Path

from pbgen.cleanroom.asset_selector import copy_assets
from pbgen.cleanroom.docs_filter import copy_public_docs
from pbgen.cleanroom.leak_checker import run_leak_check
from pbgen.cleanroom.task_packager import package_cleanroom
from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.released_package import release_task_package
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput, TaskSpec
from pbgen.serialization import read_data, write_data


def test_copy_public_docs_excludes_source_tests_and_generated_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (docs / "README.md").write_text("public docs\n", encoding="utf-8")
    (docs / ".gitignore").write_text("build/\n", encoding="utf-8")
    (docs / "helper.py").write_text("secret source\n", encoding="utf-8")
    (docs / "test_plan.md").write_text("hidden_tests/test_cases_iteration_0.json\n", encoding="utf-8")
    (docs / "tests").mkdir()
    (docs / "tests" / "test_behavior.py").write_text("secret\n", encoding="utf-8")
    output = tmp_path / "out"

    copied = copy_public_docs(repo, ["docs", "docs/.gitignore"], output)

    assert copied
    assert (output / "docs" / "README.md").exists()
    assert not (output / "docs" / ".gitignore").exists()
    assert not (output / ".gitignore").exists()
    assert not (output / "docs" / "helper.py").exists()
    assert not (output / "docs" / "test_plan.md").exists()
    assert not (output / "docs" / "tests").exists()


def test_copy_assets_rejects_source_and_hidden_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data.txt").write_text("public data\n", encoding="utf-8")
    (repo / "cmake").mkdir()
    (repo / "cmake" / "CMakeCache.txt").write_text(
        "PROJECT_SOURCE_DIR:STATIC=/Users/reviewer/work/repo\n",
        encoding="utf-8",
    )
    (repo / "model.py").write_text("source\n", encoding="utf-8")
    (repo / "hidden_tests").mkdir()
    (repo / "hidden_tests" / "fixture.txt").write_text("hidden\n", encoding="utf-8")

    copied = copy_assets(
        repo,
        ["data.txt", "model.py", "hidden_tests/fixture.txt", "cmake/CMakeCache.txt"],
        tmp_path / "assets",
    )

    assert [path.name for path in copied] == ["data.txt"]


def test_leak_check_detects_path_content_and_binary_hints(tmp_path: Path) -> None:
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "TASK.md").write_text("See generated_tests/test_cases_iteration_0.json\n", encoding="utf-8")
    (solver / "test_secret.txt").write_text("secret\n", encoding="utf-8")
    binary = solver / "executable"
    binary.mkdir()
    (binary / "program").write_bytes(b"\0ELF hidden_tests test_cases_iteration_0\0")

    report = run_leak_check(
        "demo",
        solver,
        tmp_path / "leak_report.json",
        tmp_path / "events.jsonl",
    )

    assert report["passed"] is False
    findings = "\n".join(report["findings"])
    assert "test-like path visible" in findings
    assert "forbidden content pattern" in findings
    assert "binary leak hint visible" in findings


def test_package_cleanroom_writes_manifests_and_excludes_solver_leaks(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    paths = _write_minimal_task(config, "demo")

    result = package_cleanroom("demo", config)

    solver = paths.packages / "solver"
    evaluator = paths.packages / "evaluator"
    solver_manifest = read_data(solver / "SOLVER_MANIFEST.json")
    evaluator_manifest = read_data(evaluator / "EVALUATOR_MANIFEST.json")
    leak_report = read_data(paths.reports / "leak_check_report.json")

    assert result["leak_check"]["passed"] is True
    assert leak_report["passed"] is True
    assert solver_manifest["package_type"] == "solver"
    assert evaluator_manifest["package_type"] == "evaluator"
    solver_files = "\n".join(solver_manifest["files"])
    assert "hidden_tests" not in solver_files
    assert "generated_tests" not in solver_files
    assert "generation_events" not in solver_files
    assert "executable/program" not in solver_files
    assert (solver / "SUBMISSION.md").exists()
    assert (solver / "task.yaml").exists()
    assert not (solver / "executable" / "program").exists()
    solver_task = read_data(solver / "task.yaml")
    assert solver_task["repo_url"] == "cleanroom://solver-package"
    assert "Users" not in (solver / "task.yaml").read_text(encoding="utf-8")
    assert (evaluator / "hidden_tests" / "test_cases_iteration_0.json").exists()
    assert (evaluator / "reports" / "leak_check_report.json").exists()
    assert (evaluator / "gold" / "program").exists()


def test_release_manifest_includes_cleanroom_audit_fields(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    _write_minimal_task(config, "demo")

    manifest = release_task_package("demo", config)
    persisted = read_data(tmp_path / "artifacts" / "demo" / "packages" / "release_manifest.json")

    assert manifest.leak_check_passed is True
    assert manifest.solver_includes_gold_executable is False
    assert manifest.solver_file_count is not None and manifest.solver_file_count > 0
    assert manifest.evaluator_file_count is not None and manifest.evaluator_file_count > 0
    assert manifest.excluded_patterns
    assert persisted["leak_check_passed"] is True
    assert persisted["solver_includes_gold_executable"] is False
    assert persisted["solver_manifest_path"].endswith("SOLVER_MANIFEST.json")


def _write_minimal_task(config: PBGenConfig, task_id: str) -> ArtifactPaths:
    paths = ArtifactPaths(config, task_id)
    paths.ensure_base_dirs()
    paths.repo.mkdir(parents=True, exist_ok=True)
    docs = paths.repo / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("public docs\n", encoding="utf-8")
    (docs / "secret.py").write_text("source should not ship\n", encoding="utf-8")
    assets = paths.repo / "assets"
    assets.mkdir()
    (assets / "data.txt").write_text("public data\n", encoding="utf-8")
    write_data(
        paths.task_spec,
        TaskSpec(
            task_id=task_id,
            repo_url="local",
            commit_sha="test",
            language="python",
            build_system="script",
            docs_paths=["docs"],
            asset_paths=["assets/data.txt"],
        ).model_dump(mode="json"),
    )
    paths.executable.parent.mkdir(parents=True, exist_ok=True)
    paths.executable.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
    paths.executable.chmod(0o755)
    suite = ExecutableTestSuite(
        task_id=task_id,
        iteration=0,
        cases=[
            ExecutableTestCase(
                test_id="test_ok",
                task_id=task_id,
                args=[],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(exact="ok\n"),
                source="unit",
            )
        ],
    )
    write_data(paths.generated_tests / "test_cases_iteration_0.json", suite.model_dump(mode="json"))
    write_data(paths.reports / "suite_quality_report.json", {"task_id": task_id})
    write_data(paths.logs / "generation_events.jsonl", {"should": "stay evaluator-only"})
    return paths
