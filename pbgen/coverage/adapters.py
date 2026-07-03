"""Coverage adapter interfaces and Python coverage.py integration."""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile
from typing import Any
import uuid

from pbgen.errors import CoverageError
from pbgen.eval.executable_runner import run_canonical_suites_from_path
from pbgen.schemas import CoverageGap, CoverageReport, TaskSpec
from pbgen.subprocess_utils import CommandResult, run_command


class CoverageAdapter(ABC):
    """Interface for language-specific coverage instrumentation."""

    @abstractmethod
    def instrument_build(self, repo_path: Path) -> Path:
        """Build or prepare a coverage-instrumented executable."""

    @abstractmethod
    def run_tests_with_coverage(self, tests_path: Path, executable_path: Path) -> CoverageReport:
        """Run tests and return structured coverage."""

    @abstractmethod
    def extract_uncovered_targets(self, report: CoverageReport) -> list[CoverageGap]:
        """Extract prioritized coverage gaps from a report."""


class PlaceholderCoverageAdapter(CoverageAdapter):
    """Explicit unavailable adapter for languages without coverage support yet."""

    def __init__(self, language: str) -> None:
        self.language = language

    def instrument_build(self, repo_path: Path) -> Path:
        raise CoverageError(f"{self.language} coverage instrumentation is not implemented yet.")

    def run_tests_with_coverage(self, tests_path: Path, executable_path: Path) -> CoverageReport:
        raise CoverageError(f"{self.language} coverage execution is not implemented yet.")

    def extract_uncovered_targets(self, report: CoverageReport) -> list[CoverageGap]:
        return report.gaps


@dataclass(frozen=True)
class CFamilyCoverageBuild:
    """Instrumented native executable and copied source tree."""

    executable_path: Path
    repo_path: Path
    build_dir: Path | None


@dataclass(frozen=True)
class CoverageWrapper:
    """Paths created for a coverage.py executable wrapper run."""

    wrapper_path: Path
    coverage_dir: Path
    data_file: Path
    json_report_path: Path


@dataclass(frozen=True)
class FunctionSpan:
    """AST function span used to map missing lines back to callable targets."""

    name: str
    start_line: int
    body_start_line: int
    end_line: int


