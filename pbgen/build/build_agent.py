"""Gold executable build orchestration."""

from __future__ import annotations

import shutil
import stat
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from pbgen.build.executable_hash import hash_executable
from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.errors import BuildError
from pbgen.logging.event_log import EventLogger
from pbgen.repo_discovery.metadata import analyze_repository
from pbgen.schemas import BuildArtifact, BuildCandidate, EntrypointCandidate, TaskSpec
from pbgen.serialization import read_data, write_data
from pbgen.subprocess_utils import run_command


class BuildBackend(ABC):
    """Interface for gold executable build backends."""

    @abstractmethod
    def build(self, spec: TaskSpec, repo_path: Path, output_dir: Path) -> BuildArtifact:
        """Build or assemble the gold executable."""


class LocalBuildBackend(BuildBackend):
    """Local build backend for script/Python demo tasks and simple projects."""

    def __init__(
        self,
        build_system_override: str | None = None,
        *,
        build_timeout_seconds: int = 300,
        probe_timeout_seconds: int = 15,
    ) -> None:
        self.build_system_override = build_system_override
        self.build_timeout_seconds = build_timeout_seconds
        self.probe_timeout_seconds = probe_timeout_seconds

    def build(self, spec: TaskSpec, repo_path: Path, output_dir: Path) -> BuildArtifact:
        output_dir.mkdir(parents=True, exist_ok=True)
        exe_dir = output_dir / "executable"
        all_exe_dir = output_dir / "executables"
        exe_dir.mkdir(parents=True, exist_ok=True)
        all_exe_dir.mkdir(parents=True, exist_ok=True)
        build_script = output_dir / "build_gold.sh"
        build_log = output_dir / "build.log"
        executable = exe_dir / "program"
        attempts: list[dict[str, object]] = []

        build_script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n# Local ProgramBench gold build replay.\n",
            encoding="utf-8",
        )
        build_script.chmod(build_script.stat().st_mode | stat.S_IXUSR)

        built: dict[str, Path] = {}
        errors: list[str] = []
        candidates = spec.build_candidates or [
            BuildCandidate(
                build_system=spec.build_system or "script",
                language=spec.language,
                confidence=0.5,
                entrypoint_paths=spec.binary_names,
            )
        ]
        if self.build_system_override and self.build_system_override != "auto":
            candidates = [
                candidate
                for candidate in candidates
                if candidate.build_system == self.build_system_override
            ]
        for candidate in candidates:
            try:
                if candidate.build_system in {"python-script", "script"}:
                    built = self._build_scripts(spec, repo_path, all_exe_dir, attempts)
                elif candidate.build_system == "python-package":
                    built = self._build_python_package(spec, repo_path, all_exe_dir, attempts)
                elif candidate.build_system == "make":
                    built = self._build_make(repo_path, all_exe_dir, attempts)
                elif candidate.build_system == "c-single":
                    built = self._build_c_single(candidate, repo_path, all_exe_dir, attempts)
                else:
                    attempts.append(
                        {
                            "build_system": candidate.build_system,
                            "status": "skipped",
                            "reason": "Backend detected but not implemented in local MVP.",
                        }
                    )
                    continue
                if built:
                    break
            except BuildError as exc:
                errors.append(str(exc))
                attempts.append(
                    {
                        "build_system": candidate.build_system,
                        "status": "failed",
                        "reason": str(exc),
                    }
                )

        if not built:
            build_log.write_text(_format_attempts(attempts), encoding="utf-8")
            detail = (
                "; ".join(errors)
                if errors
                else "No local build candidate produced an executable."
            )
            raise BuildError(f"{detail} See build log: {build_log}")

        primary_name, primary_path = self._choose_primary(spec, built)
        shutil.copy2(primary_path, executable)
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        package_app_dir = all_exe_dir / "_python_app"
        if package_app_dir.exists():
            exe_app_dir = exe_dir / "_python_app"
            if exe_app_dir.exists():
                shutil.rmtree(exe_app_dir)
            shutil.copytree(package_app_dir, exe_app_dir)

        probes = self._probe_executable(executable, repo_path)
        build_log.write_text(
            _format_attempts(attempts)
            + f"\nPrimary executable: {primary_name} -> {primary_path}\n\n"
            + "\n".join(probes),
            encoding="utf-8",
        )

        return BuildArtifact(
            task_id=spec.task_id,
            build_success=True,
            build_script_path=build_script,
            executable_path=executable,
            executable_hash=hash_executable(executable),
            docker_image=None,
            build_log_path=build_log,
            runtime_dependencies=["python3"] if spec.language == "python" else [],
            executable_paths=built,
            build_attempts=attempts,
        )

    def _build_scripts(
        self,
        spec: TaskSpec,
        repo_path: Path,
        output_dir: Path,
        attempts: list[dict[str, object]],
    ) -> dict[str, Path]:
        built: dict[str, Path] = {}
        entrypoints = spec.entrypoint_candidates or [
            EntrypointCandidate(
                name=Path(name).stem,
                path=name,
                invocation_kind="python-script" if name.endswith(".py") else "executable-script",
                confidence=0.5,
                reason="legacy binary name",
            )
            for name in spec.binary_names
        ]
        for entrypoint in entrypoints:
            source = repo_path / entrypoint.path
            if not source.exists() or not source.is_file():
                continue
            if entrypoint.invocation_kind in {"python-module", "python-entrypoint"}:
                continue
            destination = output_dir / _safe_executable_name(entrypoint.name)
            if source.suffix == ".py":
                _write_python_script_wrapper(destination, repo_path, source)
            else:
                shutil.copy2(source, destination)
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
            built[entrypoint.name] = destination
        attempts.append(
            {
                "build_system": "script",
                "status": "succeeded" if built else "no_outputs",
                "outputs": {name: path.as_posix() for name, path in built.items()},
            }
        )
        return built

    def _build_python_package(
        self,
        spec: TaskSpec,
        repo_path: Path,
        output_dir: Path,
        attempts: list[dict[str, object]],
    ) -> dict[str, Path]:
        built: dict[str, Path] = {}
        app_dir = output_dir / "_python_app"
        if app_dir.exists():
            shutil.rmtree(app_dir)
        shutil.copytree(
            repo_path,
            app_dir,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"),
        )
        discovered = {
            (candidate.path, candidate.invocation_kind): candidate
            for candidate in analyze_repository(repo_path).entrypoint_candidates
        }
        entrypoints: list[object] = list(spec.entrypoint_candidates)
        if not entrypoints:
            entrypoints = [
                candidate
                for candidate in discovered.values()
                if candidate.invocation_kind in {"python-module", "python-entrypoint"}
            ]
        for entrypoint in entrypoints:
            invocation_kind = getattr(entrypoint, "invocation_kind", "")
            if invocation_kind not in {"python-module", "python-entrypoint"}:
                continue
            entrypoint_path = getattr(entrypoint, "path", "")
            entrypoint_name = getattr(entrypoint, "name", Path(entrypoint_path).stem)
            local_candidate = discovered.get((entrypoint_path, invocation_kind))
            module = (
                getattr(entrypoint, "module", None)
                or (local_candidate.module if local_candidate else None)
                or _module_name_from_main(Path(entrypoint_path))
            )
            if not module:
                continue
            callable_name = getattr(entrypoint, "callable_name", None) or (
                local_candidate.callable_name if local_candidate else None
            )
            destination = output_dir / _safe_executable_name(entrypoint_name)
            _write_python_package_wrapper(destination, module, callable_name)
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
            built[entrypoint_name] = destination
        attempts.append(
            {
                "build_system": "python-package",
                "status": "succeeded" if built else "no_module_entrypoint",
                "outputs": {name: path.as_posix() for name, path in built.items()},
            }
        )
        return built

    def _build_make(
        self,
        repo_path: Path,
        output_dir: Path,
        attempts: list[dict[str, object]],
    ) -> dict[str, Path]:
        before = _executable_snapshot(repo_path)
        try:
            result = run_command(
                ["make"],
                cwd=repo_path,
                timeout_seconds=self.build_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            attempts.append(
                {
                    "build_system": "make",
                    "command": ["make"],
                    "status": "failed",
                    "reason": "timeout",
                    "timeout_seconds": self.build_timeout_seconds,
                    "stdout": _timeout_text(exc.stdout),
                    "stderr": _timeout_text(exc.stderr),
                }
            )
            raise BuildError("make timed out") from exc
        after = _executable_snapshot(repo_path)
        created = sorted(after - before)
        if not result.ok:
            attempts.append(
                {
                    "build_system": "make",
                    "command": ["make"],
                    "status": "failed",
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-2000:],
                    "stderr": result.stderr[-2000:],
                }
            )
            raise BuildError("make failed")
        outputs = created or _discover_existing_executables(repo_path)
        built: dict[str, Path] = {}
        for source in outputs:
            name = _safe_executable_name(source.stem)
            destination = output_dir / name
            shutil.copy2(source, destination)
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
            built[name] = destination
        attempts.append(
            {
                "build_system": "make",
                "command": ["make"],
                "status": "succeeded" if built else "no_outputs",
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
                "outputs": {name: path.as_posix() for name, path in built.items()},
            }
        )
        return built

    def _build_c_single(
        self,
        candidate: BuildCandidate,
        repo_path: Path,
        output_dir: Path,
        attempts: list[dict[str, object]],
    ) -> dict[str, Path]:
        if shutil.which("gcc") is None:
            raise BuildError("gcc is not available for c-single build.")
        source_rel = candidate.entrypoint_paths[0] if candidate.entrypoint_paths else ""
        source = repo_path / source_rel
        if not source.exists():
            raise BuildError("No C source file found for c-single build.")
        destination = output_dir / _safe_executable_name(source.stem)
        result = run_command(
            ["gcc", source_rel, "-o", str(destination)],
            cwd=repo_path,
            timeout_seconds=min(self.build_timeout_seconds, 120),
        )
        attempts.append(
            {
                "build_system": "c-single",
                "command": ["gcc", source_rel, "-o", str(destination)],
                "status": "succeeded" if result.ok else "failed",
                "exit_code": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
        )
        if not result.ok:
            raise BuildError("gcc build failed")
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
        return {source.stem: destination}

    def _choose_primary(self, spec: TaskSpec, built: dict[str, Path]) -> tuple[str, Path]:
        for entrypoint in spec.entrypoint_candidates:
            for name, path in built.items():
                if entrypoint.name == name or Path(entrypoint.path).stem == name:
                    return name, path
        return sorted(built.items())[0]

    def _probe_executable(self, executable: Path, repo_path: Path) -> list[str]:
        probes: list[str] = []
        for args in (["--help"], ["-h"], ["--version"], []):
            try:
                result = run_command(
                    [str(executable), *args],
                    cwd=repo_path,
                    timeout_seconds=self.probe_timeout_seconds,
                )
                probes.append(
                    f"$ program {' '.join(args)}\nexit={result.returncode}\n"
                    f"stdout={result.stdout[:500]}\nstderr={result.stderr[:500]}\n"
                )
            except subprocess.TimeoutExpired as exc:
                probes.append(
                    f"$ program {' '.join(args)}\n"
                    f"timeout={self.probe_timeout_seconds}\n"
                    f"stdout={_timeout_text(exc.stdout)[:500]}\n"
                    f"stderr={_timeout_text(exc.stderr)[:500]}\n"
                )
        return probes


def build_gold(
    task_id: str,
    config: PBGenConfig,
    backend: BuildBackend | None = None,
    *,
    build_system: str | None = None,
) -> BuildArtifact:
    """Build the reference executable for a task."""

    paths = ArtifactPaths(config, task_id)
    logger = EventLogger(paths.event_log)
    spec = TaskSpec.model_validate(read_data(paths.task_spec))
    logger.append(task_id=task_id, stage="build", event_type="gold_build_started")
    backend = backend or LocalBuildBackend(
        build_system_override=build_system,
        build_timeout_seconds=config.build_timeout_seconds,
        probe_timeout_seconds=config.probe_timeout_seconds,
    )
    try:
        artifact = backend.build(spec, paths.repo, paths.gold)
    except BuildError:
        logger.append(task_id=task_id, stage="build", event_type="gold_build_failed")
        raise
    write_data(paths.build_artifact, artifact.model_dump(mode="json"))
    logger.append(
        task_id=task_id,
        stage="build",
        event_type="executable_hashed",
        output_hashes={"executable": artifact.executable_hash},
    )
    logger.append(task_id=task_id, stage="build", event_type="gold_build_succeeded")
    return artifact


def _safe_executable_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in name)
    return cleaned or "program"


