"""Cleanroom solver/evaluator packaging."""

from __future__ import annotations

import shutil

from pbgen.cleanroom.asset_selector import copy_assets
from pbgen.cleanroom.docs_filter import copy_public_docs
from pbgen.cleanroom.leak_checker import run_leak_check
from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.logging.event_log import EventLogger
from pbgen.schemas import TaskSpec
from pbgen.serialization import read_data, write_data


def package_cleanroom(task_id: str, config: PBGenConfig) -> dict[str, object]:
    """Create separated solver-visible and evaluator-only package outputs."""

    paths = ArtifactPaths(config, task_id)
    spec = TaskSpec.model_validate(read_data(paths.task_spec))
    solver = paths.packages / "solver"
    evaluator = paths.packages / "evaluator"
    if solver.exists():
        shutil.rmtree(solver)
    if evaluator.exists():
        shutil.rmtree(evaluator)

    (solver / "executable").mkdir(parents=True)
    (solver / "docs").mkdir(parents=True)
    (solver / "assets").mkdir(parents=True)
    shutil.copy2(paths.executable, solver / "executable" / "program")
    copy_public_docs(paths.repo, spec.docs_paths, solver / "docs")
    copy_assets(paths.repo, spec.asset_paths, solver / "assets")
    (solver / "TASK.md").write_text(
        f"# {task_id}\n\nUse `executable/program` and the provided docs/assets to reproduce the program behavior.\n",
        encoding="utf-8",
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
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    if paths.reports.exists():
        shutil.copytree(paths.reports, evaluator / "reports", dirs_exist_ok=True)
    if paths.logs.exists():
        shutil.copytree(paths.logs, evaluator / "logs", dirs_exist_ok=True)
    shutil.copy2(paths.executable, evaluator / "gold" / "program")
    write_data(evaluator / "task.yaml", spec.model_dump(mode="json"))

    leak_report = run_leak_check(task_id, solver, paths.reports / "leak_check_report.json", paths.event_log)
    shutil.copy2(paths.reports / "leak_check_report.json", evaluator / "reports" / "leak_check_report.json")
    EventLogger(paths.event_log).append(
        task_id=task_id,
        stage="cleanroom",
        event_type="cleanroom_packaged",
        metrics={"solver": solver.as_posix(), "evaluator": evaluator.as_posix()},
    )
    return {"solver": solver.as_posix(), "evaluator": evaluator.as_posix(), "leak_check": leak_report}