class PythonCoverageAdapter(CoverageAdapter):
    """Run generated pytest suites through a coverage.py-backed executable wrapper."""

    def __init__(
        self,
        task_id: str = "unknown",
        iteration: int = 0,
        *,
        source_roots: list[Path] | None = None,
        work_dir: Path | None = None,
        python_executable: str | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.task_id = task_id
        self.iteration = iteration
        self.source_roots = tuple(path.resolve() for path in (source_roots or []))
        self.work_dir = work_dir
        self.python_executable = python_executable or sys.executable
        self.timeout_seconds = timeout_seconds

    def instrument_build(self, repo_path: Path) -> Path:
        """Register a Python source root for later coverage path resolution.

        Python programs do not need a compiler-level instrumentation step. The
        coverage wrapper is created per test run so it can point at the exact
        executable selected by the build stage.
        """

        resolved = repo_path.resolve()
        if not resolved.exists():
            raise CoverageError(f"Cannot instrument missing Python source root: {resolved}")
        self.source_roots = (*self.source_roots, resolved)
        return resolved

    def run_tests_with_coverage(self, tests_path: Path, executable_path: Path) -> CoverageReport:
        """Run pytest with PBGEN_EXECUTABLE set to a coverage wrapper executable."""

        tests_path = tests_path.resolve()
        executable_path = executable_path.resolve()
        _validate_coverage_inputs(tests_path, executable_path)
        _ensure_coverage_module(self.python_executable)

        with _coverage_run_directory(self.work_dir) as coverage_dir:
            wrapper = create_coverage_wrapper(
                executable_path,
                coverage_dir,
                python_executable=self.python_executable,
            )
            pytest_result = _run_pytest_against_wrapper(
                self.python_executable,
                tests_path,
                executable_path,
                wrapper.wrapper_path,
                self.source_roots,
                self.timeout_seconds,
            )
            if not pytest_result.ok:
                raise CoverageError(_format_command_failure("pytest coverage run failed", pytest_result))

            combine_result = run_command(
                [
                    self.python_executable,
                    "-m",
                    "coverage",
                    "combine",
                    "--data-file",
                    str(wrapper.data_file),
                    str(wrapper.coverage_dir),
                ],
                timeout_seconds=self.timeout_seconds,
            )
            if not combine_result.ok:
                raise CoverageError(_format_command_failure("coverage combine failed", combine_result))

            json_result = run_command(
                [
                    self.python_executable,
                    "-m",
                    "coverage",
                    "json",
                    "--data-file",
                    str(wrapper.data_file),
                    "-o",
                    str(wrapper.json_report_path),
                    "--pretty-print",
                ],
                timeout_seconds=self.timeout_seconds,
            )
            if not json_result.ok:
                raise CoverageError(_format_command_failure("coverage json export failed", json_result))

            return coverage_report_from_json(
                self.task_id,
                self.iteration,
                wrapper.json_report_path,
                source_roots=list(self.source_roots),
            )

    def extract_uncovered_targets(self, report: CoverageReport) -> list[CoverageGap]:
        """Return gaps sorted from most to least important."""

        return sorted(report.gaps, key=lambda gap: gap.priority, reverse=True)


class CFamilyCoverageAdapter(CoverageAdapter):
    """Collect native C/C++ line coverage with compiler coverage flags and gcov."""

    def __init__(
        self,
        spec: TaskSpec,
        *,
        task_id: str | None = None,
        iteration: int = 0,
        work_dir: Path | None = None,
        gcov_executable: str = "gcov",
        timeout_seconds: int = 120,
        preferred_executable_names: list[str] | None = None,
    ) -> None:
        self.spec = spec
        self.task_id = task_id or spec.task_id
        self.iteration = iteration
        self.work_dir = work_dir
        self.gcov_executable = gcov_executable
        self.timeout_seconds = timeout_seconds
        self.preferred_executable_names = preferred_executable_names or _preferred_executable_names(spec)
        self._instrumented: CFamilyCoverageBuild | None = None

    def instrument_build(self, repo_path: Path) -> Path:
        """Build an instrumented native executable in a copied work tree."""

        work_dir = (self.work_dir or Path(tempfile.mkdtemp(prefix="pbgen-cov-"))).resolve()
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        instrumented_repo = work_dir / "repo"
        shutil.copytree(
            repo_path,
            instrumented_repo,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "build"),
        )
        build_system = _coverage_build_system(self.spec, instrumented_repo)
        if build_system == "cmake":
            build = self._build_cmake(instrumented_repo)
        elif build_system == "c-single":
            build = self._build_single_source(instrumented_repo)
        elif build_system == "make":
            build = self._build_make(instrumented_repo)
        else:
            raise CoverageError(
                "Could not select a C/C++ coverage build system. "
                "Expected CMake, Make, or c-single metadata."
            )
        self._instrumented = build
        return build.executable_path

    def run_tests_with_coverage(self, tests_path: Path, executable_path: Path) -> CoverageReport:
        """Run canonical tests against the instrumented executable and parse gcov output."""

        if self._instrumented is None:
            self._instrumented = CFamilyCoverageBuild(
                executable_path=executable_path,
                repo_path=executable_path.parent,
                build_dir=None,
            )
        if shutil.which(self.gcov_executable) is None:
            return coverage_unavailable_report(
                self.task_id,
                self.iteration,
                "c-family-gcov",
                f"{self.gcov_executable} is not available",
            )
        result = run_canonical_suites_from_path(self.task_id, tests_path, executable_path)
        if result is None:
            return coverage_unavailable_report(
                self.task_id,
                self.iteration,
                "c-family-gcov",
                "no canonical executable test cases were found",
            )
        if result.exit_status != 0:
            raise CoverageError("instrumented C/C++ executable failed generated tests")
        return self._parse_gcov_report(self._instrumented)

    def extract_uncovered_targets(self, report: CoverageReport) -> list[CoverageGap]:
        return sorted(report.gaps, key=lambda gap: gap.priority, reverse=True)

    def _build_make(self, repo_path: Path) -> CFamilyCoverageBuild:
        run_command(["make", "clean"], cwd=repo_path, timeout_seconds=min(self.timeout_seconds, 60))
        result = run_command(
            [
                "make",
                "CFLAGS=--coverage -O0 -g",
                "CXXFLAGS=--coverage -O0 -g",
                "LDFLAGS=--coverage",
            ],
            cwd=repo_path,
            timeout_seconds=self.timeout_seconds,
        )
        if not result.ok:
            raise CoverageError(_format_command_failure("coverage make build failed", result))
        return CFamilyCoverageBuild(
            executable_path=_select_native_executable(
                repo_path,
                preferred_names=self.preferred_executable_names,
            ),
            repo_path=repo_path,
            build_dir=None,
        )

    def _build_cmake(self, repo_path: Path) -> CFamilyCoverageBuild:
        if shutil.which("cmake") is None:
            raise CoverageError("cmake is not available for coverage build")
        build_dir = repo_path / "build"
        configure = run_command(
            [
                "cmake",
                "-S",
                ".",
                "-B",
                "build",
                "-DCMAKE_BUILD_TYPE=Debug",
                "-DCMAKE_C_FLAGS=--coverage -O0 -g",
                "-DCMAKE_CXX_FLAGS=--coverage -O0 -g",
                "-DCMAKE_EXE_LINKER_FLAGS=--coverage",
                *_custom_cmake_definitions(self.spec),
            ],
            cwd=repo_path,
            timeout_seconds=self.timeout_seconds,
        )
        if not configure.ok:
            raise CoverageError(_format_command_failure("coverage cmake configure failed", configure))
        build_command = ["cmake", "--build", "build"]
        target = _custom_cmake_target(self.spec)
        if target:
            build_command.extend(["--target", target])
        build = run_command(
            build_command,
            cwd=repo_path,
            timeout_seconds=self.timeout_seconds,
        )
        if not build.ok:
            raise CoverageError(_format_command_failure("coverage cmake build failed", build))
        return CFamilyCoverageBuild(
            executable_path=_select_native_executable(
                build_dir,
                preferred_names=self.preferred_executable_names,
            ),
            repo_path=repo_path,
            build_dir=build_dir,
        )

    def _build_single_source(self, repo_path: Path) -> CFamilyCoverageBuild:
        source_rel = self.spec.build_candidates[0].entrypoint_paths[0] if self.spec.build_candidates else ""
        if not source_rel:
            raise CoverageError("single-source coverage build has no source file")
        compiler = _coverage_compiler_for_path(Path(source_rel))
        if compiler is None:
            raise CoverageError("no C/C++ compiler is available for coverage build")
        executable = repo_path / Path(source_rel).stem
        result = run_command(
            [compiler, "--coverage", "-O0", "-g", source_rel, "-o", str(executable)],
            cwd=repo_path,
            timeout_seconds=self.timeout_seconds,
        )
        if not result.ok:
            raise CoverageError(_format_command_failure("coverage single-source build failed", result))
        return CFamilyCoverageBuild(executable_path=executable, repo_path=repo_path, build_dir=None)

    def _parse_gcov_report(self, build: CFamilyCoverageBuild) -> CoverageReport:
        output_dir = build.repo_path / ".pbgen-gcov"
        output_dir.mkdir(exist_ok=True)
        source_files = _c_family_sources(build.repo_path)
        if not source_files:
            return coverage_unavailable_report(
                self.task_id,
                self.iteration,
                "c-family-gcov",
                "no C/C++ source files were found",
            )
        summaries: list[tuple[Path, float, int, list[CoverageGap], list[dict[str, object]]]] = []
        for source in source_files:
            gcov_result = _run_gcov_for_source(
                self.gcov_executable,
                source,
                build.repo_path,
                build.build_dir,
                output_dir,
                self.timeout_seconds,
            )
            if gcov_result is None:
                continue
            line_percent, executable_lines = _parse_gcov_stdout(gcov_result.stdout)
            gcov_file = output_dir / f"{source.name}.gcov"
            gaps, ranges = _parse_gcov_missing_ranges(gcov_file, build.repo_path)
            summaries.append((source, line_percent, executable_lines, gaps, ranges))
        if not summaries:
            return coverage_unavailable_report(
                self.task_id,
                self.iteration,
                "c-family-gcov",
                "gcov did not produce parseable source reports",
            )
        total_lines = sum(executable_lines for _, _, executable_lines, _, _ in summaries)
        weighted = sum(
            line_percent * executable_lines
            for _, line_percent, executable_lines, _, _ in summaries
        )
        all_gaps = [gap for _, _, _, gaps, _ in summaries for gap in gaps]
        all_ranges = [item for _, _, _, _, ranges in summaries for item in ranges]
        uncovered_files = sorted({gap.file_path for gap in all_gaps})
        return CoverageReport(
            task_id=self.task_id,
            iteration=self.iteration,
            coverage_backend="c-family-gcov",
            coverage_available=True,
            line_coverage=(weighted / total_lines / 100.0) if total_lines else None,
            uncovered_files=uncovered_files,
            uncovered_line_ranges=all_ranges,
            gaps=sorted(all_gaps, key=lambda gap: gap.priority, reverse=True),
        )


