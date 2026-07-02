import sys
from pathlib import Path

import pytest

from pbgen.config import PBGenConfig
from pbgen.efficiency.efficiency_score import score_efficiency
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput
from pbgen.serialization import read_data
from pbgen.serialization import write_data


def test_below_correctness_gate_is_not_eligible(tmp_path) -> None:
    result = score_efficiency(
        "demo",
        tmp_path / "missing-reference",
        tmp_path / "missing-candidate",
        0.5,
        tmp_path / "efficiency_manifest.json",
        PBGenConfig(workspace_root=tmp_path),
        benchmark_commands=[["bench"]],
    )

    assert not result.eligible
    assert result.reason == "correctness 0.500 below gate 1.000"

    persisted = read_data(tmp_path / "efficiency_manifest.json")
    assert not persisted["eligible"]
    assert persisted["reason"] == "correctness 0.500 below gate 1.000"


def test_no_benchmark_commands_is_not_eligible(tmp_path) -> None:
    result = score_efficiency(
        "demo",
        tmp_path / "missing-reference",
        tmp_path / "missing-candidate",
        1.0,
        tmp_path / "efficiency_manifest.json",
        PBGenConfig(workspace_root=tmp_path),
    )

    assert not result.eligible
    assert result.reason == "no benchmark commands available"

    persisted = read_data(tmp_path / "efficiency_manifest.json")
    assert not persisted["eligible"]
    assert persisted["reason"] == "no benchmark commands available"


def test_tiny_script_benchmark_command_is_measured(tmp_path) -> None:
    script = _write_script(
        tmp_path / "bench.py",
        """
        import sys
        total = sum(int(value) for value in sys.argv[2:])
        print(f"{sys.argv[1]}:{total}")
        """,
    )
    executable = Path(sys.executable)

    result = score_efficiency(
        "demo",
        executable,
        executable,
        1.0,
        tmp_path / "efficiency_manifest.json",
        PBGenConfig(workspace_root=tmp_path, benchmark_trials=1, benchmark_warmups=0),
        benchmark_commands=[[str(script), "bench", "1", "2", "3"]],
    )

    assert result.eligible
    assert result.reason is None
    assert result.reference_median_runtime_ms is not None
    assert result.reference_median_runtime_ms > 0.0
    assert result.candidate_median_runtime_ms is not None
    assert result.candidate_median_runtime_ms > 0.0
    assert result.runtime_ratio is not None
    assert result.runtime_ratio > 0.0
    assert result.efficiency_multiplier is not None
    assert 0.75 <= result.efficiency_multiplier <= 1.25

    persisted = read_data(tmp_path / "efficiency_manifest.json")
    assert persisted["eligible"]
    assert persisted["reference_median_runtime_ms"] == pytest.approx(
        result.reference_median_runtime_ms
    )


def test_accepted_canonical_cases_supply_efficiency_commands(tmp_path) -> None:
    script = _write_script(
        tmp_path / "bench.py",
        """
        import sys
        print(" ".join(sys.argv[1:]))
        """,
    )
    tests_dir = tmp_path / "generated_tests"
    tests_dir.mkdir()
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=0,
        cases=[
            ExecutableTestCase(
                test_id="test_runtime_case",
                task_id="demo",
                args=[str(script), "canonical"],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(contains=["canonical"]),
                expected_stderr=ExpectedOutput(exact=""),
                behavior_category="positive",
                source="unit",
            ),
            ExecutableTestCase(
                test_id="test_stdin_case_not_measured",
                task_id="demo",
                args=[str(script), "stdin"],
                stdin="input",
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(contains=["stdin"]),
                expected_stderr=ExpectedOutput(exact=""),
                behavior_category="stdin",
                source="unit",
            ),
        ],
    )
    write_data(tests_dir / "test_cases_iteration_0.json", suite.model_dump(mode="json"))

    result = score_efficiency(
        "demo",
        Path(sys.executable),
        Path(sys.executable),
        1.0,
        tmp_path / "efficiency_manifest.json",
        PBGenConfig(workspace_root=tmp_path, benchmark_trials=1, benchmark_warmups=0),
        accepted_test_cases_path=tests_dir,
    )

    assert result.eligible
    assert result.benchmark_command_count == 1
    assert result.benchmark_command_sources == ["accepted_test_case"]

    persisted = read_data(tmp_path / "efficiency_manifest.json")
    assert persisted["benchmark_command_count"] == 1
    assert persisted["benchmark_command_sources"] == ["accepted_test_case"]


def _write_script(path: Path, body: str) -> Path:
    path.write_text(body.lstrip(), encoding="utf-8")
    return path
