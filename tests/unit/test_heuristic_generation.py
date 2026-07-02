from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from pbgen.schemas import BehaviorCommand, BehaviorSurface, CommandExample, CoverageGap
from pbgen.testgen.example_extractor import extract_behavior_hints
from pbgen.testgen.prompt_builder import TestGenerationPrompt as GenerationPrompt
from pbgen.testgen.test_writer import LocalHeuristicTestGenerationBackend


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

    generated = first_paths[0].read_text(encoding="utf-8")
    assert "assert result.stdout == '7\\n'" in generated
    assert "assert result.stderr == 'invalid number: not-a-number\\n'" in generated
    assert "assert result.returncode == 2" in generated

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