def run_python_coverage(
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    *,
    iteration: int = 0,
    source_roots: list[Path] | None = None,
    work_dir: Path | None = None,
    timeout_seconds: int = 120,
) -> CoverageReport:
    """Convenience hook for pipeline stages that do not need adapter state."""

    adapter = PythonCoverageAdapter(
        task_id=task_id,
        iteration=iteration,
        source_roots=source_roots,
        work_dir=work_dir,
        timeout_seconds=timeout_seconds,
    )
    return adapter.run_tests_with_coverage(tests_path, executable_path)


def coverage_unavailable_report(
    task_id: str,
    iteration: int,
    backend: str,
    reason: str,
) -> CoverageReport:
    """Return an honest report for unavailable coverage tooling/capability."""

    return CoverageReport(
        task_id=task_id,
        iteration=iteration,
        coverage_backend=backend,
        coverage_available=False,
        coverage_unavailable_reason=reason,
        line_coverage=None,
    )


def create_coverage_wrapper(
    executable_path: Path,
    coverage_dir: Path,
    *,
    python_executable: str | None = None,
    wrapper_name: str = "pbgen-coverage-wrapper",
) -> CoverageWrapper:
    """Create an executable that runs the target under coverage.py parallel mode."""

    executable_path = executable_path.resolve()
    coverage_dir = coverage_dir.resolve()
    python_executable = python_executable or sys.executable
    coverage_dir.mkdir(parents=True, exist_ok=True)

    data_file = coverage_dir / ".coverage"
    json_report_path = coverage_dir / "coverage.json"
    wrapper_path = coverage_dir / wrapper_name
    wrapper_path.write_text(
        _render_wrapper_script(
            executable_path=executable_path,
            coverage_dir=coverage_dir,
            data_file=data_file,
            python_executable=python_executable,
        ),
        encoding="utf-8",
    )
    wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)
    return CoverageWrapper(
        wrapper_path=wrapper_path,
        coverage_dir=coverage_dir,
        data_file=data_file,
        json_report_path=json_report_path,
    )


