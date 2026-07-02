"""Generated behavioral tests for the cleanroom executable."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    executable = os.environ["PBGEN_EXECUTABLE"]
    return subprocess.run(
        [executable, *args],
        check=False,
        text=True,
        capture_output=True,
        cwd=Path(__file__).parent,
    )


def test_iter_0_0_version() -> None:
    result = run_cmd(['--version'])
    assert result.returncode == 0
    assert result.stdout == 'pbcalc 1.0\n'
    assert result.stderr == ''


def test_iter_0_1_add_2_5_1_25() -> None:
    result = run_cmd(['add', '2.5', '-1.25', '4'])
    assert result.returncode == 0
    assert result.stdout == '5.25\n'
    assert result.stderr == ''


def test_iter_0_2_add_1_2() -> None:
    result = run_cmd(['add', '1', '2'])
    assert result.returncode == 0
    assert result.stdout == '3\n'
    assert result.stderr == ''


def test_iter_0_3_mul_1_2() -> None:
    result = run_cmd(['mul', '1', '2'])
    assert result.returncode == 0
    assert result.stdout == '2\n'
    assert result.stderr == ''


def test_iter_0_4_stats_1_2() -> None:
    result = run_cmd(['stats', '1', '2'])
    assert result.returncode == 0
    assert result.stdout == 'count=2\nsum=3\nmean=1.5\nmin=1\nmax=2\n'
    assert result.stderr == ''


def test_iter_0_5_help() -> None:
    result = run_cmd(['--help'])
    assert result.returncode == 0
    assert result.stdout == 'Usage: pbcalc COMMAND [ARGS]\n\nCommands:\n  add NUM...     Sum decimal numbers.\n  mul NUM...     Multiply decimal numbers.\n  stats NUM...   Print count, sum, mean, min, and max.\n\nOptions:\n  -h, --help     Show help.\n  --version      Show version.\n'
    assert result.stderr == ''


def test_iter_0_6_h() -> None:
    result = run_cmd(['-h'])
    assert result.returncode == 0
    assert result.stdout == 'Usage: pbcalc COMMAND [ARGS]\n\nCommands:\n  add NUM...     Sum decimal numbers.\n  mul NUM...     Multiply decimal numbers.\n  stats NUM...   Print count, sum, mean, min, and max.\n\nOptions:\n  -h, --help     Show help.\n  --version      Show version.\n'
    assert result.stderr == ''


def test_iter_0_7_add_1() -> None:
    result = run_cmd(['add', '1'])
    assert result.returncode == 0
    assert result.stdout == '1\n'
    assert result.stderr == ''


def test_iter_0_8_mul_1() -> None:
    result = run_cmd(['mul', '1'])
    assert result.returncode == 0
    assert result.stdout == '1\n'
    assert result.stderr == ''


def test_iter_0_9_stats_1() -> None:
    result = run_cmd(['stats', '1'])
    assert result.returncode == 0
    assert result.stdout == 'count=1\nsum=1\nmean=1\nmin=1\nmax=1\n'
    assert result.stderr == ''


def test_iter_0_10_pbcalc() -> None:
    result = run_cmd(['pbcalc'])
    assert result.returncode == 2
    assert result.stdout == ''
    assert result.stderr == 'at least one number is required\n'