def _module_name_from_main(path: Path) -> str | None:
    parts = list(path.parts)
    if not parts or parts[-1] != "__main__.py":
        return None
    if parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts[:-1]) or None


def _write_python_script_wrapper(destination: Path, repo_path: Path, source: Path) -> None:
    destination.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import runpy",
                "import sys",
                "from pathlib import Path",
                "",
                f"repo = Path({str(repo_path)!r})",
                f"source = Path({str(source)!r})",
                "sys.path.insert(0, str(repo))",
                "sys.argv[0] = str(source)",
                "runpy.run_path(str(source), run_name='__main__')",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_python_package_wrapper(
    destination: Path, module: str, callable_name: str | None
) -> None:
    lines = [
        "#!/usr/bin/env python3",
        "import runpy",
        "import sys",
        "from pathlib import Path",
        "",
        'app = Path(__file__).resolve().parent / "_python_app"',
        'sys.path.insert(0, str(app / "src"))',
        "sys.path.insert(0, str(app))",
    ]
    if callable_name:
        lines.extend(
            [
                f"module = __import__({module!r}, fromlist=[{callable_name!r}])",
                f"raise SystemExit(getattr(module, {callable_name!r})())",
            ]
        )
    else:
        lines.append(f"runpy.run_module({module!r}, run_name='__main__')")
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def _executable_snapshot(repo_path: Path) -> set[Path]:
    return set(_discover_existing_executables(repo_path))


def _discover_existing_executables(repo_path: Path) -> list[Path]:
    ignored = {".git", ".venv", "__pycache__", "target", "node_modules"}
    outputs: list[Path] = []
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo_path).parts
        if any(part in ignored for part in rel_parts):
            continue
        if path.suffix.lower() in {".c", ".h", ".py", ".java", ".rs", ".go", ".o"}:
            continue
        if path.stat().st_mode & stat.S_IXUSR:
            outputs.append(path)
    return outputs


def _format_attempts(attempts: list[dict[str, object]]) -> str:
    lines = ["Build attempts:"]
    for attempt in attempts:
        lines.append(str(attempt))
    return "\n".join(lines) + "\n"


def _timeout_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-2000:]
    return str(value)[-2000:]
