from __future__ import annotations

from pathlib import Path

import pytest

from pbgen.eval.executable_runner import (
    load_canonical_suites,
    run_canonical_suites_from_path,
    run_executable_test_cases,
)
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput
from pbgen.serialization import write_data


def test_run_executable_test_cases_records_pass_and_failure(tmp_path: Path) -> None:
    executable = _write_cli(tmp_path / "tool")
    cases = [
        ExecutableTestCase(
            test_id="test_add",
            task_id="demo",
            args=["add", "2", "3"],
            expected_exit_code=0,
            expected_stdout=ExpectedOutput(exact="5\n"),
            expected_stderr=ExpectedOutput(exact=""),
            source="unit",
        ),
        ExecutableTestCase(
            test_id="test_wrong",
            task_id="demo",
            args=["add", "2", "3"],
            expected_exit_code=0,
            expected_stdout=ExpectedOutput(exact="999\n"),
            expected_stderr=ExpectedOutput(exact=""),
            source="unit",
        ),
    ]

    result = run_executable_test_cases("demo", cases, executable)

    assert result.total_tests == 2
    assert result.passed_tests == 1
    assert result.failed_tests == 1
    assert result.pass_rate == pytest.approx(0.5)
    by_id = {outcome.test_id: outcome for outcome in result.outcomes}
    assert by_id["test_add"].outcome == "passed"
    assert by_id["test_wrong"].outcome == "failed"
    assert by_id["test_wrong"].failure_message == "stdout exact mismatch"


def test_run_executable_test_cases_supports_stdin_env_and_fixtures(tmp_path: Path) -> None:
    executable = _write_cli(tmp_path / "tool")
    case = ExecutableTestCase(
        test_id="test_context",
        task_id="demo",
        args=["context", "input.txt"],
        stdin="hello stdin\n",
        env={"PBGEN_SAMPLE": "sample-env"},
        fixture_files={"input.txt": "fixture-data"},
        expected_exit_code=0,
        expected_stdout=ExpectedOutput(
            contains=["stdin=hello stdin", "env=sample-env", "file=fixture-data"],
        ),
        source="unit",
    )

    result = run_executable_test_cases("demo", [case], executable)

    assert result.pass_rate == 1.0
    assert result.outcomes[0].outcome == "passed"


def test_run_executable_test_cases_rejects_unsafe_fixture_paths(tmp_path: Path) -> None:
    executable = _write_cli(tmp_path / "tool")
    case = ExecutableTestCase(
        test_id="test_bad_fixture",
        task_id="demo",
        fixture_files={"../escape.txt": "bad"},
        expected_exit_code=0,
        source="unit",
    )

    result = run_executable_test_cases("demo", [case], executable)

    assert result.failed_tests == 1
    assert result.outcomes[0].outcome == "error"
    assert result.outcomes[0].failure_message == "unsafe fixture path: ../escape.txt"


def test_run_executable_test_cases_handles_timeout(tmp_path: Path) -> None:
    executable = _write_cli(tmp_path / "tool")
    case = ExecutableTestCase(
        test_id="test_timeout",
        task_id="demo",
        args=["sleep"],
        expected_exit_code=0,
        timeout_seconds=1,
        source="unit",
    )

    result = run_executable_test_cases("demo", [case], executable)

    assert result.failed_tests == 1
    assert "timed out" in (result.outcomes[0].failure_message or "")


def test_load_and_run_canonical_suites_from_path(tmp_path: Path) -> None:
    executable = _write_cli(tmp_path / "tool")
    tests_dir = tmp_path / "generated_tests"
    tests_dir.mkdir()
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=0,
        cases=[
            ExecutableTestCase(
                test_id="test_help",
                task_id="demo",
                args=["--help"],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(contains=["Usage"]),
                source="unit",
            )
        ],
    )
    write_data(tests_dir / "test_cases_iteration_0.json", suite.model_dump(mode="json"))
    write_data(tests_dir / "test_cases_iteration_0_artifact.json", {"ignored": True})

    loaded = load_canonical_suites(tests_dir)
    result = run_canonical_suites_from_path("demo", tests_dir, executable)

    assert len(loaded) == 1
    assert result is not None
    assert result.pass_rate == 1.0


def test_run_generated_suite_falls_back_to_pytest_without_canonical_cases(tmp_path: Path) -> None:
    executable = _write_cli(tmp_path / "program")
    test_file = tmp_path / "test_behavior.py"
    test_file.write_text(
        "import os\n\n"
        "def test_env():\n"
        "    assert os.environ['PBGEN_EXECUTABLE'].endswith('program')\n",
        encoding="utf-8",
    )

    result = run_generated_suite("demo", test_file, executable)

    assert result.total_tests == 1
    assert result.pass_rate == 1.0


def _write_cli(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys
import time


def main(argv: list[str]) -> int:
    if argv == ["--help"]:
        print("Usage: tool COMMAND")
        return 0
    if argv[:1] == ["add"]:
        print(sum(int(value) for value in argv[1:]))
        return 0
    if argv[:1] == ["context"]:
        stdin = sys.stdin.read().strip()
        file_text = Path(argv[1]).read_text(encoding="utf-8")
        print(f"stdin={stdin}")
        print(f"env={os.environ.get('PBGEN_SAMPLE', '')}")
        print(f"file={file_text}")
        return 0
    if argv[:1] == ["sleep"]:
        time.sleep(5)
        return 0
    print("unknown", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
