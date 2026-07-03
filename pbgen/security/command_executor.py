"""Command execution backends for local and no-network sandboxed runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

from pbgen.errors import PBGenError
from pbgen.subprocess_utils import CommandResult, run_command


_CONTAINER_ROOT = Path("/workspace")
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class DockerNoNetworkCommandRunner:
    """Run commands inside a Docker container with networking disabled."""

    mount_root: Path
    image: str = "python:3.11-slim"
    cpus: int = 2
    memory: str = "2g"
    docker_executable: str = "docker"

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_root", self.mount_root.expanduser().resolve())

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = 60,
    ) -> CommandResult:
        """Run a command in a no-network Docker container."""

        self.preflight()
        docker_args = self.docker_args(args, cwd=cwd, env=env, stdin=stdin)
        return run_command(docker_args, stdin=stdin, timeout_seconds=timeout_seconds)

    def preflight(self) -> None:
        """Validate Docker and the configured image before execution."""

        if shutil.which(self.docker_executable) is None:
            raise PBGenError(
                "Docker executable is not available for docker-no-network execution."
            )
        result = run_command(
            [self.docker_executable, "image", "inspect", self.image],
            timeout_seconds=20,
        )
        if not result.ok:
            raise PBGenError(
                f"Docker image {self.image!r} is not available locally for "
                "docker-no-network execution."
            )

    def docker_args(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> list[str]:
        """Return the exact `docker run` command for tests and logs."""

        if not args:
            raise PBGenError("Cannot execute an empty command in Docker.")
        host_cwd = (cwd or self.mount_root).expanduser().resolve()
        container_cwd = self._container_path(host_cwd)
        command = [
            self.docker_executable,
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            str(self.cpus),
            "--memory",
            self.memory,
            "-v",
            f"{self.mount_root}:{_CONTAINER_ROOT}",
            "-w",
            str(container_cwd),
        ]
        if stdin is not None:
            command.append("-i")
        for key, value in sorted((env or {}).items()):
            if not _ENV_NAME_PATTERN.match(key):
                raise PBGenError(f"Invalid environment variable for Docker run: {key!r}")
            command.extend(["-e", f"{key}={value}"])
        command.append(self.image)
        command.extend(self._containerized_args(args))
        return command

    def _containerized_args(self, args: list[str]) -> list[str]:
        converted: list[str] = []
        for arg in args:
            path = Path(arg)
            if path.is_absolute():
                converted.append(str(self._container_path(path.expanduser().resolve())))
            else:
                converted.append(arg)
        return converted

    def _container_path(self, host_path: Path) -> Path:
        try:
            relative = host_path.relative_to(self.mount_root)
        except ValueError as exc:
            raise PBGenError(
                f"Path escapes Docker mount root {self.mount_root}: {host_path}"
            ) from exc
        return _CONTAINER_ROOT / relative
