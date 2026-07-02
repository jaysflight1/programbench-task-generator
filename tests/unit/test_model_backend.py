from __future__ import annotations

import json
from pathlib import Path

import pytest

from pbgen.config import PBGenConfig
from pbgen.errors import TestGenerationError as GenerationError
from pbgen.schemas import BehaviorCommand, BehaviorSurface
from pbgen.testgen.backends import create_test_generation_backend
from pbgen.testgen.model_backend import (
    ModelTestGenerationBackend,
    StaticModelClient,
    parse_model_generation_response,
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


def test_model_parser_accepts_fenced_python() -> None:
    parsed = parse_model_generation_response(f"Here is a test:\n```python\n{SAFE_MODEL_TEST}\n```")

    assert len(parsed) == 1
    assert parsed[0].filename == "test_model_generated_0.py"
    assert "test_model_help" in parsed[0].source


def test_model_factory_requires_explicit_model_command(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path, generation_backend="model", model_command=None)

    backend = create_test_generation_backend(config)

    with pytest.raises(GenerationError, match="no external model command"):
        backend.generate_tests(_prompt(tmp_path), tmp_path / "generated_tests")


def test_rendered_model_prompt_contains_surface_and_constraints(tmp_path: Path) -> None:
    prompt_text = render_model_generation_prompt(_prompt(tmp_path))

    assert "Return JSON" in prompt_text
    assert "PBGEN_EXECUTABLE" in prompt_text
    assert "demo" in prompt_text
    assert "--help" in prompt_text


def _prompt(tmp_path: Path) -> GenerationPrompt:
    return GenerationPrompt(
        task_id="demo",
        behavior_surface=BehaviorSurface(
            task_id="demo",
            commands=[BehaviorCommand(command="--help", category="help")],
            global_flags=["--help"],
        ),
        coverage_gaps=[],
        iteration=0,
        executable_path=tmp_path / "gold" / "executable" / "program",
    )
