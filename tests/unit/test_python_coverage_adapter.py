import json
import os
import stat

import pytest

from pbgen.coverage.adapters import (
    PythonCoverageAdapter,
    coverage_report_from_json,
    create_coverage_wrapper,
)


def test_coverage_json_maps_missing_ranges_to_functions(tmp_path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    source_file = source_root / "tool.py"
    source_file.write_text(
        """def used(value):
    if value:
        return "yes"
    return "no"

def skipped(value):
    doubled = value * 2
    if doubled > 10:
        return "large"
    return "small"

class Worker:
    def idle(self):
        value = "idle"
        return value
""",
        encoding="utf-8",
    )
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(
        json.dumps(
            {
                "totals": {
                    "covered_lines": 5,
                    "num_statements": 11,
                    "percent_covered": 45.45,
                },
                "files": {
                    str(source_file): {
                        "executed_lines": [1, 2, 3, 6, 12, 13],
                        "missing_lines": [7, 8, 9, 10, 14, 15],
                        "summary": {
                            "covered_lines": 5,
                            "num_statements": 11,
                            "percent_covered": 45.45,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = coverage_report_from_json(
        "demo",
        2,
        coverage_json,
        source_roots=[source_root],
    )

    assert report.task_id == "demo"
    assert report.iteration == 2
    assert report.line_coverage == pytest.approx(5 / 11)
    assert report.function_coverage == pytest.approx(1 / 3)
    assert report.uncovered_files == ["tool.py"]
    assert report.uncovered_functions == ["tool.py:Worker.idle", "tool.py:skipped"]
    assert report.uncovered_line_ranges == [
        {
            "file_path": "tool.py",
            "start_line": 7,
            "end_line": 10,
            "function_name": "skipped",
            "missing_lines": 4,
        },
        {
            "file_path": "tool.py",
            "start_line": 14,
            "end_line": 15,
            "function_name": "Worker.idle",
            "missing_lines": 2,
        },
    ]
    assert [gap.function_name for gap in report.gaps] == ["skipped", "Worker.idle"]
    assert report.gaps[0].priority > report.gaps[1].priority


def test_create_coverage_wrapper_is_executable_and_uses_parallel_mode(tmp_path) -> None:
    executable = tmp_path / "program.py"
    executable.write_text("print('ok')\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    wrapper = create_coverage_wrapper(
        executable,
        tmp_path / "coverage",
        python_executable="/usr/bin/python3",
    )

    assert os.access(wrapper.wrapper_path, os.X_OK)
    wrapper_source = wrapper.wrapper_path.read_text(encoding="utf-8")
    assert "--parallel-mode" in wrapper_source
    assert str(executable.resolve()) in wrapper_source
    assert str(wrapper.data_file) in wrapper_source


def test_python_coverage_adapter_runs_pytest_with_wrapper_when_coverage_is_available(tmp_path) -> None:
    pytest.importorskip("coverage")
    source_root = tmp_path / "src"
    tests_path = tmp_path / "tests"
    work_dir = tmp_path / "work"
    source_root.mkdir()
    tests_path.mkdir()

    executable = source_root / "program.py"
    executable.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys


def ping() -> None:
    print("pong")


def unused() -> None:
    print("unused")


def main() -> int:
    if sys.argv[1:] == ["ping"]:
        ping()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    (tests_path / "test_behavior.py").write_text(
        """from __future__ import annotations

import os
import subprocess


def test_ping() -> None:
    result = subprocess.run(
        [os.environ["PBGEN_EXECUTABLE"], "ping"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == "pong\\n"
""",
        encoding="utf-8",
    )

    report = PythonCoverageAdapter(
        task_id="demo",
        source_roots=[source_root],
        work_dir=work_dir,
    ).run_tests_with_coverage(tests_path, executable)

    assert report.line_coverage is not None
    assert report.line_coverage < 1.0
    assert "program.py" in report.uncovered_files
    assert any(gap.function_name == "unused" for gap in report.gaps)