def coverage_report_from_json(
    task_id: str,
    iteration: int,
    coverage_json_path: Path,
    *,
    source_roots: list[Path] | None = None,
) -> CoverageReport:
    """Convert coverage.py JSON output into the existing CoverageReport schema."""

    source_roots = [path.resolve() for path in (source_roots or [])]
    try:
        payload = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CoverageError(f"Could not read coverage JSON report: {coverage_json_path}") from exc
    except json.JSONDecodeError as exc:
        raise CoverageError(f"Invalid coverage JSON report: {coverage_json_path}") from exc

    files = payload.get("files", {})
    if not isinstance(files, dict):
        raise CoverageError("coverage JSON report does not contain a files mapping.")

    uncovered_files: list[str] = []
    uncovered_line_ranges: list[dict[str, Any]] = []
    uncovered_function_keys: set[str] = set()
    gaps: list[CoverageGap] = []
    total_missing = 0

    file_entries = sorted(files.items(), key=lambda item: item[0])
    missing_by_file: dict[str, int] = {}
    coverage_by_file: dict[str, float | None] = {}
    for reported_path, file_payload in file_entries:
        if not isinstance(file_payload, dict):
            continue
        missing_lines = _int_list(file_payload.get("missing_lines"))
        total_missing += len(missing_lines)
        missing_by_file[reported_path] = len(missing_lines)
        coverage_by_file[reported_path] = _line_coverage_from_summary(file_payload.get("summary"))

    for reported_path, file_payload in file_entries:
        if not isinstance(file_payload, dict):
            continue
        missing_lines = _int_list(file_payload.get("missing_lines"))
        if not missing_lines:
            continue

        display_path = _display_file_path(reported_path, source_roots)
        uncovered_files.append(display_path)
        source_path = _resolve_reported_path(reported_path, coverage_json_path.parent, source_roots)
        function_spans = _function_spans_for_path(source_path) if source_path else []

        for start_line, end_line, function_name in _missing_ranges(missing_lines, function_spans):
            missing_count = end_line - start_line + 1
            range_record = {
                "file_path": display_path,
                "start_line": start_line,
                "end_line": end_line,
                "function_name": function_name,
                "missing_lines": missing_count,
            }
            uncovered_line_ranges.append(range_record)
            if function_name:
                uncovered_function_keys.add(f"{display_path}:{function_name}")
            gaps.append(
                CoverageGap(
                    file_path=display_path,
                    function_name=function_name,
                    start_line=start_line,
                    end_line=end_line,
                    reason=f"{missing_count} uncovered executable line(s)",
                    priority=_gap_priority(
                        missing_count=missing_count,
                        file_missing=missing_by_file.get(reported_path, missing_count),
                        total_missing=total_missing,
                        file_line_coverage=coverage_by_file.get(reported_path),
                        has_function=function_name is not None,
                    ),
                )
            )

    gaps = sorted(gaps, key=lambda gap: gap.priority, reverse=True)
    totals = payload.get("totals", {})
    line_coverage = _line_coverage_from_summary(totals)
    branch_coverage = _branch_coverage_from_summary(totals)
    function_coverage = _function_coverage(files, coverage_json_path.parent, source_roots)

    return CoverageReport(
        task_id=task_id,
        iteration=iteration,
        coverage_backend="python-coverage.py",
        coverage_available=True,
        line_coverage=line_coverage,
        branch_coverage=branch_coverage,
        function_coverage=function_coverage,
        uncovered_files=uncovered_files,
        uncovered_functions=sorted(uncovered_function_keys),
        uncovered_line_ranges=uncovered_line_ranges,
        gaps=gaps,
    )


