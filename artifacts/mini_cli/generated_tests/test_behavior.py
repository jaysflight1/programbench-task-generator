"""Generated behavioral tests for the cleanroom executable."""

from __future__ import annotations

import os
import subprocess


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    executable = os.environ["PBGEN_EXECUTABLE"]
    return subprocess.run([executable, *args], check=False, text=True, capture_output=True)



def test_help_lists_supported_commands() -> None:
    result = run_cmd(["--help"])
    assert result.returncode == 0
    assert "Usage: pbcalc COMMAND [ARGS]" in result.stdout
    assert "add NUM...     Sum decimal numbers." in result.stdout
    assert "stats NUM...   Print count, sum, mean, min, and max." in result.stdout



def test_version_reports_program_identity() -> None:
    result = run_cmd(["--version"])
    assert result.returncode == 0
    assert "pbcalc 1.0" in result.stdout



def test_invalid_number_returns_clear_error() -> None:
    result = run_cmd(["add", "2", "not-a-number"])
    assert result.returncode != 0
    assert "invalid number: not-a-number" in result.stderr

