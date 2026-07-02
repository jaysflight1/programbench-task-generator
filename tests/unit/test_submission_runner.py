import json
from pathlib import Path

import pytest

from pbgen.eval.submission_runner import (
    _parse_pytest_counts,
    _parse_pytest_outcomes,
    run_pytest_suite,
)


def test_parse_pytest_json_report_outcomes(tmp_path) -> None:
    outcome_file = tmp_path / "outcomes.json"
    executable = Path("/tmp/program")
    outcome_file.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "nodeid": "tests/test_cli.py::test_success",
                        "outcome": "passed",
                        "duration": 0.012,
                        "stdout": "ok\n",
                    },
                    {
                        "nodeid": "tests/test_cli.py::TestCLI::test_failure[empty]",
                        "setup": {"outcome": "passed", "duration": 0.001},
                        "call": {
                            "outcome": "failed",
                            "duration": 0.002,
                            "stdout": "call out\n",
                            "stderr": "call err\n",
                            "crash": {"message": "AssertionError: bad result"},
                        },
                        "teardown": {"outcome": "passed", "duration": 0.001},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    outcomes = _parse_pytest_outcomes(outcome_file, executable)

    assert outcomes is not None
    assert len(outcomes) == 2
    assert outcomes[0].nodeid == "tests/test_cli.py::test_success"
    assert outcomes[0].test_id == "test_success"
    assert outcomes[0].file_path == Path("tests/test_cli.py")
    assert outcomes[0].outcome == "passed"
    assert outcomes[0].duration_ms == pytest.approx(12.0)
    assert outcomes[0].stdout == "ok\n"
    assert outcomes[0].executable_path == executable

    assert outcomes[1].test_id == "test_failure[empty]"
    assert outcomes[1].outcome == "failed"
    assert outcomes[1].duration_ms == pytest.approx(4.0)
    assert outcomes[1].stdout == "call out\n"
    assert outcomes[1].stderr == "call err\n"
    assert outcomes[1].failure_message == "AssertionError: bad result"
    assert outcomes[1].executable_path == executable


def test_parse_pytest_counts_includes_errors_for_fallback() -> None:
    assert _parse_pytest_counts("2 failed, 3 passed, 1 error in 0.10s") == (6, 3, 3)


def test_run_pytest_suite_records_structured_outcomes(tmp_path) -> None:
    test_file = tmp_path / "test_behavior.py"
    test_file.write_text(
        "\n".join(
            [
                "import os",
                "",
                "def test_pass():",
                "    print('pass output')",
                "    assert os.environ['PBGEN_EXECUTABLE'].endswith('program')",
                "",
                "def test_fail():",
                "    print('fail output')",
                "    raise AssertionError('boom')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "program"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    result = run_pytest_suite("demo", test_file, executable)

    assert result.total_tests == 2
    assert result.passed_tests == 1
    assert result.failed_tests == 1
    assert result.pass_rate == pytest.approx(0.5)
    assert result.exit_status == 1
    assert len(result.outcomes) == 2

    by_id = {outcome.test_id: outcome for outcome in result.outcomes}
    assert by_id["test_pass"].outcome == "passed"
    assert by_id["test_pass"].executable_path == executable
    assert by_id["test_fail"].outcome == "failed"
    assert "fail output" in by_id["test_fail"].stdout
    assert "boom" in (by_id["test_fail"].failure_message or "")
    assert by_id["test_fail"].executable_path == executable