def _first_build_system(spec: TaskSpec) -> str | None:
    return spec.build_candidates[0].build_system if spec.build_candidates else None


def _coverage_build_system(spec: TaskSpec, repo_path: Path) -> str | None:
    build_system = (spec.build_system or _first_build_system(spec) or "").lower()
    if build_system in {"cmake", "make", "c-single"}:
        return build_system
    if build_system == "custom-command":
        if _custom_command_uses_cmake(spec) or (repo_path / "CMakeLists.txt").is_file():
            return "cmake"
        if _custom_command_uses_make(spec) or _has_makefile(repo_path):
            return "make"
        return None
    if (repo_path / "CMakeLists.txt").is_file():
        return "cmake"
    if _has_makefile(repo_path):
        return "make"
    return build_system or None


def _custom_command_uses_cmake(spec: TaskSpec) -> bool:
    return any(
        "cmake" in {token.lower() for token in _command_tokens(command)}
        for candidate in spec.build_candidates
        for command in candidate.commands
    )


def _custom_command_uses_make(spec: TaskSpec) -> bool:
    return any(
        bool({"make", "gmake"} & {token.lower() for token in _command_tokens(command)})
        for candidate in spec.build_candidates
        for command in candidate.commands
    )


def _command_tokens(command: list[str]) -> list[str]:
    tokens: list[str] = []
    for token in command:
        try:
            split = shlex.split(token)
        except ValueError:
            split = [token]
        tokens.extend(
            Path(item).name if index == 0 else item
            for index, item in enumerate(split)
        )
    return tokens


def _custom_cmake_definitions(spec: TaskSpec) -> list[str]:
    return sorted(
        {
            token
            for token in _custom_command_tokens(spec)
            if token.startswith("-D")
        }
    )


def _custom_cmake_target(spec: TaskSpec) -> str | None:
    tokens = _custom_command_tokens(spec)
    for index, token in enumerate(tokens):
        if token == "--target" and index + 1 < len(tokens):
            target = tokens[index + 1]
            if target and not target.startswith("-"):
                return target
    return None


def _custom_command_tokens(spec: TaskSpec) -> list[str]:
    return [
        token
        for candidate in spec.build_candidates
        for command in candidate.commands
        for token in _command_tokens(command)
    ]


def _has_makefile(repo_path: Path) -> bool:
    return any((repo_path / name).is_file() for name in ("GNUmakefile", "Makefile", "makefile"))


def _preferred_executable_names(spec: TaskSpec) -> list[str]:
    names: list[str] = []
    for value in spec.binary_names:
        names.extend(_executable_name_variants(value))
    for candidate in spec.build_candidates:
        for value in candidate.output_hints:
            names.extend(_executable_name_variants(value))
        for value in candidate.entrypoint_paths:
            names.extend(_executable_name_variants(value))
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        normalized = name.strip().strip("./")
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            unique.append(normalized)
    return unique


