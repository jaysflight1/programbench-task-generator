from __future__ import annotations

from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.eval.executable_runner import load_canonical_suites
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.quality.hard_gates import apply_hard_quality_gates
from pbgen.schemas import (
    AssertionLintFlag,
    AssertionLintReport,
    ExecutableTestCase,
    ExecutableTestSuite,
    ExpectedOutput,
    LintSeverity,
)
from pbgen.testgen.test_writer import write_executable_test_suite


def test_hard_gates_filter_canonical_suite_and_rendered_pytest(tmp_path: Path) -> None:
    tests_dir = tmp_path / "generated_tests"
    executable = _write_cli(tmp_path / "program")
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=0,
        cases=[
            _case("test_strong", ["strong", "case"], "OK: strong case\n"),
            _case("test_dummy", ["noop"], ""),
            _case("test_bad_gold", ["bad"], "expected\n"),
            _case("test_high_lint", ["lint"], "linted\n"),
        ],
    )
    rendered_path = tests_dir / "test_behavior_iter_0.py"
    write_executable_test_suite(tests_dir, suite, rendered_path=rendered_path)
    lint_report = AssertionLintReport(
        task_id="demo",
        flags=[
            AssertionLintFlag(
                rule_id="assert_true",
                severity=LintSeverity.HIGH,
                message="assert true",
                file_path=rendered_path,
                line=1,
                test_name="test_high_lint",
            )
        ],
    )

    result = apply_hard_quality_gates(
        task_id="demo",
        tests_path=tests_dir,
        executable_path=executable,
        lint_report=lint_report,
        dummy_work_dir=tmp_path / "dummies",
        report_path=tmp_path / "reports" / "hard_gate_report_iteration_0.json",
        event_log_path=tmp_path / "events.jsonl",
        config=PBGenConfig(workspace_root=tmp_path, determinism_runs=2),
        iteration=0,
    )

    assert result.report.accepted_test_count == 1
    assert result.report.rejected_test_count == 3
    assert result.report.canonical_filter_applied is True
    rejected = {item.test_id: item.reasons for item in result.report.rejected_tests}
    assert rejected["test_dummy"] == ["passed at least one dummy executable"]
    assert rejected["test_bad_gold"] == ["gold determinism failed"]
    assert rejected["test_high_lint"] == ["high assertion lint: assert_true"]

    filtered_suite = load_canonical_suites(tests_dir)[0]
    assert [case.test_id for case in filtered_suite.cases] == ["test_strong"]
    rendered = rendered_path.read_text(encoding="utf-8")
    assert "def test_strong" in rendered
    assert "def test_dummy" not in rendered
    assert "def test_bad_gold" not in rendered
    assert "def test_high_lint" not in rendered

    accepted_result = run_generated_suite("demo", tests_dir, executable)
    assert accepted_result.total_tests == 1
    assert accepted_result.pass_rate == 1.0


def _case(test_id: str, args: list[str], stdout: str) -> ExecutableTestCase:
    return ExecutableTestCase(
        test_id=test_id,
        task_id="demo",
        args=args,
        expected_exit_code=0,
        expected_stdout=ExpectedOutput(exact=stdout),
        expected_stderr=ExpectedOutput(exact=""),
        source="unit",
    )


def _write_cli(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if argv == ["strong", "case"]:
        print("OK: strong case")
        return 0
    if argv == ["noop"]:
        return 0
    if argv == ["bad"]:
        print("actual")
        return 0
    if argv == ["lint"]:
        print("linted")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
