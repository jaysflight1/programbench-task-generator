"""Optional model-backed pytest generation backend."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Protocol, cast

from pbgen.config import PBGenConfig
from pbgen.errors import TestGenerationError
from pbgen.serialization import write_data
from pbgen.testgen.model_safety import ModelSafetyDiagnostic, validate_model_generated_pytest
from pbgen.testgen.prompt_builder import TestGenerationPrompt
from pbgen.testgen.test_writer import TestGenerationBackend


MODEL_PROMPT_VERSION = "model_backend_v1"
MAX_MODEL_TEST_FILES = 8
MAX_MODEL_TEST_SOURCE_CHARS = 80_000


@dataclass(frozen=True)
class ModelGenerationRequest:
    """One prompt submitted to the configured model provider."""

    prompt: str
    model: str | None
    temperature: float
    timeout_seconds: int
    max_output_chars: int


class ModelClient(Protocol):
    """Provider-neutral model client used by the model backend."""

    def generate(self, request: ModelGenerationRequest) -> str:
        """Return raw model text for one generation request."""


@dataclass(frozen=True)
class ExternalCommandModelClient:
    """Model client that delegates to an operator-provided command.

    The command receives the prompt on stdin and must write the model response
    to stdout. This keeps pbgen provider-neutral and avoids requiring API keys
    or network calls in tests.
    """

    command: list[str]

    def generate(self, request: ModelGenerationRequest) -> str:
        if not self.command:
            raise TestGenerationError(
                "Model backend selected, but no external model command is configured. "
                "Set PBGEN_MODEL_COMMAND, pass --model-command, or provide model_command "
                "in pbgen_task.yaml."
            )
        try:
            completed = subprocess.run(
                self.command,
                input=request.prompt,
                check=False,
                text=True,
                capture_output=True,
                timeout=request.timeout_seconds,
            )
        except OSError as exc:
            raise TestGenerationError(f"Could not start model command {self.command!r}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TestGenerationError(
                f"Model command timed out after {request.timeout_seconds} seconds."
            ) from exc
        if completed.returncode != 0:
            stderr = _clip(completed.stderr.strip(), 2_000)
            raise TestGenerationError(
                f"Model command exited with status {completed.returncode}: {stderr}"
            )
        if len(completed.stdout) > request.max_output_chars:
            raise TestGenerationError(
                f"Model response exceeded {request.max_output_chars} characters."
            )
        return completed.stdout


@dataclass(frozen=True)
class StaticModelClient:
    """In-memory model client for deterministic tests."""

    response: str
    requests: list[ModelGenerationRequest] = field(default_factory=list)

    def generate(self, request: ModelGenerationRequest) -> str:
        self.requests.append(request)
        return self.response


@dataclass(frozen=True)
class ModelGeneratedTest:
    """One generated pytest file returned by a model."""

    filename: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelTestGenerationBackend(TestGenerationBackend):
    """Generate pytest tests from a configured model provider."""

    prompt_version = MODEL_PROMPT_VERSION

    def __init__(
        self,
        config: PBGenConfig,
        *,
        client: ModelClient | None = None,
    ) -> None:
        self.config = config
        self.client = client or _client_from_config(config)

    def generate_tests(self, prompt: TestGenerationPrompt, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        request = ModelGenerationRequest(
            prompt=render_model_generation_prompt(prompt),
            model=self.config.model_name,
            temperature=self.config.model_temperature,
            timeout_seconds=self.config.model_timeout_seconds,
            max_output_chars=self.config.model_max_output_chars,
        )
        raw_response = self.client.generate(request)
        generated_tests = parse_model_generation_response(raw_response)
        diagnostics: list[dict[str, Any]] = []
        accepted_paths: list[Path] = []

        if len(generated_tests) > MAX_MODEL_TEST_FILES:
            diagnostics.append(
                {
                    "severity": "high",
                    "reason": "too_many_files",
                    "message": f"Model returned {len(generated_tests)} files.",
                }
            )
            generated_tests = generated_tests[:MAX_MODEL_TEST_FILES]

        for index, generated in enumerate(generated_tests):
            safety_report = validate_model_generated_pytest(generated.source)
            if not safety_report.ok:
                diagnostics.append(_rejected_diagnostic(generated, safety_report.diagnostics))
                continue
            path = _next_model_path(output_dir, prompt.iteration, index, generated.filename)
            path.write_text(generated.source.rstrip() + "\n", encoding="utf-8")
            accepted_paths.append(path)
            diagnostics.append(
                {
                    "filename": path.name,
                    "source_filename": generated.filename,
                    "accepted": True,
                    "metadata": generated.metadata,
                }
            )

        _write_generation_diagnostics(output_dir, prompt.iteration, diagnostics)
        if not accepted_paths:
            raise TestGenerationError(
                "Model backend produced no safe pytest files. "
                f"See {_diagnostic_path(output_dir, prompt.iteration)} for rejection details."
            )
        return accepted_paths


def render_model_generation_prompt(prompt: TestGenerationPrompt) -> str:
    """Render the canonical provider-neutral prompt for model test generation."""

    payload = {
        "task_id": prompt.task_id,
        "iteration": prompt.iteration,
        "behavior_surface": prompt.behavior_surface.model_dump(mode="json"),
        "coverage_gaps": [gap.model_dump(mode="json") for gap in prompt.coverage_gaps],
        "existing_test_names": prompt.existing_test_names,
    }
    return (
        "You are generating hidden pytest tests for a ProgramBench-style Python CLI task.\n"
        "Return JSON with a top-level 'tests' array. Each item must include "
        "'filename' and 'content'. The content must be a complete pytest file.\n\n"
        "Hard safety requirements:\n"
        "- Use only the executable path from os.environ['PBGEN_EXECUTABLE'].\n"
        "- Do not import the target repository package or source modules.\n"
        "- Do not use shell=True, network access, package installation, deletion, "
        "filesystem mutation, absolute local paths, or '..' path traversal.\n"
        "- Prefer subprocess.run([executable, *args], check=False, text=True, "
        "capture_output=True, cwd=Path(__file__).parent).\n"
        "- Assert exact returncode and meaningful stdout/stderr behavior.\n\n"
        "Task payload:\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
    )


def parse_model_generation_response(raw_response: str) -> list[ModelGeneratedTest]:
    """Parse model JSON or fenced pytest code into generated-test records."""

    stripped = raw_response.strip()
    if not stripped:
        raise TestGenerationError("Model response was empty.")
    parsed = _parse_json_response(stripped)
    if parsed is not None:
        return parsed
    fenced = _parse_fenced_code(stripped)
    if fenced:
        return fenced
    if "def test_" in stripped:
        return [ModelGeneratedTest(filename="test_model_generated.py", source=stripped)]
    raise TestGenerationError(
        "Model response did not contain supported JSON, fenced Python, or pytest source."
    )


def _parse_json_response(text: str) -> list[ModelGeneratedTest] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    tests_data: Any
    if isinstance(data, dict) and isinstance(data.get("tests"), list):
        tests_data = data["tests"]
    elif isinstance(data, dict) and any(key in data for key in ("content", "code", "source")):
        tests_data = [data]
    elif isinstance(data, list):
        tests_data = data
    else:
        raise TestGenerationError("Model JSON must contain a tests array or test content.")

    tests: list[ModelGeneratedTest] = []
    for index, item in enumerate(tests_data):
        if not isinstance(item, dict):
            raise TestGenerationError(f"Model tests[{index}] must be an object.")
        filename = item.get("filename") or item.get("path") or f"test_model_generated_{index}.py"
        content = item.get("content") or item.get("code") or item.get("source")
        if not isinstance(filename, str) or not filename:
            raise TestGenerationError(f"Model tests[{index}].filename must be a non-empty string.")
        if not isinstance(content, str) or not content.strip():
            raise TestGenerationError(f"Model tests[{index}].content must be non-empty.")
        metadata = {key: value for key, value in item.items() if key not in {"content", "code", "source"}}
        tests.append(
            ModelGeneratedTest(
                filename=_safe_model_filename(filename, index),
                source=content,
                metadata=metadata,
            )
        )
    return tests


def _parse_fenced_code(text: str) -> list[ModelGeneratedTest]:
    tests: list[ModelGeneratedTest] = []
    for index, match in enumerate(re.finditer(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)):
        source = match.group(1).strip()
        if "def test_" not in source:
            continue
        tests.append(
            ModelGeneratedTest(
                filename=f"test_model_generated_{index}.py",
                source=source,
            )
        )
    return tests


def _client_from_config(config: PBGenConfig) -> ModelClient:
    provider = config.model_provider.strip().lower()
    if provider in {"external-command", "command", "external_command"}:
        return ExternalCommandModelClient(config.model_command or [])
    raise TestGenerationError(
        f"Unsupported model provider {config.model_provider!r}. "
        "Supported provider: external-command."
    )


def _safe_model_filename(filename: str, index: int) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    if not name.startswith("test_"):
        name = f"test_{name}"
    if not name.endswith(".py"):
        name = f"{name}.py"
    return name or f"test_model_generated_{index}.py"


def _next_model_path(output_dir: Path, iteration: int, index: int, filename: str) -> Path:
    stem = Path(filename).stem or "test_model_generated"
    candidate = output_dir / f"{stem}_iter_{iteration}_{index}.py"
    if not candidate.exists():
        return candidate
    suffix = 1
    while True:
        path = output_dir / f"{stem}_iter_{iteration}_{index}_{suffix:02d}.py"
        if not path.exists():
            return path
        suffix += 1


def _write_generation_diagnostics(
    output_dir: Path,
    iteration: int,
    diagnostics: list[dict[str, Any]],
) -> None:
    path = _diagnostic_path(output_dir, iteration)
    write_data(
        path,
        {
            "iteration": iteration,
            "prompt_version": MODEL_PROMPT_VERSION,
            "diagnostics": diagnostics,
        },
    )


def _diagnostic_path(output_dir: Path, iteration: int) -> Path:
    return output_dir.parent / "reports" / f"model_generation_iteration_{iteration}.json"


def _rejected_diagnostic(
    generated: ModelGeneratedTest,
    issues: tuple[ModelSafetyDiagnostic, ...],
) -> dict[str, Any]:
    return {
        "filename": generated.filename,
        "accepted": False,
        "metadata": generated.metadata,
        "issues": [
            {
                "rule_id": issue.rule_id,
                "severity": issue.severity,
                "message": issue.message,
                "line": issue.line,
                "column": issue.column,
            }
            for issue in issues
        ],
    }


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def model_generated_tests_from_json(data: object) -> list[ModelGeneratedTest]:
    """Typed helper retained for tests and future provider-specific clients."""

    return cast(list[ModelGeneratedTest], _parse_json_response(json.dumps(data)))