def _executable_name_variants(value: str) -> list[str]:
    path = Path(value)
    variants = [value, path.name, path.stem]
    if value.startswith("build/"):
        variants.append(value.removeprefix("build/"))
    return variants


def _select_native_executable(root: Path, *, preferred_names: list[str] | None = None) -> Path:
    outputs = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.stat().st_mode & stat.S_IXUSR
        and not _has_ignored_native_part(path.relative_to(root))
        and path.suffix.lower() not in {".o", ".a", ".so", ".dylib", ".gcno", ".gcda"}
    ]
    if not outputs:
        raise CoverageError(f"No native executable produced under {root}")
    preferred = {
        variant.lower().strip("./")
        for name in (preferred_names or [])
        for variant in _executable_name_variants(name)
        if variant.strip()
    }
    return sorted(
        outputs,
        key=lambda path: (
            _native_executable_preference(path, root, preferred),
            len(path.relative_to(root).parts),
            path.name,
        ),
    )[0]


def _native_executable_preference(path: Path, root: Path, preferred: set[str]) -> int:
    if not preferred:
        return 0
    relative = path.relative_to(root).as_posix().lower()
    candidates = {relative, path.name.lower(), path.stem.lower()}
    if candidates & preferred:
        return 0
    if any(relative.endswith(name) for name in preferred):
        return 0
    return 1


def _has_ignored_native_part(path: Path) -> bool:
    return any(part in {".git", ".pbgen-gcov", "CMakeFiles"} for part in path.parts)


def _coverage_compiler_for_path(path: Path) -> str | None:
    if path.suffix.lower() in {".cpp", ".cc", ".cxx"}:
        for compiler in ("c++", "clang++", "g++"):
            if shutil.which(compiler):
                return compiler
        return None
    for compiler in ("cc", "clang", "gcc"):
        if shutil.which(compiler):
            return compiler
    return None


