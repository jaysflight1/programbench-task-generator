from __future__ import annotations

from pathlib import Path

from pbgen.quality.mutation_runner import MutationLiteRunner
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput
from pbgen.serialization import read_data, write_data


def test_mutation_lite_runner_flags_only_weak_survivors(tmp_path: Path) -> None:
    tests_dir = tmp_path / "generated_tests"
    tests_dir.mkdir()
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=0,
        cases=[
            ExecutableTestCase(
                test_id="test_strong",
                task_id="demo",
                args=["add", "2", "3"],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(exact="5\n"),
                expected_stderr=ExpectedOutput(exact=""),
                source="unit",
            ),
            ExecutableTestCase(
                test_id="test_weak_exit_only",
                task_id="demo",
                args=["noop"],
                expected_exit_code=0,
                source="unit",
            ),
        ],
    )
    write_data(tests_dir / "test_cases_iteration_0.json", suite.model_dump(mode="json"))

    report = MutationLiteRunner().run(
        "demo",
        tests_dir,
        tmp_path / "mutants",
        tmp_path / "reports" / "mutation_lite_report.json",
        tmp_path / "events.jsonl",
    )

    assert report.mutation_count == 5
    assert report.per_test_mutation_survived == {
        "test_strong": False,
        "test_weak_exit_only": True,
    }
    assert report.mutation_survival_rate == 0.5
    persisted = read_data(tmp_path / "reports" / "mutation_lite_report.json")
    assert persisted["mutation_survival_rate"] == 0.5
