"""Released solver/evaluator package product workflow."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pbgen.cleanroom.task_packager import package_cleanroom
from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.eval.executable_runner import load_canonical_suites
from pbgen.schemas import ReleasedTaskPackageManifest, TaskSpec
from pbgen.serialization import read_data, write_data


def release_task_package(task_id: str, config: PBGenConfig) -> ReleasedTaskPackageManifest:
    """Create cleanroom packages and write a release manifest."""

    package_cleanroom(task_id, config)
    paths = ArtifactPaths(config, task_id)
    spec = TaskSpec.model_validate(read_data(paths.task_spec))
    solver = paths.packages / "solver"
    evaluator = paths.packages / "evaluator"
    hidden_tests = evaluator / "hidden_tests"
    manifest = ReleasedTaskPackageManifest(
        task_id=task_id,
        language=spec.language,
        build_system=spec.build_system,
        solver_package=solver,
        evaluator_package=evaluator,
        hidden_tests_path=hidden_tests,
        runtime_policy=config.execution_policy,
        accepted_test_count=_canonical_case_count(paths.generated_tests),
        package_hash=_hash_package_tree(paths.packages),
    )
    write_data(paths.packages / "release_manifest.json", manifest.model_dump(mode="json"))
    write_data(evaluator / "release_manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _canonical_case_count(tests_path: Path) -> int:
    return sum(len(suite.cases) for suite in load_canonical_suites(tests_path))


def _hash_package_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {"release_manifest.json", "evaluator/release_manifest.json"}:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