def _c_family_sources(repo_path: Path) -> list[Path]:
    return [
        path
        for path in sorted(repo_path.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in {".c", ".cpp", ".cc", ".cxx"}
        and not any(part in {"build", ".git", ".pbgen-gcov"} for part in path.relative_to(repo_path).parts)
    ]


def _run_gcov_for_source(
    gcov_executable: str,
    source: Path,
    repo_path: Path,
    build_dir: Path | None,
    output_dir: Path,
    timeout_seconds: int,
) -> CommandResult | None:
    object_targets = _gcov_object_targets(source, repo_path, build_dir)
    if build_dir is not None:
        object_targets.extend([build_dir, *sorted(path for path in build_dir.rglob("*") if path.is_dir())])
    object_targets.append(repo_path)
    seen: set[Path] = set()
    for object_target in object_targets:
        if object_target in seen:
            continue
        seen.add(object_target)
        result = run_command(
            [
                gcov_executable,
                "-b",
                "-o",
                str(object_target),
                str(source),
            ],
            cwd=output_dir,
            timeout_seconds=timeout_seconds,
        )
        if result.ok and "Lines executed:" in result.stdout:
            return result
    return None


def _gcov_object_targets(source: Path, repo_path: Path, build_dir: Path | None) -> list[Path]:
    """Return concrete gcov data files before directory fallbacks."""

    roots = [repo_path]
    if build_dir is not None:
        roots.insert(0, build_dir)
    names = {f"{source.name}.gcno", f"{source.stem}.gcno"}
    targets: list[Path] = []
    for root in roots:
        targets.extend(path for path in sorted(root.rglob("*")) if path.is_file() and path.name in names)
    return targets


def _parse_gcov_stdout(stdout: str) -> tuple[float, int]:
    percent_match = re.search(r"Lines executed:([0-9.]+)% of ([0-9]+)", stdout)
    if percent_match is None:
        return 0.0, 0
    return float(percent_match.group(1)), int(percent_match.group(2))


def _parse_gcov_missing_ranges(
    gcov_file: Path,
    repo_path: Path,
) -> tuple[list[CoverageGap], list[dict[str, object]]]:
    if not gcov_file.exists():
        return [], []
    missing_lines: list[int] = []
    source_file = gcov_file.stem
    for line in gcov_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        count, line_number_text, source_text = (part.strip() for part in parts)
        if count not in {"#####", "====="}:
            continue
        if not source_text or source_text.startswith(("{", "}", "/*", "*")):
            continue
        try:
            missing_lines.append(int(line_number_text))
        except ValueError:
            continue
    ranges: list[dict[str, object]] = []
    gaps: list[CoverageGap] = []
    for start, end in _collapse_ranges(missing_lines):
        missing_count = end - start + 1
        ranges.append(
            {
                "file_path": source_file,
                "start_line": start,
                "end_line": end,
                "function_name": None,
                "missing_lines": missing_count,
            }
        )
        gaps.append(
            CoverageGap(
                file_path=_display_file_path(source_file, [repo_path]),
                start_line=start,
                end_line=end,
                reason=f"{missing_count} uncovered native line(s)",
                priority=min(1.0, 0.25 + (float(missing_count) / 20.0)),
            )
        )
    return gaps, ranges


def _collapse_ranges(lines: list[int]) -> list[tuple[int, int]]:
    if not lines:
        return []
    sorted_lines = sorted(set(lines))
    ranges: list[tuple[int, int]] = []
    start = previous = sorted_lines[0]
    for line in sorted_lines[1:]:
        if line == previous + 1:
            previous = line
            continue
        ranges.append((start, previous))
        start = previous = line
    ranges.append((start, previous))
    return ranges


def _render_wrapper_script(
    *,
    executable_path: Path,
    coverage_dir: Path,
    data_file: Path,
    python_executable: str,
) -> str:
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys

PYTHON_EXECUTABLE = {str(python_executable)!r}
TARGET_EXECUTABLE = {str(executable_path)!r}
COVERAGE_DIR = {str(coverage_dir)!r}
DATA_FILE = {str(data_file)!r}


def main() -> int:
    env = os.environ.copy()
    env["COVERAGE_FILE"] = DATA_FILE
    command = [
        PYTHON_EXECUTABLE,
        "-m",
        "coverage",
        "run",
        "--parallel-mode",
        "--data-file",
        DATA_FILE,
        TARGET_EXECUTABLE,
        *sys.argv[1:],
    ]
    completed = subprocess.run(command, check=False, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _validate_coverage_inputs(tests_path: Path, executable_path: Path) -> None:
    if not tests_path.exists():
        raise CoverageError(f"Cannot run coverage for missing tests path: {tests_path}")
    if not executable_path.exists():
        raise CoverageError(f"Cannot run coverage for missing executable: {executable_path}")


def _ensure_coverage_module(python_executable: str) -> None:
    result = run_command(
        [python_executable, "-m", "coverage", "--version"],
        timeout_seconds=20,
    )
    if not result.ok:
        raise CoverageError(
            "coverage.py is required for Python coverage collection. "
            "Install the coverage package in the Python environment used by pbgen."
        )


class _coverage_run_directory:
    def __init__(self, work_dir: Path | None) -> None:
        self.work_dir = work_dir
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if self.work_dir is None:
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="pbgen-coverage-")
            self.path = Path(self._temporary_directory.name)
        else:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            self.path = self.work_dir / f"coverage-run-{uuid.uuid4().hex}"
            self.path.mkdir(parents=True, exist_ok=False)
        return self.path

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()


def _run_pytest_against_wrapper(
    python_executable: str,
    tests_path: Path,
    executable_path: Path,
    wrapper_path: Path,
    source_roots: tuple[Path, ...],
    timeout_seconds: int,
) -> CommandResult:
    env = os.environ.copy()
    env["PBGEN_EXECUTABLE"] = str(wrapper_path)
    pythonpath_entries = [str(executable_path.parent), *(str(path) for path in source_roots)]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(pythonpath_entries))
    cwd = tests_path if tests_path.is_dir() else tests_path.parent
    return run_command(
        [python_executable, "-m", "pytest", str(tests_path), "-q"],
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
    )


