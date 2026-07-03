from __future__ import annotations

import json
from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.schemas import BehaviorSurface, CommandExample, ExecutableTestSuite
from pbgen.testgen.model_backend import ModelTestGenerationBackend, StaticModelClient
from pbgen.testgen.output_normalization import normalize_observed_outputs
from pbgen.testgen.prompt_builder import TestGenerationPrompt as GenerationPrompt
from pbgen.testgen.test_writer import LocalHeuristicTestGenerationBackend


def test_stable_stderr_remains_exact() -> None:
    normalized = normalize_observed_outputs("", "invalid number: not-a-number\n")

    assert normalized.stderr.exact == "invalid number: not-a-number\n"
    assert normalized.stderr.contains == []
    assert normalized.provenance["stderr_normalized"] == "false"


def test_traceback_stderr_uses_portable_final_error_lines() -> None:
    stderr = (
        "Traceback (most recent call last):\n"
        '  File "/Users/example/project/tool.py", line 7, in <module>\n'
        "    main()\n"
        "FileNotFoundError: [Errno 2] No such file or directory: 'schema.json'\n"
    )

    normalized = normalize_observed_outputs("", stderr)

    assert normalized.stderr.exact is None
    assert normalized.stderr.contains == [
        "FileNotFoundError: [Errno 2] No such file or directory: 'schema.json'"
    ]
    assert normalized.provenance["stderr_normalized"] == "true"


def test_model_structured_generation_normalizes_observed_stderr(tmp_path: Path) -> None:
    executable = _write_traceback_gold(tmp_path / "gold" / "program")
    response = json.dumps(
        {
            "test_cases": [
                {
                    "test_id": "boom",
                    "args": ["boom"],
                    "behavior_category": "error-formatting",
                    "source_evidence": "negative path",
                }
            ]
        }
    )
    backend = ModelTestGenerationBackend(
        PBGenConfig(workspace_root=tmp_path, generation_backend="model"),
        client=StaticModelClient(response),
    )

    backend.generate_tests(_prompt(tmp_path, executable), tmp_path / "generated")
    suite = ExecutableTestSuite.model_validate(
        json.loads((tmp_path / "generated" / "test_cases_iteration_0.json").read_text())
    )

    assert suite.cases[0].expected_stderr.exact is None
    assert suite.cases[0].expected_stderr.contains == ["RuntimeError: synthetic failure"]
    assert suite.cases[0].provenance["stderr_normalized"] == "true"


def test_local_generation_normalizes_observed_stderr(tmp_path: Path) -> None:
    executable = _write_traceback_gold(tmp_path / "gold" / "program")
    surface = BehaviorSurface(
        task_id="demo",
        command_examples=[
            CommandExample(args=["boom"], source="docs", category="error-formatting")
        ],
    )

    LocalHeuristicTestGenerationBackend().generate_tests(
        _prompt(tmp_path, executable, surface=surface),
        tmp_path / "generated",
    )
    suite = ExecutableTestSuite.model_validate(
        json.loads((tmp_path / "generated" / "test_cases_iteration_0.json").read_text())
    )

    assert suite.cases[0].expected_stderr.exact is None
    assert suite.cases[0].expected_stderr.contains == ["RuntimeError: synthetic failure"]
    assert suite.cases[0].provenance["stderr_normalized"] == "true"


def _prompt(
    tmp_path: Path,
    executable: Path,
    *,
    surface: BehaviorSurface | None = None,
) -> GenerationPrompt:
    return GenerationPrompt(
        task_id="demo",
        behavior_surface=surface or BehaviorSurface(task_id="demo"),
        iteration=0,
        executable_path=executable,
    )


def _write_traceback_gold(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "raise RuntimeError('synthetic failure')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
