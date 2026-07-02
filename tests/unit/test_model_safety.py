from __future__ import annotations

import pytest

from pbgen.testgen.model_safety import (
    ModelSafetyPolicy,
    validate_model_generated_pytest,
)


SAFE_GENERATED_TEST = '''"""Generated behavioral tests for the cleanroom executable."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    executable = os.environ["PBGEN_EXECUTABLE"]
    return subprocess.run(
        [executable, *args],
        check=False,
        text=True,
        capture_output=True,
        cwd=Path(__file__).parent,
    )


@pytest.mark.parametrize("args", [["--help"], ["calc", "1", "2"]])
def test_behavior(args: list[str]) -> None:
    result = run_cmd(args)
    assert result.returncode == 0
    assert isinstance(result.stdout, str)
    assert result.stderr == ""


def test_typing_import_is_used() -> None:
    value: Any = "ok"
    assert value == "ok"
'''


def test_safe_generated_pytest_style_passes() -> None:
    report = validate_model_generated_pytest(SAFE_GENERATED_TEST)

    assert report.ok
    assert report.diagnostics == ()


def test_syntax_error_returns_structured_diagnostic() -> None:
    report = validate_model_generated_pytest("def test_bad(:\n    pass\n")

    assert not report.ok
    assert report.has_rule("syntax_error")
    assert report.diagnostics[0].line == 1


@pytest.mark.parametrize(
    ("source", "rule_id"),
    [
        (
            "from calculator import main\n\n"
            "def test_imports_target() -> None:\n"
            "    assert main is not None\n",
            "target_import",
        ),
        (
            "import subprocess\n\n"
            "def test_shell() -> None:\n"
            "    subprocess.run('echo unsafe', shell=True)\n",
            "shell_true",
        ),
        (
            "import os\n\n"
            "def test_system() -> None:\n"
            "    os.system('echo unsafe')\n",
            "os_process_call",
        ),
        (
            "import subprocess\n\n"
            "def test_python_root() -> None:\n"
            "    subprocess.run(['python', '-m', 'pytest'], check=False)\n",
            "arbitrary_subprocess_command",
        ),
        (
            "import socket\n\n"
            "def test_socket() -> None:\n"
            "    assert socket is not None\n",
            "network_import",
        ),
        (
            "from pathlib import Path\n\n"
            "def test_write() -> None:\n"
            "    Path('out.txt').write_text('x')\n",
            "filesystem_mutation",
        ),
        (
            "import os\n\n"
            "def test_remove() -> None:\n"
            "    os.remove('out.txt')\n",
            "filesystem_mutation",
        ),
        (
            "def test_absolute_path() -> None:\n"
            "    assert '/Users/example/project' != ''\n",
            "absolute_local_path",
        ),
        (
            "def test_traversal() -> None:\n"
            "    assert '../fixture.txt' != ''\n",
            "path_traversal",
        ),
        (
            "import os\n"
            "import subprocess\n\n"
            "def test_install_token() -> None:\n"
            "    executable = os.environ['PBGEN_EXECUTABLE']\n"
            "    subprocess.run([executable, 'install'], check=False)\n",
            "shellish_command_token",
        ),
        (
            "def test_url() -> None:\n"
            "    assert 'https://example.com/input' != ''\n",
            "network_url",
        ),
        (
            "def test_open_write() -> None:\n"
            "    open('out.txt', 'w').write('x')\n",
            "filesystem_mutation",
        ),
    ],
)
def test_representative_unsafe_cases_fail(source: str, rule_id: str) -> None:
    report = validate_model_generated_pytest(
        source,
        ModelSafetyPolicy(target_import_roots=frozenset({"calculator"})),
    )

    assert not report.ok
    assert report.has_rule(rule_id), report.diagnostics
