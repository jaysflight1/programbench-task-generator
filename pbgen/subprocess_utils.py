"""Centralized subprocess execution with safe defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """Structured subprocess result used by pipeline stages."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: Path | None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    """Pluggable command runner used by local and sandboxed execution paths."""

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = 60,
    ) -> CommandResult:
        """Run one command and return a structured result."""


class LocalCommandRunner:
    """Command runner that executes directly on the host."""

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = 60,
    ) -> CommandResult:
        return run_command(
            args,
            cwd=cwd,
            env=env,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
        )


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    timeout_seconds: int | None = 60,
) -> CommandResult:
    """Run a command through the single framework subprocess wrapper."""

    completed = subprocess.run(
        args,
        input=stdin,
        check=False,
        text=True,
        capture_output=True,
        cwd=cwd,
        env=env,
        timeout=timeout_seconds,
    )
    return CommandResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        cwd=cwd,
    )
