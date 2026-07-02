"""Candidate-evaluation product workflow."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from pathlib import Path
import stat

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.errors import PBGenError
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.schemas import (
    CandidateEvaluationReport,
    CandidateSubmission,
    ReleasedTaskPackageManifest,
    TaskSpec,
)
from pbgen.security import enforce_command_allowed
from pbgen.security.command_executor import DockerNoNetworkCommandRunner
from pbgen.serialization import read_data, write_data
from pbgen.subprocess_utils import CommandResult, CommandRunner, LocalCommandRunner


_RUN_DIR_NAME = "latest"
_CLUTTER_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}


@dataclass(frozen=True)
class _EvaluationPackage:
    task_id: str
    evaluator_dir: Path
    hidden_tests_path: Path
    reports_dir: Path
    runtime_policy: str


def evaluate_executable_candidate(
    task_id: str,
    config: PBGenConfig,
    executable_path: Path,
) -> CandidateEvaluationReport:
    """Evaluate a legacy executable candidate against generated hidden tests."""

    paths = ArtifactPaths(config, task_id)
    result = run_generated_suite(task_id, paths.generated_tests, executable_path)
    report = CandidateEvaluationReport(
        task_id=task_id,
        resolved=result.total_tests > 0 and result.failed_tests == 0,
        tests_passed=result.passed_tests,
        total_tests=result.total_tests,
        pass_rate=result.pass_rate,
        build_success=True,
        runtime_policy=config.execution_policy,
        executable_path=executable_path,
        outcomes=result.outcomes,
    )
    write_data(paths.reports / "candidate_evaluation_report.json", report.model_dump(mode="json"))
    return report


def evaluate_source_submission(
    submission: CandidateSubmission,
    config: PBGenConfig,
) -> CandidateEvaluationReport:
    """Build a candidate source tree and run released hidden tests."""

    package = _resolve_evaluation_package(submission.package_path)
    if submission.submission_source is None:
        raise PBGenError("evaluate-submission requires a submission source directory.")
    if submission.build_script is None:
        raise PBGenError("evaluate-submission requires a candidate build script.")

    run_dir = package.evaluator_dir / "candidate_runs" / _RUN_DIR_NAME
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    source_dir = run_dir / "source"
    build_log_path = run_dir / "build.log"
    command_runner = _command_runner(config, run_dir)

    try:
        _copy_source_tree(submission.submission_source, source_dir)
        build_script = _copy_or_resolve_build_script(
            submission.build_script,
            original_source=submission.submission_source,
            copied_source=source_dir,
            run_dir=run_dir,
        )
        command = _build_command(build_script)
        enforce_command_allowed(
            command,
            policy=config.execution_policy,
            allow_patterns=config.safe_command_allow_patterns,
            deny_patterns=config.safe_command_deny_patterns,
            trusted=config.trusted_local_execution,
            command_kind="build",
        )
        build_result = command_runner.run(
            command,
            cwd=source_dir,
            timeout_seconds=config.build_timeout_seconds,
        )
    except (OSError, PBGenError) as exc:
        _write_build_log(build_log_path, None, str(exc))
        return _write_report(
            package,
            _failed_build_report(package, config, build_log_path, str(exc)),
        )

    _write_build_log(build_log_path, build_result, None)
    if not build_result.ok:
        return _write_report(
            package,
            _failed_build_report(
                package,
                config,
                build_log_path,
                f"candidate build script exited with status {build_result.returncode}",
            ),
        )

    executable = source_dir / "out" / "program"
    if not executable.is_file():
        return _write_report(
            package,
            _failed_build_report(
                package,
                config,
                build_log_path,
                "candidate build script did not produce out/program",
            ),
        )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    try:
        result = run_generated_suite(
            package.task_id,
            package.hidden_tests_path,
            executable,
            command_runner=_sandbox_runner(config, command_runner),
            work_root=_sandbox_work_root(config, run_dir),
        )
    except (OSError, PBGenError) as exc:
        return _write_report(
            package,
            CandidateEvaluationReport(
                task_id=package.task_id,
                resolved=False,
                tests_passed=0,
                total_tests=0,
                pass_rate=0.0,
                build_success=True,
                runtime_policy=config.execution_policy,
                executable_path=executable,
                build_log_path=build_log_path,
                reason=str(exc),
            ),
        )
    report = CandidateEvaluationReport(
        task_id=package.task_id,
        resolved=result.total_tests > 0 and result.failed_tests == 0,
        tests_passed=result.passed_tests,
        total_tests=result.total_tests,
        pass_rate=result.pass_rate,
        build_success=True,
        runtime_policy=config.execution_policy,
        executable_path=executable,
        build_log_path=build_log_path,
        outcomes=result.outcomes,
    )
    return _write_report(package, report)


def _resolve_evaluation_package(package_path: Path | None) -> _EvaluationPackage:
    if package_path is None:
        raise PBGenError("evaluate-submission requires a released package path.")
    path = package_path.expanduser().resolve()
    if path.is_file():
        manifest = ReleasedTaskPackageManifest.model_validate(read_data(path))
        evaluator_dir = manifest.evaluator_package
        return _EvaluationPackage(
            task_id=manifest.task_id,
            evaluator_dir=evaluator_dir,
            hidden_tests_path=manifest.hidden_tests_path,
            reports_dir=evaluator_dir / "reports",
            runtime_policy=manifest.runtime_policy,
        )
    if not path.is_dir():
        raise PBGenError(f"Released package path does not exist: {package_path}")
    manifest_path = path / "release_manifest.json"
    if manifest_path.exists():
        manifest = ReleasedTaskPackageManifest.model_validate(read_data(manifest_path))
        return _EvaluationPackage(
            task_id=manifest.task_id,
            evaluator_dir=path,
            hidden_tests_path=path / "hidden_tests",
            reports_dir=path / "reports",
            runtime_policy=manifest.runtime_policy,
        )
    task_path = path / "task.yaml"
    if not task_path.exists():
        raise PBGenError(f"Evaluator package is missing task.yaml: {path}")
    spec = TaskSpec.model_validate(read_data(task_path))
    return _EvaluationPackage(
        task_id=spec.task_id,
        evaluator_dir=path,
        hidden_tests_path=path / "hidden_tests",
        reports_dir=path / "reports",
        runtime_policy="unknown",
    )


def _copy_source_tree(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise PBGenError(f"Submission source is not a directory: {source}")
    if source.is_symlink():
        raise PBGenError(f"Submission source must not be a symlink: {source}")
    shutil.copytree(
        source,
        destination,
        ignore=_ignore_submission_paths,
    )


def _copy_or_resolve_build_script(
    build_script: Path,
    *,
    original_source: Path,
    copied_source: Path,
    run_dir: Path,
) -> Path:
    script = build_script.expanduser().resolve()
    source = original_source.expanduser().resolve()
    if not script.is_file():
        raise PBGenError(f"Candidate build script is not a file: {build_script}")
    if script.is_symlink():
        raise PBGenError(f"Candidate build script must not be a symlink: {build_script}")
    try:
        relative = script.relative_to(source)
    except ValueError:
        destination = run_dir / "build_script"
        shutil.copy2(script, destination)
        return destination
    return copied_source / relative


def _command_runner(config: PBGenConfig, run_dir: Path) -> CommandRunner:
    if _uses_docker_runner(config):
        return DockerNoNetworkCommandRunner(run_dir, image=config.docker_image)
    return LocalCommandRunner()


def _sandbox_runner(config: PBGenConfig, runner: CommandRunner) -> CommandRunner | None:
    return runner if _uses_docker_runner(config) else None


def _sandbox_work_root(config: PBGenConfig, run_dir: Path) -> Path | None:
    if not _uses_docker_runner(config):
        return None
    return run_dir / "test_work"


def _uses_docker_runner(config: PBGenConfig) -> bool:
    return config.execution_policy == "docker-no-network" and not config.trusted_local_execution


def _build_command(build_script: Path) -> list[str]:
    if build_script.suffix == ".py":
        return ["python3", str(build_script)]
    build_script.chmod(build_script.stat().st_mode | stat.S_IXUSR)
    return [str(build_script)]


def _ignore_submission_paths(directory: str, names: list[str]) -> set[str]:
    root = Path(directory)
    ignored: set[str] = set()
    for name in names:
        path = root / name
        if name in _CLUTTER_NAMES or name.endswith(".egg-info") or path.is_symlink():
            ignored.add(name)
    return ignored


def _failed_build_report(
    package: _EvaluationPackage,
    config: PBGenConfig,
    build_log_path: Path,
    reason: str,
) -> CandidateEvaluationReport:
    return CandidateEvaluationReport(
        task_id=package.task_id,
        resolved=False,
        tests_passed=0,
        total_tests=0,
        pass_rate=0.0,
        build_success=False,
        runtime_policy=config.execution_policy,
        build_log_path=build_log_path,
        reason=reason,
    )


def _write_build_log(
    path: Path,
    result: CommandResult | None,
    error: str | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if result is None:
        path.write_text(f"error: {error or 'unknown build error'}\n", encoding="utf-8")
        return
    path.write_text(
        "\n".join(
            [
                f"command: {result.args!r}",
                f"cwd: {result.cwd}",
                f"exit_code: {result.returncode}",
                "",
                "stdout:",
                result.stdout,
                "",
                "stderr:",
                result.stderr,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_report(
    package: _EvaluationPackage,
    report: CandidateEvaluationReport,
) -> CandidateEvaluationReport:
    package.reports_dir.mkdir(parents=True, exist_ok=True)
    write_data(
        package.reports_dir / "candidate_evaluation_report.json",
        report.model_dump(mode="json"),
    )
    return report
