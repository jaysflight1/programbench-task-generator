from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys

from pbgen.config import PBGenConfig
from pbgen.schemas import (
    BehaviorCommand,
    BehaviorSurface,
    CommandExample,
    CoverageGap,
    ExecutableTestSuite,
)
from pbgen.testgen.example_extractor import extract_behavior_hints
from pbgen.testgen.prompt_builder import TestGenerationPrompt as GenerationPrompt
from pbgen.testgen.test_writer import AgenticTestGenerationBackend, LocalHeuristicTestGenerationBackend


def test_extract_behavior_hints_parses_docs_help_and_inline_examples() -> None:
    text = """
Usage: program COMMAND [ARGS]

Commands:
  add NUM...           Sum numbers.
  export --json NAME   Print a named record.

Options:
  -h, --help           Show help.

Examples:
$ program add 1 2

```sh
$ program --version
program export --json demo
```

Inline forms include `program add 3 4` and `stats NUM...`.
"""

    hints = extract_behavior_hints(text, {"program"})

    assert ("add", "1", "2") in {example.argv for example in hints.examples}
    assert ("--version",) in {example.argv for example in hints.examples}
    assert ("export", "--json", "demo") in {example.argv for example in hints.examples}
    assert {"-h", "--help", "--json"}.issubset(set(hints.flags))
    assert any(
        usage.name == "add" and usage.signature == ("NUM...",)
        for usage in hints.command_usages
    )
    assert any(
        usage.name == "stats" and usage.signature == ("NUM...",)
        for usage in hints.command_usages
    )


