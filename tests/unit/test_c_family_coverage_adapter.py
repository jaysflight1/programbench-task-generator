from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from pbgen.config import PBGenConfig
from pbgen.coverage.adapters import (
    CFamilyCoverageAdapter,
    _custom_cmake_definitions,
    _custom_cmake_target,
    _gcov_object_targets,
    _select_native_executable,
)
from pbgen.coverage.coverage_runner import run_c_family_coverage
from pbgen.repo_discovery.checkout import init_task
from pbgen.schemas import BuildCandidate, ExecutableTestCase, ExecutableTestSuite, ExpectedOutput, TaskSpec
from pbgen.serialization import read_data, write_data


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_c_coverage_reports_unavailable_when_gcov_missing(tmp_path: Path) -> None:
    if shutil.which("make") is None or shutil.which("cc") is None:
        pytest.skip("make and cc are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-c", config=config, local_path=FIXTURES / "make_c")
    paths = tmp_path / "artifacts" / "make-c"
    spec = TaskSpec.model_validate(read_data(paths / "task_spec.yaml"))
    tests_path = paths / "generated_tests"
    _write_canonical_cases(tests_path)
    adapter = CFamilyCoverageAdapter(
        spec,
        iteration=0,
        work_dir=paths / "reports" / "coverage_iteration_0",
        gcov_executable="pbgen-missing-gcov",
    )

    executable = adapter.instrument_build(paths / "repo")
    report = adapter.run_tests_with_coverage(tests_path, executable)

    assert report.coverage_available is False
    assert report.coverage_backend == "c-family-gcov"
    assert "pbgen-missing-gcov is not available" == report.coverage_unavailable_reason


def test_c_coverage_collects_gcov_report_when_tools_are_available(tmp_path: Path) -> None:
    if shutil.which("make") is None or shutil.which("cc") is None or shutil.which("gcov") is None:
        pytest.skip("make, cc, and gcov are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-c", config=config, local_path=FIXTURES / "make_c")
    paths = tmp_path / "artifacts" / "make-c"
    spec = TaskSpec.model_validate(read_data(paths / "task_spec.yaml"))
    tests_path = paths / "generated_tests"
    _write_canonical_cases(tests_path)

    report = run_c_family_coverage(
        spec,
        paths / "repo",
        tests_path,
        iteration=0,
        work_dir=paths / "reports" / "coverage_iteration_0",
        config=config,
    )

    assert report.coverage_available is True
    assert report.coverage_backend == "c-family-gcov"
    assert report.line_coverage is not None
    assert 0.0 <= report.line_coverage <= 1.0


def test_cmake_coverage_collects_gcov_report_when_tools_are_available(tmp_path: Path) -> None:
    if shutil.which("cmake") is None or shutil.which("cc") is None or shutil.which("gcov") is None:
        pytest.skip("cmake, cc, and gcov are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="cmake-c", config=config, local_path=FIXTURES / "cmake_c")
    paths = tmp_path / "artifacts" / "cmake-c"
    spec = TaskSpec.model_validate(read_data(paths / "task_spec.yaml"))
    tests_path = paths / "generated_tests"
    _write_canonical_cases(tests_path, task_id="cmake-c", executable_name="cmake_calc")

    report = run_c_family_coverage(
        spec,
        paths / "repo",
        tests_path,
        iteration=0,
        work_dir=paths / "reports" / "coverage_iteration_0",
        config=config,
    )

    assert report.coverage_available is True
    assert report.coverage_backend == "c-family-gcov"
    assert report.line_coverage is not None
    assert 0.0 <= report.line_coverage <= 1.0


def test_custom_command_cmake_coverage_does_not_fall_back_to_make(tmp_path: Path) -> None:
    if shutil.which("cmake") is None or shutil.which("cc") is None or shutil.which("gcov") is None:
        pytest.skip("cmake, cc, and gcov are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="custom-cmake", config=config, local_path=FIXTURES / "cmake_c")
    paths = tmp_path / "artifacts" / "custom-cmake"
    discovered = TaskSpec.model_validate(read_data(paths / "task_spec.yaml"))
    spec = discovered.model_copy(
        update={
            "build_system": "custom-command",
            "build_candidates": [
                BuildCandidate(
                    build_system="custom-command",
                    language="c",
                    confidence=1.0,
                    commands=[
                        [
                            "bash",
                            "-lc",
                            "cmake -S . -B build -DPBGEN_SAMPLE=ON "
                            "&& cmake --build build --target cmake_calc",
                        ]
                    ],
                    output_hints=["build/cmake_calc"],
                )
            ],
        }
    )
    tests_path = paths / "generated_tests"
    _write_canonical_cases(tests_path, task_id="custom-cmake", executable_name="cmake_calc")

    report = run_c_family_coverage(
        spec,
        paths / "repo",
        tests_path,
        iteration=0,
        work_dir=paths / "reports" / "coverage_iteration_0",
        config=config,
    )

    assert report.coverage_available is True
    assert report.line_coverage is not None
    assert _custom_cmake_definitions(spec) == ["-DPBGEN_SAMPLE=ON"]
    assert _custom_cmake_target(spec) == "cmake_calc"


def test_native_executable_selection_prefers_primary_binary_hint(tmp_path: Path) -> None:
    helper = tmp_path / "helper"
    preferred = tmp_path / "nested" / "md2html"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    preferred.parent.mkdir()
    preferred.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    preferred.chmod(0o755)

    selected = _select_native_executable(tmp_path, preferred_names=["build/nested/md2html"])

    assert selected == preferred


def test_gcov_object_targets_include_cmake_source_named_gcno_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "md4c.c"
    build_dir = repo / "build"
    object_dir = build_dir / "src" / "CMakeFiles" / "md4c.dir"
    source.parent.mkdir(parents=True)
    object_dir.mkdir(parents=True)
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    gcno = object_dir / "md4c.c.gcno"
    gcno.write_text("fake\n", encoding="utf-8")

    targets = _gcov_object_targets(source, repo, build_dir)

    assert targets[0] == gcno


def _write_canonical_cases(
    tests_path: Path,
    *,
    task_id: str = "make-c",
    executable_name: str = "calc",
) -> None:
    del executable_name
    suite = ExecutableTestSuite(
        task_id=task_id,
        iteration=0,
        generator="fixture",
        cases=[
            ExecutableTestCase(
                test_id="add",
                task_id=task_id,
                args=["add", "2", "5"],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(exact="7\n"),
                source="fixture",
            ),
            ExecutableTestCase(
                test_id="bad-command",
                task_id=task_id,
                args=["wat"],
                expected_exit_code=2,
                expected_stderr=ExpectedOutput(contains=["unknown command"]),
                source="fixture",
            ),
        ],
    )
    write_data(tests_path / "test_cases_iteration_0.json", suite.model_dump(mode="json"))
