from __future__ import annotations

import json
from pathlib import Path

import pytest

from pbgen.config import PBGenConfig
from pbgen.errors import TestGenerationError as GenerationError
from pbgen.schemas import BehaviorCommand, BehaviorSurface
from pbgen.schemas import ExecutableTestSuite
from pbgen.testgen.backends import create_test_generation_backend
from pbgen.testgen.model_backend import (
    ModelTestGenerationBackend,
    StaticModelClient,
    parse_model_generation_response,
    parse_model_test_case_response,
    render_model_generation_prompt,
)
from pbgen.testgen.prompt_builder import TestGenerationPrompt as GenerationPrompt


SAFE_MODEL_TEST = '''"""Generated model-backed tests."""

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


def test_model_help() -> None:
    result = run_cmd(["--help"])
    assert result.returncode == 0
    assert "Usage" in result.stdout
'''


def test_model_backend_writes_safe_fake_model_output(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "tests": [
                {
                    "filename": "help_behavior.py",
                    "content": SAFE_MODEL_TEST,
                    "behavior_category": "help",
                    "intended_command": ["--help"],
                }
            ]
        }
    )
    client = StaticModelClient(response)
    backend = ModelTestGenerationBackend(
        PBGenConfig(workspace_root=tmp_path, generation_backend="model"),
        client=client,
    )

    paths = backend.generate_tests(_prompt(tmp_path), tmp_path / "generated_tests")

    assert [path.name for path in paths] == ["test_help_behavior_iter_0_0.py"]
    assert "test_model_help" in paths[0].read_text(encoding="utf-8")
    diagnostics = json.loads(
        (tmp_path / "reports" / "model_generation_iteration_0.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostics["diagnostics"][0]["accepted"] is True
    assert "PBGEN_EXECUTABLE" in client.requests[0].prompt


def test_model_backend_writes_structured_cases_after_gold_observation(tmp_path: Path) -> None:
    executable = _write_fake_gold(tmp_path / "gold" / "executable" / "program")
    response = json.dumps(
        {
            "test_cases": [
                {
                    "test_id": "help case",
                    "args": ["--help"],
                    "behavior_category": "help",
                    "source_evidence": "help text",
                },
                {
                    "test_id": "help duplicate",
                    "args": ["--help"],
                    "behavior_category": "help",
                    "source_evidence": "duplicate",
                },
            ]
        }
    )
    backend = ModelTestGenerationBackend(
        PBGenConfig(workspace_root=tmp_path, generation_backend="model"),
        client=StaticModelClient(response),
    )

    paths = backend.generate_tests(_prompt(tmp_path, executable), tmp_path / "generated_tests")
    suite = ExecutableTestSuite.model_validate(
        json.loads((tmp_path / "generated_tests" / "test_cases_iteration_0.json").read_text())
    )
    diagnostics = json.loads(
        (tmp_path / "reports" / "model_generation_iteration_0.json").read_text()
    )
    request_metadata = json.loads(
        (tmp_path / "reports" / "model_request_iteration_0.json").read_text()
    )

    assert [path.name for path in paths] == ["test_behavior_iter_0.py"]
    assert len(suite.cases) == 1
    assert suite.cases[0].expected_exit_code == 0
    assert suite.cases[0].expected_stdout.exact == "Usage: fake [--help]\n"
    assert suite.cases[0].behavior_category == "help"
    assert suite.cases[0].provenance["gold_observed"] == "true"
    assert diagnostics["behavior_category_counts"] == {"help": 1}
    assert (tmp_path / "reports" / "model_prompt_iteration_0.txt").exists()
    assert (tmp_path / "reports" / "model_response_iteration_0.json").exists()
    assert len(request_metadata["prompt_sha256"]) == 64
    assert len(request_metadata["response_sha256"]) == 64
    assert any(
        item.get("reason") == "duplicate structured test case"
        for item in diagnostics["diagnostics"]
    )


def test_model_backend_rejects_unsafe_structured_cases_before_writing(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "test_cases": [
                {
                    "args": ["delete", "all"],
                    "expected_exit_code": 0,
                    "source_evidence": "unsafe docs",
                }
            ]
        }
    )
    backend = ModelTestGenerationBackend(
        PBGenConfig(workspace_root=tmp_path, generation_backend="model"),
        client=StaticModelClient(response),
    )

    with pytest.raises(GenerationError, match="no safe structured test cases"):
        backend.generate_tests(_prompt(tmp_path), tmp_path / "generated_tests")

    assert not list((tmp_path / "generated_tests").glob("*.py"))
    diagnostics = json.loads(
        (tmp_path / "reports" / "model_generation_iteration_0.json").read_text()
    )
    assert diagnostics["diagnostics"][0]["accepted"] is False
    assert "unsafe model test command" in diagnostics["diagnostics"][0]["reason"]


def test_model_backend_rejects_unsafe_output_before_writing(tmp_path: Path) -> None:
    unsafe_source = """
import subprocess


def test_bad() -> None:
    subprocess.run(["rm", "-rf", "/tmp/example"], check=False)
"""
    client = StaticModelClient(json.dumps({"tests": [{"filename": "bad.py", "content": unsafe_source}]}))
    backend = ModelTestGenerationBackend(
        PBGenConfig(workspace_root=tmp_path, generation_backend="model"),
        client=client,
    )

    with pytest.raises(GenerationError, match="no safe pytest files"):
        backend.generate_tests(_prompt(tmp_path), tmp_path / "generated_tests")

    assert not list((tmp_path / "generated_tests").glob("*.py"))
    diagnostics = json.loads(
        (tmp_path / "reports" / "model_generation_iteration_0.json").read_text(
            encoding="utf-8"
        )
    )
    rejected = diagnostics["diagnostics"][0]
    assert rejected["accepted"] is False
    assert {issue["rule_id"] for issue in rejected["issues"]} >= {
        "arbitrary_subprocess_command",
        "absolute_local_path",
    }


def test_model_backend_can_require_structured_cases(tmp_path: Path) -> None:
    client = StaticModelClient(json.dumps({"tests": [{"filename": "safe.py", "content": SAFE_MODEL_TEST}]}))
    backend = ModelTestGenerationBackend(
        PBGenConfig(
            workspace_root=tmp_path,
            generation_backend="model",
            model_require_structured_cases=True,
        ),
        client=client,
    )

    with pytest.raises(GenerationError, match="requires structured JSON"):
        backend.generate_tests(_prompt(tmp_path), tmp_path / "generated_tests")

    diagnostics = json.loads(
        (tmp_path / "reports" / "model_generation_iteration_0.json").read_text()
    )
    assert diagnostics["diagnostics"][0]["accepted"] is False
    assert "structured test_cases JSON is required" in diagnostics["diagnostics"][0]["reason"]


def test_model_parser_accepts_fenced_python() -> None:
    parsed = parse_model_generation_response(f"Here is a test:\n```python\n{SAFE_MODEL_TEST}\n```")

    assert len(parsed) == 1
    assert parsed[0].filename == "test_model_generated_0.py"
    assert "test_model_help" in parsed[0].source


def test_model_parser_accepts_structured_cases() -> None:
    parsed = parse_model_test_case_response(
        json.dumps({"test_cases": [{"args": ["--help"], "source_evidence": "docs"}]})
    )

    assert len(parsed) == 1
    assert parsed[0].data["args"] == ["--help"]


def test_model_factory_requires_explicit_model_command(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path, generation_backend="model", model_command=None)

    backend = create_test_generation_backend(config)

    with pytest.raises(GenerationError, match="no external model command"):
        backend.generate_tests(_prompt(tmp_path), tmp_path / "generated_tests")


def test_rendered_model_prompt_contains_surface_and_constraints(tmp_path: Path) -> None:
    prompt_text = render_model_generation_prompt(
        _prompt(tmp_path).model_copy(
            update={
                "task_spec": {"language": "python", "build_system": "python-package"},
                "existing_test_names": ["test_existing"],
                "previous_generation_diagnostics": [
                    {"accepted": False, "reason": "duplicate structured test case"}
                ],
                "previous_behavior_category_counts": {"help": 2},
            }
        )
    )

    assert "Return JSON" in prompt_text
    assert "PBGEN_EXECUTABLE" in prompt_text
    assert "demo" in prompt_text
    assert "--help" in prompt_text
    assert "python-package" in prompt_text
    assert "test_existing" in prompt_text
    assert "duplicate structured test case" in prompt_text
    assert "malformed-input" in prompt_text
    assert "filesystem-output" in prompt_text
    assert "error-formatting" in prompt_text


def _prompt(tmp_path: Path, executable_path: Path | None = None) -> GenerationPrompt:
    return GenerationPrompt(
        task_id="demo",
        behavior_surface=BehaviorSurface(
            task_id="demo",
            commands=[BehaviorCommand(command="--help", category="help")],
            global_flags=["--help"],
        ),
        coverage_gaps=[],
        iteration=0,
        executable_path=executable_path or tmp_path / "gold" / "executable" / "program",
    )


def _write_fake_gold(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys

if sys.argv[1:] == ["--help"]:
    print("Usage: fake [--help]")
    raise SystemExit(0)
print("unknown", file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
