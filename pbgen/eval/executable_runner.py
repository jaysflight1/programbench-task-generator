"""Direct execution runner for canonical executable test cases."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from pbgen.errors import PBGenError
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput, PerTestOutcome, TestRunResult
from pbgen.serialization import read_data
from pbgen.subprocess_utils import CommandResult, CommandRunner, run_command


_SNIPPET_LIMIT = 4000


def run_executable_test_suite(
    task_id: str,
    suite: ExecutableTestSuite,
    executable_path: Path,
    *,
    command_runner: CommandRunner | None = None,
    work_root: Path | None = None,
) -> TestRunResult:
    """Execute a canonical test suite directly against an executable."""

    return run_executable_test_cases(
        task_id,
        suite.cases,
        executable_path,
        command_runner=command_runner,
        work_root=work_root,
    )


def run_executable_test_cases(
    task_id: str,
    cases: Iterable[ExecutableTestCase],
    executable_path: Path,
    *,
    command_runner: CommandRunner | None = None,
    work_root: Path | None = None,
) -> TestRunResult:
    """Execute canonical test cases and return structured outcomes."""

    outcomes = [
        _run_case(
            case,
            executable_path,
            command_runner=command_runner,
            work_root=work_root,
        )
        for case in cases
    ]
    passed = sum(1 for outcome in outcomes if outcome.outcome == "passed")
    failed = sum(1 for outcome in outcomes if outcome.outcome in {"failed", "error"})
    stdout = "\n".join(outcome.stdout for outcome in outcomes if outcome.stdout)
    stderr = "\n".join(outcome.stderr for outcome in outcomes if outcome.stderr)
    return TestRunResult(
        task_id=task_id,
        total_tests=len(outcomes),
        passed_tests=passed,
        failed_tests=failed,
        exit_status=0 if failed == 0 else 1,
        stdout=_snippet(stdout),
        stderr=_snippet(stderr),
        outcomes=outcomes,
    )


def run_canonical_suites_from_path(
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    *,
    command_runner: CommandRunner | None = None,
    work_root: Path | None = None,
) -> TestRunResult | None:
    """Run canonical suites found under a path, or return None when absent."""

    suites = load_canonical_suites(tests_path)
    if not suites:
        return None
    cases = [case for suite in suites for case in suite.cases]
    return run_executable_test_cases(
        task_id,
        cases,
        executable_path,
        command_runner=command_runner,
        work_root=work_root,
    )


def load_canonical_suites(tests_path: Path) -> list[ExecutableTestSuite]:
    """Load generated `test_cases_iteration_*.json` suites in stable order."""

    roots = [tests_path] if tests_path.is_dir() else [tests_path.parent]
    suites: list[ExecutableTestSuite] = []
    for root in roots:
        for path in sorted(root.glob("test_cases_iteration*.json")):
            if path.name.endswith("_artifact.json"):
                continue
            suites.append(ExecutableTestSuite.model_validate(read_data(path)))
    return suites


def _run_case(
    case: ExecutableTestCase,
    executable_path: Path,
    *,
    command_runner: CommandRunner | None,
    work_root: Path | None,
) -> PerTestOutcome:
    started = time.monotonic()
    if work_root is not None:
        work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="pbgen-case-",
        dir=str(work_root) if work_root is not None else None,
    ) as temp_dir:
        cwd = Path(temp_dir)
        fixture_error = _write_fixtures(cwd, case.fixture_files)
        if fixture_error is not None:
            return _outcome(
                case,
                executable_path,
                started,
                "error",
                failure_message=fixture_error,
            )
        try:
            result = _execute_case_command(
                [str(executable_path), *case.args],
                cwd=cwd,
                env=case.env,
                stdin=case.stdin,
                timeout_seconds=case.timeout_seconds,
                command_runner=command_runner,
            )
        except subprocess.TimeoutExpired as exc:
            return _outcome(
                case,
                executable_path,
                started,
                "failed",
                stdout=_timeout_text(exc.stdout),
                stderr=_timeout_text(exc.stderr),
                failure_message=f"timed out after {case.timeout_seconds} seconds",
            )
        except OSError as exc:
            return _outcome(
                case,
                executable_path,
                started,
                "error",
                failure_message=str(exc),
            )
        except PBGenError as exc:
            return _outcome(
                case,
                executable_path,
                started,
                "error",
                failure_message=str(exc),
            )

    failure = _case_failure(case, result.returncode, result.stdout, result.stderr)
    return _outcome(
        case,
        executable_path,
        started,
        "passed" if failure is None else "failed",
        stdout=result.stdout,
        stderr=result.stderr,
        failure_message=failure,
    )


def _execute_case_command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: str,
    timeout_seconds: int,
    command_runner: CommandRunner | None,
) -> CommandResult:
    if command_runner is not None:
        return command_runner.run(
            args,
            cwd=cwd,
            env=env,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
        )

    local_env = os.environ.copy()
    local_env.update(env)
    return run_command(
        args,
        cwd=cwd,
        env=local_env,
        stdin=stdin,
        timeout_seconds=timeout_seconds,
    )


def _case_failure(
    case: ExecutableTestCase,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> str | None:
    if exit_code != case.expected_exit_code:
        return f"expected exit code {case.expected_exit_code}, got {exit_code}"
    stdout_failure = _output_failure("stdout", case.expected_stdout, stdout)
    if stdout_failure is not None:
        return stdout_failure
    return _output_failure("stderr", case.expected_stderr, stderr)


def _output_failure(stream: str, expected: ExpectedOutput, actual: str) -> str | None:
    if expected.exact is not None and actual != expected.exact:
        return f"{stream} exact mismatch"
    for value in expected.contains:
        if value not in actual:
            return f"{stream} missing expected substring {value!r}"
    for pattern in expected.regex:
        if re.search(pattern, actual) is None:
            return f"{stream} missing regex match {pattern!r}"
    return None


def _write_fixtures(cwd: Path, fixture_files: dict[str, str]) -> str | None:
    for relative, content in fixture_files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            return f"unsafe fixture path: {relative}"
        target = cwd / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return None


def _outcome(
    case: ExecutableTestCase,
    executable_path: Path,
    started: float,
    outcome: str,
    *,
    stdout: str = "",
    stderr: str = "",
    failure_message: str | None = None,
) -> PerTestOutcome:
    return PerTestOutcome(
        test_id=case.test_id,
        nodeid=case.test_id,
        file_path=None,
        outcome=outcome,
        duration_ms=(time.monotonic() - started) * 1000,
        stdout=_snippet(stdout),
        stderr=_snippet(stderr),
        failure_message=failure_message,
        executable_path=executable_path,
    )


def _snippet(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]"


def _timeout_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
