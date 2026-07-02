from __future__ import annotations

from pathlib import Path

from pbgen.quality.redundancy import RedundancyAnalyzer
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput
from pbgen.serialization import write_data


def test_redundancy_clusters_duplicate_command_shapes(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_behavior.py").write_text(
        """
def run_cmd(args):
    return None

def test_one():
    result = run_cmd(["add", "1", "2"])
    assert result.stdout == "3"

def test_two():
    result = run_cmd(["add", "1", "2"])
    assert result.stdout == "3"
""",
        encoding="utf-8",
    )
    report = RedundancyAnalyzer().analyze("demo", tests, tmp_path / "redundancy.json", tmp_path / "events.jsonl")
    assert report.redundancy_score > 0


def test_redundancy_clusters_canonical_cases_by_behavior_shape(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=0,
        cases=[
            _case("test_add_small", ["add", "1", "2"], "3\n"),
            _case("test_add_large", ["add", "10", "20"], "30\n"),
            _case("test_help", ["--help"], "Usage: demo\n", category="help"),
        ],
    )
    write_data(tests / "test_cases_iteration_0.json", suite.model_dump(mode="json"))

    report = RedundancyAnalyzer().analyze(
        "demo",
        tests,
        tmp_path / "redundancy.json",
        tmp_path / "events.jsonl",
    )

    duplicate_items = [item for item in report.items if item.cluster_size == 2]
    assert {item.test_id for item in duplicate_items} == {"test_add_small", "test_add_large"}
    assert report.redundancy_score > 0


def _case(
    test_id: str,
    args: list[str],
    stdout: str,
    *,
    category: str = "calculation",
) -> ExecutableTestCase:
    return ExecutableTestCase(
        test_id=test_id,
        task_id="demo",
        args=args,
        expected_exit_code=0,
        expected_stdout=ExpectedOutput(exact=stdout),
        expected_stderr=ExpectedOutput(exact=""),
        behavior_category=category,
        source="unit",
    )