def _format_command_failure(message: str, result: CommandResult) -> str:
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    details = [f"{message} (exit {result.returncode})."]
    if stdout:
        details.append(f"stdout:\n{stdout}")
    if stderr:
        details.append(f"stderr:\n{stderr}")
    return "\n".join(details)


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    lines: set[int] = set()
    for item in value:
        try:
            lines.add(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(lines)


def _line_coverage_from_summary(summary: object) -> float | None:
    if not isinstance(summary, dict):
        return None
    ratio = _ratio(summary, "covered_lines", "num_statements")
    if ratio is not None:
        return ratio
    percent = summary.get("percent_covered")
    return _percent_to_ratio(percent)


def _branch_coverage_from_summary(summary: object) -> float | None:
    if not isinstance(summary, dict):
        return None
    return _ratio(summary, "covered_branches", "num_branches")


def _ratio(summary: dict[str, object], covered_key: str, total_key: str) -> float | None:
    covered = _as_float(summary.get(covered_key))
    total = _as_float(summary.get(total_key))
    if covered is None or total is None:
        return None
    if total == 0:
        return None
    return covered / total


def _percent_to_ratio(value: object) -> float | None:
    number = _as_float(value)
    if number is None:
        return None
    return number / 100.0


def _as_float(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _display_file_path(reported_path: str, source_roots: list[Path]) -> str:
    path = Path(reported_path)
    if not path.is_absolute():
        return reported_path
    for root in source_roots:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _resolve_reported_path(
    reported_path: str,
    report_dir: Path,
    source_roots: list[Path],
) -> Path | None:
    path = Path(reported_path)
    candidates = [path] if path.is_absolute() else []
    candidates.extend(root / reported_path for root in source_roots)
    candidates.append(report_dir / reported_path)
    candidates.append(Path.cwd() / reported_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _function_spans_for_path(source_path: Path) -> list[FunctionSpan]:
    try:
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    spans: list[FunctionSpan] = []

    def visit_body(nodes: list[ast.stmt], prefix: tuple[str, ...]) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = ".".join((*prefix, node.name))
                spans.append(
                    FunctionSpan(
                        name=qualified_name,
                        start_line=node.lineno,
                        body_start_line=_body_start_line(node),
                        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    )
                )
                visit_body(node.body, (*prefix, node.name))
            elif isinstance(node, ast.ClassDef):
                visit_body(node.body, (*prefix, node.name))

    visit_body(tree.body, ())
    return spans


def _body_start_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    if not node.body:
        return node.lineno
    return min(statement.lineno for statement in node.body)


def _missing_ranges(
    missing_lines: list[int],
    function_spans: list[FunctionSpan],
) -> list[tuple[int, int, str | None]]:
    ranges: list[tuple[int, int, str | None]] = []
    start_line: int | None = None
    previous_line: int | None = None
    current_function: str | None = None

    for line in missing_lines:
        function_name = _function_for_line(line, function_spans)
        if (
            start_line is None
            or previous_line is None
            or line != previous_line + 1
            or function_name != current_function
        ):
            if start_line is not None and previous_line is not None:
                ranges.append((start_line, previous_line, current_function))
            start_line = line
            current_function = function_name
        previous_line = line

    if start_line is not None and previous_line is not None:
        ranges.append((start_line, previous_line, current_function))
    return ranges


def _function_for_line(line: int, function_spans: list[FunctionSpan]) -> str | None:
    matches = [
        span for span in function_spans if span.start_line <= line <= span.end_line
    ]
    if not matches:
        return None
    return max(matches, key=lambda span: (span.start_line, -span.end_line)).name


def _gap_priority(
    *,
    missing_count: int,
    file_missing: int,
    total_missing: int,
    file_line_coverage: float | None,
    has_function: bool,
) -> float:
    file_share = file_missing / total_missing if total_missing else 0.0
    uncovered_weight = 1.0 - file_line_coverage if file_line_coverage is not None else 0.0
    function_bonus = 0.25 if has_function else 0.0
    return round(float(missing_count) + file_share + uncovered_weight + function_bonus, 4)


def _function_coverage(
    files: object,
    report_dir: Path,
    source_roots: list[Path],
) -> float | None:
    if not isinstance(files, dict):
        return None

    total_functions = 0
    covered_functions = 0
    for reported_path, file_payload in files.items():
        if not isinstance(reported_path, str) or not isinstance(file_payload, dict):
            continue
        source_path = _resolve_reported_path(reported_path, report_dir, source_roots)
        if source_path is None:
            continue
        spans = _function_spans_for_path(source_path)
        if not spans:
            continue
        executed_lines = set(_int_list(file_payload.get("executed_lines")))
        for span in spans:
            total_functions += 1
            if any(span.body_start_line <= line <= span.end_line for line in executed_lines):
                covered_functions += 1

    if total_functions == 0:
        return None
    return covered_functions / total_functions
