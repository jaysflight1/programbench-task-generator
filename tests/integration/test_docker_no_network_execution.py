from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from pbgen.candidate_evaluator import evaluate_source_submission
from pbgen.config import PBGenConfig
from pbgen.schemas import (
    CandidateSubmission,
    ExecutableTestCase,
    ExecutableTestSuite,
    ExpectedOutput,
    ReleasedTaskPackageManifest,
)
from pbgen.serialization import write_data


def _docker_image() -> str | None:
    image = os.environ.get("PBGEN_DOCKER_TEST_IMAGE")
    if not image or shutil.which("docker") is None:
        return None
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False,
        text=True,
        capture_output=True,
        timeout=20,
    )
    return image if result.returncode == 0 else None


@pytest.mark.skipif(
    _docker_image() is None,
    reason="set PBGEN_DOCKER_TEST_IMAGE to a locally available image to run Docker smoke test",
)
def test_docker_no_network_evaluates_source_submission_when_image_available(
    tmp_path: Path,
) -> None:
    image = _docker_image()
    assert image is not None
    evaluator = _write_evaluator_package(tmp_path)
    source, build_script = _write_candidate_source(tmp_path)

    report = evaluate_source_submission(
        CandidateSubmission(
            package_path=evaluator,
            submission_source=source,
            build_script=build_script,
        ),
        PBGenConfig(
            workspace_root=tmp_path,
            execution_policy="docker-no-network",
            docker_image=image,
            build_timeout_seconds=30,
        ),
    )

    assert report.resolved is True
    assert report.build_success is True
    assert report.pass_rate == 1.0
    assert report.runtime_policy == "docker-no-network"


def _write_evaluator_package(tmp_path: Path) -> Path:
    evaluator = tmp_path / "evaluator"
    hidden_tests = evaluator / "hidden_tests"
    hidden_tests.mkdir(parents=True)
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=0,
        cases=[
            ExecutableTestCase(
                test_id="test_help",
                task_id="demo",
                args=["--help"],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(exact="Usage: candidate\n"),
                expected_stderr=ExpectedOutput(exact=""),
                source="unit",
            )
        ],
    )
    write_data(hidden_tests / "test_cases_iteration_0.json", suite.model_dump(mode="json"))
    manifest = ReleasedTaskPackageManifest(
        task_id="demo",
        language="python",
        build_system="script",
        solver_package=tmp_path / "solver",
        evaluator_package=evaluator,
        hidden_tests_path=hidden_tests,
        runtime_policy="docker-no-network",
        accepted_test_count=1,
        package_hash="test-hash",
    )
    write_data(evaluator / "release_manifest.json", manifest.model_dump(mode="json"))
    return evaluator


def _write_candidate_source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "candidate"
    source.mkdir()
    build_script = source / "build.py"
    build_script.write_text(
        "from pathlib import Path\n"
        "out = Path('out')\n"
        "out.mkdir(exist_ok=True)\n"
        "program = out / 'program'\n"
        "program.write_text("
        "'#!/usr/bin/env python3\\n'"
        "'import sys\\n'"
        "'if sys.argv[1:] == [\"--help\"]:\\n'"
        "'    print(\"Usage: candidate\")\\n'"
        "'    raise SystemExit(0)\\n'"
        "'raise SystemExit(2)\\n',"
        "encoding='utf-8')\n"
        "program.chmod(0o755)\n",
        encoding="utf-8",
    )
    return source, build_script
