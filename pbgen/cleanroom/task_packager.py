"""Cleanroom solver/evaluator packaging."""

from __future__ import annotations

from typing import Any, TypedDict
import shutil
from pathlib import Path

from pbgen.cleanroom.asset_selector import copy_assets
from pbgen.cleanroom.docs_filter import copy_public_docs
from pbgen.cleanroom.leak_checker import run_leak_check
from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.logging.event_log import EventLogger
from pbgen.schemas import TaskSpec
from pbgen.serialization import read_data, write_data


EXCLUDED_SOLVER_PATTERNS = [
    ".git/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "artifacts/",
    "generated_tests/",
    "hidden_tests/",
    "gold/",
    "logs/",
    "reports/",
    "tests/",
    "test/",
    "executable/program",
    "*.pyc",
    "*.pyo",
    "*.egg-info/",
]


class CleanroomPackageInfo(TypedDict):
    solver: str
    evaluator: str
    leak_check: dict[str, object]
    solver_manifest: dict[str, Any]
    evaluator_manifest: dict[str, Any]


def package_cleanroom(task_id: str, config: PBGenConfig) -> CleanroomPackageInfo:
    """Create separated solver-visible and evaluator-only package outputs."""

    paths = ArtifactPaths(config, task_id)
    spec = TaskSpec.model_validate(read_data(paths.task_spec))
    solver = paths.packages / "solver"
    evaluator = paths.packages / "evaluator"
    if solver.exists():
        shutil.rmtree(solver)
    if evaluator.exists():
        shutil.rmtree(evaluator)

    (solver / "docs").mkdir(parents=True)
    (solver / "assets").mkdir(parents=True)
    copied_docs = copy_public_docs(paths.repo, spec.docs_paths, solver / "docs")
    copied_assets = copy_assets(paths.repo, spec.asset_paths, solver / "assets")
    (solver / "TASK.md").write_text(
        "\n".join(
            [
                f"# {task_id}",
                "",
                "Implement a candidate solution from source.",
                "",
                "Submit a source directory and a build script. The build script must",
                "produce an executable at `out/program` inside the submitted source tree.",
                "The evaluator will build that source and run hidden executable tests.",
                "",
                "Use the provided public docs/assets to infer the expected behavior.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (solver / "SUBMISSION.md").write_text(
        "\n".join(
            [
                "# Candidate Submission Contract",
                "",
                "- Provide a source directory.",
                "- Provide a build script path.",
                "- The build script runs from the source directory.",
                "- On success, it must create `out/program`.",
                "- Hidden tests and gold/reference binaries are evaluator-only.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_data(solver / "task.yaml", _solver_visible_task_spec(spec).model_dump(mode="json"))
    solver_manifest = _write_package_manifest(
        solver / "SOLVER_MANIFEST.json",
        package_type="solver",
        task_id=task_id,
        root=solver,
        included_public_files=[
            *(path.relative_to(solver).as_posix() for path in copied_docs),
            *(path.relative_to(solver).as_posix() for path in copied_assets),
            "SUBMISSION.md",
            "TASK.md",
            "task.yaml",
        ],
        excluded_patterns=EXCLUDED_SOLVER_PATTERNS,
    )

    (evaluator / "hidden_tests").mkdir(parents=True)
    (evaluator / "reports").mkdir(parents=True)
    (evaluator / "logs").mkdir(parents=True)
    (evaluator / "gold").mkdir(parents=True)
    if paths.generated_tests.exists():
        shutil.copytree(
            paths.generated_tests,
            evaluator / "hidden_tests",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "*.pyc"),
        )
    if paths.reports.exists():
        shutil.copytree(paths.reports, evaluator / "reports", dirs_exist_ok=True)
    if paths.logs.exists():
        shutil.copytree(paths.logs, evaluator / "logs", dirs_exist_ok=True)
    shutil.copy2(paths.executable, evaluator / "gold" / "program")
    write_data(evaluator / "task.yaml", spec.model_dump(mode="json"))
    evaluator_manifest = _write_package_manifest(
        evaluator / "EVALUATOR_MANIFEST.json",
        package_type="evaluator",
        task_id=task_id,
        root=evaluator,
        included_public_files=[],
        excluded_patterns=[],
    )

    leak_report = run_leak_check(task_id, solver, paths.reports / "leak_check_report.json", paths.event_log)
    shutil.copy2(paths.reports / "leak_check_report.json", evaluator / "reports" / "leak_check_report.json")
    EventLogger(paths.event_log).append(
        task_id=task_id,
        stage="cleanroom",
        event_type="cleanroom_packaged",
        metrics={
            "solver": solver.as_posix(),
            "evaluator": evaluator.as_posix(),
            "solver_files": solver_manifest["file_count"],
            "evaluator_files": evaluator_manifest["file_count"],
            "leak_check_passed": leak_report["passed"],
        },
    )
    return {
        "solver": solver.as_posix(),
        "evaluator": evaluator.as_posix(),
        "leak_check": leak_report,
        "solver_manifest": solver_manifest,
        "evaluator_manifest": evaluator_manifest,
    }


def _write_package_manifest(
    path: Path,
    *,
    package_type: str,
    task_id: str,
    root: Path,
    included_public_files: list[str],
    excluded_patterns: list[str],
) -> dict[str, object]:
    files = [
        item.relative_to(root).as_posix()
        for item in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix())
        if item.is_file()
    ]
    manifest: dict[str, object] = {
        "task_id": task_id,
        "package_type": package_type,
        "file_count": len(files),
        "files": files,
        "included_public_files": sorted(set(included_public_files)),
        "excluded_patterns": excluded_patterns,
    }
    write_data(path, manifest)
    return manifest


def _solver_visible_task_spec(spec: TaskSpec) -> TaskSpec:
    return spec.model_copy(
        update={
            "repo_url": "cleanroom://solver-package",
            "build_candidates": [],
            "entrypoint_candidates": [],
            "dependency_manifests": [],
            "metadata_warnings": [
                *spec.metadata_warnings,
                "Solver-visible task spec omits repository source paths, build probes, and gold executable metadata.",
            ],
        }
    )
