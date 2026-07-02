"""Run generated pytest suites against a supplied executable."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from pbgen.errors import PBGenError
from pbgen.eval.executable_runner import run_canonical_suites_from_path
from pbgen.schemas import PerTestOutcome, TestRunResult
from pbgen.subprocess_utils import CommandRunner, run_command


_SNIPPET_LIMIT = 4000
_PBGEN_PYTEST_OUTCOME_MODULE = "pbgen_pytest_outcomes"
_PBGEN_PYTEST_OUTCOME_ENV = "PBGEN_PYTEST_OUTCOME_PATH"
_PYTEST_OUTCOME_PLUGIN = r'''
"""Temporary pytest plugin used by ProgramBench to emit per-test outcomes."""

from __future__ import annotations

import json
import os


_OUTCOMES = {}


def _entry_for(report):
    return _OUTCOMES.setdefault(
        report.nodeid,
        {
            "nodeid": report.nodeid,
            "outcome": None,
            "duration": 0.0,
            "stdout": "",
            "stderr": "",
            "failure_message": None,
        },
    )


def pytest_runtest_logreport(report):
    if report.when not in {"setup", "call", "teardown"}:
        return

    entry = _entry_for(report)
    entry["duration"] += float(getattr(report, "duration", 0.0) or 0.0)

    stdout = getattr(report, "capstdout", "") or ""
    stderr = getattr(report, "capstderr", "") or ""
    if stdout:
        entry["stdout"] += stdout
    if stderr:
        entry["stderr"] += stderr

    if report.when == "call":
        entry["outcome"] = report.outcome
    elif report.failed:
        entry["outcome"] = "error"
    elif report.skipped and entry["outcome"] is None:
        entry["outcome"] = "skipped"

    if report.failed:
        entry["failure_message"] = getattr(report, "longreprtext", None) or str(report.longrepr)


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("PBGEN_PYTEST_OUTCOME_PATH")
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"exitstatus": exitstatus, "tests": list(_OUTCOMES.values())}, handle)
'''


def run_pytest_suite(task_id: str, tests_path: Path, executable_path: Path) -> TestRunResult:
    """Execute pytest with `PBGEN_EXECUTABLE` pointing at the target executable."""

    env = os.environ.copy()
    env["PBGEN_EXECUTABLE"] = str(executable_path)
    with tempfile.TemporaryDirectory(prefix="pbgen-pytest-") as temp_dir:
        outcome_path = Path(temp_dir) / "outcomes.json"
        structured_args = _pytest_structured_args(Path(temp_dir), outcome_path, env)
        result = run_command(
            [sys.executable, "-m", "pytest", "-q", *structured_args, str(tests_path)],
            env=env,
            timeout_seconds=120,
        )
        outcomes = _parse_pytest_outcomes(outcome_path, executable_path)

    if outcomes is None:
        total, passed, failed = _parse_pytest_counts(result.stdout + "\n" + result.stderr)
        outcomes = []
    else:
        total, passed, failed = _count_outcomes(outcomes)

    return TestRunResult(
        task_id=task_id,
        total_tests=total,
        passed_tests=passed,
        failed_tests=failed,
        exit_status=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        outcomes=outcomes,
    )


def run_generated_suite(
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    *,
    command_runner: CommandRunner | None = None,
    work_root: Path | None = None,
) -> TestRunResult:
    """Run canonical executable cases when available, otherwise run pytest files."""

    canonical_result = run_canonical_suites_from_path(
        task_id,
        tests_path,
        executable_path,
        command_runner=command_runner,
        work_root=work_root,
    )
    if canonical_result is not None:
        return canonical_result
    if command_runner is not None:
        raise PBGenError(
            "Sandboxed candidate evaluation requires canonical hidden tests; "
            "pytest fallback would execute candidate code on the host."
        )
    return run_pytest_suite(task_id, tests_path, executable_path)


def _pytest_structured_args(temp_dir: Path, outcome_path: Path, env: dict[str, str]) -> list[str]:
    env[_PBGEN_PYTEST_OUTCOME_ENV] = str(outcome_path)
    if importlib.util.find_spec("pytest_jsonreport") is not None:
        return ["--json-report", "--json-report-file", str(outcome_path)]

    plugin_path = temp_dir / f"{_PBGEN_PYTEST_OUTCOME_MODULE}.py"
    plugin_path.write_text(_PYTEST_OUTCOME_PLUGIN, encoding="utf-8")
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(temp_dir) if not pythonpath else f"{temp_dir}{os.pathsep}{pythonpath}"
    )
    return ["-p", _PBGEN_PYTEST_OUTCOME_MODULE]


def _parse_pytest_outcomes(
    outcome_path: Path,
    executable_path: Path,
) -> list[PerTestOutcome] | None:
    if not outcome_path.exists():
        return None
    try:
        data = json.loads(outcome_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    raw_tests = data.get("tests") if isinstance(data, dict) else None
    if not isinstance(raw_tests, list):
        return None

    outcomes: list[PerTestOutcome] = []
    for item in raw_tests:
        if not isinstance(item, dict):
            continue
        outcome = _parse_pytest_outcome_item(item, executable_path)
        if outcome is not None:
            outcomes.append(outcome)
    if raw_tests and not outcomes:
        return None
    return outcomes


def _parse_pytest_outcome_item(
    item: dict[str, Any],
    executable_path: Path,
) -> PerTestOutcome | None:
    nodeid = item.get("nodeid")
    if not isinstance(nodeid, str) or not nodeid:
        return None

    outcome = _extract_outcome(item)
    if outcome is None:
        return None

    return PerTestOutcome(
        test_id=_test_id_from_nodeid(nodeid),
        nodeid=nodeid,
        file_path=_file_path_from_item(item, nodeid),
        outcome=outcome,
        duration_ms=_duration_ms_from_item(item),
        stdout=_snippet(_captured_text(item, "stdout")),
        stderr=_snippet(_captured_text(item, "stderr")),
        failure_message=_failure_message_from_item(item),
        executable_path=executable_path,
    )


def _extract_outcome(item: dict[str, Any]) -> str | None:
    explicit = item.get("outcome")
    if isinstance(explicit, str) and explicit:
        return explicit.lower()

    setup = _phase(item, "setup")
    call = _phase(item, "call")
    teardown = _phase(item, "teardown")

    if _phase_outcome(teardown) == "failed":
        return "error"
    if _phase_outcome(setup) == "failed":
        return "error"
    if call is not None:
        return _phase_outcome(call)
    if setup is not None and _phase_outcome(setup) == "skipped":
        return "skipped"
    return None


def _phase(item: dict[str, Any], name: str) -> dict[str, Any] | None:
    phase = item.get(name)
    return phase if isinstance(phase, dict) else None


def _phase_outcome(phase: dict[str, Any] | None) -> str | None:
    if phase is None:
        return None
    outcome = phase.get("outcome")
    return outcome.lower() if isinstance(outcome, str) and outcome else None


def _duration_ms_from_item(item: dict[str, Any]) -> float | None:
    explicit_ms = item.get("duration_ms")
    if isinstance(explicit_ms, int | float):
        return float(explicit_ms)

    explicit_seconds = item.get("duration")
    if isinstance(explicit_seconds, int | float):
        return float(explicit_seconds) * 1000

    total_seconds = 0.0
    found_duration = False
    for phase_name in ("setup", "call", "teardown"):
        phase = _phase(item, phase_name)
        duration = phase.get("duration") if phase else None
        if isinstance(duration, int | float):
            total_seconds += float(duration)
            found_duration = True
    return total_seconds * 1000 if found_duration else None


def _captured_text(item: dict[str, Any], stream: str) -> str:
    value = item.get(stream)
    parts = [value] if isinstance(value, str) and value else []
    for phase_name in ("setup", "call", "teardown"):
        phase = _phase(item, phase_name)
        phase_value = phase.get(stream) if phase else None
        if isinstance(phase_value, str) and phase_value:
            parts.append(phase_value)
    return "".join(parts)


def _failure_message_from_item(item: dict[str, Any]) -> str | None:
    explicit = item.get("failure_message")
    if isinstance(explicit, str) and explicit:
        return _snippet(explicit)

    for phase_name in ("setup", "call", "teardown"):
        phase = _phase(item, phase_name)
        if not phase:
            continue
        crash = phase.get("crash")
        if isinstance(crash, dict):
            message = crash.get("message")
            if isinstance(message, str) and message:
                return _snippet(message)
        longrepr = phase.get("longrepr")
        if isinstance(longrepr, str) and longrepr:
            return _snippet(longrepr)
        traceback = phase.get("traceback")
        if isinstance(traceback, list) and traceback:
            return _snippet("\n".join(str(line) for line in traceback))
    return None


def _test_id_from_nodeid(nodeid: str) -> str:
    if "::" in nodeid:
        return nodeid.rsplit("::", 1)[-1]
    return Path(nodeid).stem


def _file_path_from_item(item: dict[str, Any], nodeid: str) -> Path | None:
    candidate = item.get("file_path") or item.get("path")
    if not isinstance(candidate, str) or not candidate:
        candidate = nodeid.split("::", 1)[0]
    return Path(candidate) if candidate else None


def _snippet(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]"


def _count_outcomes(outcomes: list[PerTestOutcome]) -> tuple[int, int, int]:
    passed = sum(1 for outcome in outcomes if outcome.outcome == "passed")
    failed = sum(1 for outcome in outcomes if outcome.outcome in {"failed", "error"})
    return len(outcomes), passed, failed


def _parse_pytest_counts(output: str) -> tuple[int, int, int]:
    passed = 0
    failed = 0
    match = re.search(r"(\d+) passed", output)
    if match:
        passed = int(match.group(1))
    match = re.search(r"(\d+) failed", output)
    if match:
        failed = int(match.group(1))
    match = re.search(r"(\d+) errors?", output)
    if match:
        failed += int(match.group(1))
    if not passed and not failed:
        single = re.search(r"^(\.+|F+)", output.strip())
        if single:
            passed = single.group(1).count(".")
            failed = single.group(1).count("F")
    return passed + failed, passed, failed