def test_backend_records_gold_outputs_and_appends_iteration_files(tmp_path: Path) -> None:
    executable = _write_fake_gold(tmp_path / "gold_program")
    output_dir = tmp_path / "generated_tests"
    surface = BehaviorSurface(
        task_id="calc",
        commands=[
            BehaviorCommand(command="--help", category="help"),
            BehaviorCommand(command="calc", category="subcommand", notes="NUM...     Add numbers"),
        ],
        global_flags=["--help"],
        command_examples=[
            CommandExample(args=["calc", "3", "4"], source="docs", category="example"),
        ],
    )
    prompt = GenerationPrompt(
        task_id="calc",
        behavior_surface=surface,
        coverage_gaps=[
            CoverageGap(
                file_path="calculator.py",
                function_name="parse_numbers",
                reason="uncovered invalid numeric parser branch",
                priority=1.0,
            )
        ],
        iteration=0,
        executable_path=executable,
    )

    backend = LocalHeuristicTestGenerationBackend()
    first_paths = backend.generate_tests(prompt, output_dir)
    second_paths = backend.generate_tests(prompt, output_dir)

    assert first_paths[0].name == "test_behavior_iter_0.py"
    assert second_paths[0].name == "test_behavior_iter_0_01.py"
    assert first_paths[0] != second_paths[0]
    assert (output_dir / "test_cases_iteration_0.json").exists()
    assert (output_dir / "test_cases_iteration_0_01.json").exists()
    assert (output_dir / "test_cases_iteration_0_artifact.json").exists()

    generated = first_paths[0].read_text(encoding="utf-8")
    assert "assert result.stdout == '7\\n'" in generated
    assert "assert result.stderr == 'invalid number: not-a-number\\n'" in generated
    assert "assert result.returncode == 2" in generated
    suite = ExecutableTestSuite.model_validate(
        json.loads((output_dir / "test_cases_iteration_0.json").read_text(encoding="utf-8"))
    )
    assert suite.task_id == "calc"
    assert suite.iteration == 0
    assert suite.generator == "local_agentic_v1"
    assert any(case.args == ["calc", "3", "4"] for case in suite.cases)
    assert any(
        case.expected_stderr.exact == "invalid number: not-a-number\n"
        for case in suite.cases
    )
    diagnostics = json.loads(
        (tmp_path / "reports" / "agentic_generation_iteration_0.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostics["accepted"] == len(suite.cases)
    assert diagnostics["diagnostics"][0]["revision"] == (
        "expected behavior replaced with observed gold behavior"
    )

    env = os.environ.copy()
    env["PBGEN_EXECUTABLE"] = str(executable)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(first_paths[0]), "-q"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_backend_filters_examples_with_execution_policy(tmp_path: Path) -> None:
    executable = _write_fake_gold(tmp_path / "gold_program")
    output_dir = tmp_path / "generated_tests"
    surface = BehaviorSurface(
        task_id="calc",
        commands=[BehaviorCommand(command="--help", category="help")],
        global_flags=["--help"],
        command_examples=[
            CommandExample(args=["--help"], source="docs", category="help"),
            CommandExample(args=["delete", "all"], source="docs", category="example"),
        ],
    )
    prompt = GenerationPrompt(
        task_id="calc",
        behavior_surface=surface,
        iteration=0,
        executable_path=executable,
        safe_command_deny_patterns=[r"delete"],
    )

    paths = LocalHeuristicTestGenerationBackend().generate_tests(prompt, output_dir)
    suite = ExecutableTestSuite.model_validate(
        json.loads((output_dir / "test_cases_iteration_0.json").read_text(encoding="utf-8"))
    )

    assert paths
    assert all("delete" not in case.args for case in suite.cases)
    assert any(case.args == ["--help"] for case in suite.cases)


def test_agentic_backend_generates_stdin_file_env_and_config_cases(tmp_path: Path) -> None:
    executable = _write_context_gold(tmp_path / "context_program")
    output_dir = tmp_path / "generated_tests"
    surface = BehaviorSurface(
        task_id="context",
        commands=[
            BehaviorCommand(command="stdin", category="subcommand"),
            BehaviorCommand(command="read", category="subcommand"),
        ],
        global_flags=["--config"],
        stdin_supported=True,
        file_inputs=["input.txt"],
        config_files=["config.json"],
        env_vars=["PBGEN_MODE"],
    )
    prompt = GenerationPrompt(
        task_id="context",
        behavior_surface=surface,
        iteration=0,
        executable_path=executable,
    )

    AgenticTestGenerationBackend(PBGenConfig(workspace_root=tmp_path)).generate_tests(
        prompt,
        output_dir,
    )
    suite = ExecutableTestSuite.model_validate(
        json.loads((output_dir / "test_cases_iteration_0.json").read_text(encoding="utf-8"))
    )
    stdin_case = next(case for case in suite.cases if case.args == ["stdin"] and case.stdin)
    file_case = next(case for case in suite.cases if case.args == ["read", "input.txt"])
    env_case = next(case for case in suite.cases if case.behavior_category == "env")
    config_case = next(case for case in suite.cases if case.behavior_category == "config")

    assert stdin_case.stdin
    assert stdin_case.expected_stdout.exact == "stdin=sample stdin\nsecond line\n"
    assert file_case.fixture_files == {"input.txt": "sample file payload 0\n"}
    assert file_case.expected_stdout.exact == "file=sample file payload 0\n"
    assert env_case.env == {"PBGEN_MODE": "pbgen-sample-value"}
    assert env_case.expected_stdout.exact == "env=pbgen-sample-value\n"
    assert config_case.fixture_files == {"config.json": '{"mode": "sample"}\n'}
    assert config_case.expected_stdout.exact == 'config={"mode": "sample"}\n'


def test_agentic_backend_honors_large_candidate_budget(tmp_path: Path) -> None:
    executable = _write_echo_gold(tmp_path / "echo_program")
    output_dir = tmp_path / "generated_tests"
    examples = [
        CommandExample(args=["case", str(index)], source="docs", category="example")
        for index in range(240)
    ]
    surface = BehaviorSurface(task_id="bulk", command_examples=examples)
    prompt = GenerationPrompt(
        task_id="bulk",
        behavior_surface=surface,
        iteration=0,
        executable_path=executable,
    )

    AgenticTestGenerationBackend(
        PBGenConfig(workspace_root=tmp_path, agentic_candidate_budget=200)
    ).generate_tests(prompt, output_dir)
    suite = ExecutableTestSuite.model_validate(
        json.loads((output_dir / "test_cases_iteration_0.json").read_text(encoding="utf-8"))
    )

    assert len(suite.cases) == 200
    assert suite.cases[0].args == ["case", "0"]
    assert suite.cases[-1].args == ["case", "199"]


def _write_fake_gold(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if argv == ["--help"]:
        print("Usage: calc NUM...")
        return 0
    if argv[:1] == ["calc"]:
        values = argv[1:]
        if values == ["not-a-number"]:
            print("invalid number: not-a-number", file=sys.stderr)
            return 2
        print(int(sum(float(value) for value in values)))
        return 0
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_context_gold(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys


def main(argv: list[str]) -> int:
    if argv == ["stdin"]:
        print(f"stdin={sys.stdin.read().rstrip()}")
        return 0
    if argv == ["read", "input.txt"]:
        print(f"file={Path('input.txt').read_text(encoding='utf-8').rstrip()}")
        return 0
    if argv == []:
        print(f"env={os.environ.get('PBGEN_MODE', '')}")
        return 0
    if argv == ["--config", "config.json"]:
        print(f"config={Path('config.json').read_text(encoding='utf-8').rstrip()}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_echo_gold(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys

print(" ".join(sys.argv[1:]))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
