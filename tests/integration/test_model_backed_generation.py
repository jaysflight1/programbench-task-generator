from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from pbgen.config import PBGenConfig
from pbgen.pipeline import run_task_profile
from pbgen.schemas import TaskProfile
from pbgen.serialization import read_data


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_model_backed_python_fixture_runs_end_to_end(tmp_path: Path) -> None:
    model_script = _write_fake_model(tmp_path / "fake_model.py")
    profile = TaskProfile(
        task_id="model_python_fixture",
        local_path=FIXTURES / "python_package_cli",
        primary_binary="pkgcalc",
        expected_language="python",
        trusted_local=True,
        iterations=1,
        generation_backend="model",
        model_command=[sys.executable, model_script.as_posix()],
        model_name="fake-review-model",
        model_require_structured_cases=True,
        coverage_target=0.1,
        min_coverage_delta=0.0,
    )

    summary = run_task_profile(profile, _fast_config(tmp_path))

    _assert_model_artifacts(tmp_path / "artifacts" / "model_python_fixture")
    assert summary.gold_pass_rate == 1.0


@pytest.mark.skipif(
    shutil.which("cmake") is None or shutil.which("cc") is None,
    reason="CMake model-backed fixture requires cmake and a C compiler",
)
def test_model_backed_cmake_fixture_runs_end_to_end(tmp_path: Path) -> None:
    model_script = _write_fake_model(tmp_path / "fake_model.py")
    profile = TaskProfile(
        task_id="model_cmake_fixture",
        local_path=FIXTURES / "cmake_c",
        primary_binary="cmake_calc",
        expected_language="c/c++",
        trusted_local=True,
        iterations=1,
        generation_backend="model",
        model_command=[sys.executable, model_script.as_posix()],
        model_name="fake-review-model",
        model_require_structured_cases=True,
        coverage_target=0.1,
        min_coverage_delta=0.0,
    )

    summary = run_task_profile(profile, _fast_config(tmp_path), build_system="cmake")

    _assert_model_artifacts(tmp_path / "artifacts" / "model_cmake_fixture")
    assert summary.gold_pass_rate == 1.0


def _fast_config(tmp_path: Path) -> PBGenConfig:
    return PBGenConfig(
        workspace_root=tmp_path,
        determinism_runs=2,
        benchmark_trials=1,
        benchmark_warmups=0,
        model_timeout_seconds=30,
    )


def _assert_model_artifacts(root: Path) -> None:
    reports = root / "reports"
    generated = root / "generated_tests"
    evaluator_reports = root / "packages" / "evaluator" / "reports"
    solver = root / "packages" / "solver"

    assert (reports / "model_prompt_iteration_0.txt").exists()
    assert (reports / "model_response_iteration_0.json").exists()
    assert (reports / "model_request_iteration_0.json").exists()
    assert (reports / "model_generation_iteration_0.json").exists()
    assert (generated / "test_cases_iteration_0.json").exists()
    assert (evaluator_reports / "model_prompt_iteration_0.txt").exists()
    assert not (solver / "reports").exists()

    diagnostics = read_data(reports / "model_generation_iteration_0.json")
    request = read_data(reports / "model_request_iteration_0.json")
    assert diagnostics["diagnostics"][0]["accepted"] is True
    assert request["model"] == "fake-review-model"
    assert request["client_metadata"]["adapter"] == "fake-model"


def _write_fake_model(path: Path) -> Path:
    path.write_text(
        """
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

_prompt = sys.stdin.read()
metadata = os.environ.get("PBGEN_MODEL_METADATA_PATH")
if metadata:
    Path(metadata).write_text(
        json.dumps({"adapter": "fake-model", "estimated_cost_usd": 0.0}),
        encoding="utf-8",
    )
print(json.dumps({"test_cases": [
    {
        "test_id": "model_help",
        "args": ["--help"],
        "behavior_category": "flag",
        "source": "fake-model",
        "source_evidence": "help behavior"
    }
]}))
""".lstrip(),
        encoding="utf-8",
    )
    return path
