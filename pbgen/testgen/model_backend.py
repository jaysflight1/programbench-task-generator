"""Optional model-backed pytest generation backend."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Protocol, cast

from pbgen.config import PBGenConfig
from pbgen.errors import TestGenerationError
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput
from pbgen.serialization import write_data
from pbgen.security import is_command_allowed
from pbgen.testgen.model_safety import ModelSafetyDiagnostic, validate_model_generated_pytest
from pbgen.testgen.output_normalization import apply_observed_outputs
from pbgen.testgen.prompt_builder import TestGenerationPrompt
from pbgen.testgen.test_writer import TestGenerationBackend, write_executable_test_suite


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


@dataclass(frozen=True)
class ModelGeneratedCaseProposal:
    """One structured executable test-case proposal from a model."""

    data: dict[str, Any]
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
        structured_cases = parse_model_test_case_response(raw_response)
        if structured_cases:
            return self._generate_structured_tests(prompt, output_dir, structured_cases)
        generated_tests = parse_model_generation_response(raw_response)
        return self._generate_legacy_pytest_tests(prompt, output_dir, generated_tests)

    def _generate_structured_tests(
        self,
        prompt: TestGenerationPrompt,
        output_dir: Path,
        proposals: list[ModelGeneratedCaseProposal],
    ) -> list[Path]:
        diagnostics: list[dict[str, Any]] = []
        accepted_cases: list[ExecutableTestCase] = []
        seen_signatures: set[str] = set()
        executable_path = prompt.executable_path if prompt.executable_path and prompt.executable_path.exists() else None
        for index, proposal in enumerate(proposals):
            try:
                case = _case_from_model_proposal(prompt, proposal, index)
                _validate_model_case(prompt, case)
                if executable_path is not None:
                    case = _observe_case_on_gold(case, executable_path)
            except TestGenerationError as exc:
                diagnostics.append(
                    {
                        "accepted": False,
                        "proposal_index": index,
                        "metadata": proposal.metadata,
                        "reason": str(exc),
                    }
                )
                continue
            signature = _case_signature(case)
            if signature in seen_signatures:
                diagnostics.append(
                    {
                        "accepted": False,
                        "proposal_index": index,
                        "metadata": proposal.metadata,
                        "reason": "duplicate structured test case",
                    }
                )
                continue
            seen_signatures.add(signature)
            accepted_cases.append(case)
            diagnostics.append(
                {
                    "accepted": True,
                    "proposal_index": index,
                    "test_id": case.test_id,
                    "metadata": proposal.metadata,
                    "observed_gold": executable_path is not None,
                }
            )
        _write_generation_diagnostics(output_dir, prompt.iteration, diagnostics)
        if not accepted_cases:
            raise TestGenerationError(
                "Model backend produced no safe structured test cases. "
                f"See {_diagnostic_path(output_dir, prompt.iteration)} for rejection details."
            )
        suite = ExecutableTestSuite(
            task_id=prompt.task_id,
            iteration=prompt.iteration,
            cases=accepted_cases,
            generator=MODEL_PROMPT_VERSION,
            renderer="pytest",
        )
        return write_executable_test_suite(output_dir, suite)

    def _generate_legacy_pytest_tests(
        self,
        prompt: TestGenerationPrompt,
        output_dir: Path,
        generated_tests: list[ModelGeneratedTest],
    ) -> list[Path]:
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
                    "legacy_pytest": True,
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
        "You are generating hidden executable test cases for a ProgramBench-style CLI task.\n"
        "Return JSON with a top-level 'test_cases' array. Each item should include "
        "'args', optional 'stdin', optional 'env', optional 'fixture_files', "
        "optional expected output fields, 'behavior_category', 'source', and "
        "'source_evidence'. The system will run safe proposals against the gold "
        "executable and record exact observed behavior before rendering pytest.\n\n"
        "Hard safety requirements:\n"
        "- Propose only argv fragments for the target executable; never shell commands.\n"
        "- Do not request network access, package installation, deletion, filesystem "
        "mutation, absolute local paths, or '..' path traversal.\n"
        "- Prefer positive, negative, stdin, file-input, env/config, and edge cases.\n"
        "- If exact expected output is uncertain, omit it; gold observation will fill it.\n\n"
        "Legacy fallback: raw pytest JSON with a top-level 'tests' array is still accepted, "
        "but structured 'test_cases' is strongly preferred. Legacy pytest must use only "
        "os.environ['PBGEN_EXECUTABLE'] as the executable root.\n\n"
        "Task payload:\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
    )


def parse_model_test_case_response(raw_response: str) -> list[ModelGeneratedCaseProposal]:
    """Parse structured executable test-case proposals from model JSON."""

    stripped = raw_response.strip()
    if not stripped:
        raise TestGenerationError("Model response was empty.")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    cases_data: Any
    if isinstance(data, dict):
        cases_data = (
            data.get("test_cases")
            or data.get("executable_test_cases")
            or data.get("cases")
        )
    else:
        cases_data = None
    if cases_data is None:
        return []
    if not isinstance(cases_data, list):
        raise TestGenerationError("Model test_cases must be an array.")
    proposals: list[ModelGeneratedCaseProposal] = []
    for index, item in enumerate(cases_data):
        if not isinstance(item, dict):
            raise TestGenerationError(f"Model test_cases[{index}] must be an object.")
        metadata = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "args",
                "stdin",
                "env",
                "fixture_files",
                "expected_exit_code",
                "expected_stdout",
                "expected_stderr",
                "behavior_category",
                "source",
                "source_path",
                "source_evidence",
                "test_id",
            }
        }
        proposals.append(ModelGeneratedCaseProposal(data=item, metadata=metadata))
    return proposals


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


def _case_from_model_proposal(
    prompt: TestGenerationPrompt,
    proposal: ModelGeneratedCaseProposal,
    index: int,
) -> ExecutableTestCase:
    data = proposal.data
    args = data.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise TestGenerationError("structured test case args must be a list of strings")
    env = data.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise TestGenerationError("structured test case env must be a string mapping")
    fixture_files = data.get("fixture_files", {})
    if not isinstance(fixture_files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in fixture_files.items()
    ):
        raise TestGenerationError("structured test case fixture_files must be a string mapping")
    expected_exit_code = data.get("expected_exit_code", 0)
    if not isinstance(expected_exit_code, int):
        raise TestGenerationError("structured test case expected_exit_code must be an integer")
    return ExecutableTestCase(
        test_id=_safe_test_id(str(data.get("test_id") or f"model_case_{prompt.iteration}_{index}")),
        task_id=prompt.task_id,
        args=args,
        stdin=str(data.get("stdin") or ""),
        env=env,
        fixture_files=fixture_files,
        expected_exit_code=expected_exit_code,
        expected_stdout=_expected_output(data.get("expected_stdout")),
        expected_stderr=_expected_output(data.get("expected_stderr")),
        behavior_category=_optional_str(data.get("behavior_category")),
        source=_optional_str(data.get("source")) or "model-structured",
        source_path=_optional_str(data.get("source_path")),
        provenance={"source_evidence": _optional_str(data.get("source_evidence")) or ""},
    )


def _validate_model_case(prompt: TestGenerationPrompt, case: ExecutableTestCase) -> None:
    decision = is_command_allowed(
        ["program", *case.args],
        policy=prompt.execution_policy,
        allow_patterns=prompt.safe_command_allow_patterns,
        deny_patterns=prompt.safe_command_deny_patterns,
        trusted=prompt.trusted_local_execution,
        command_kind="generated-test",
    )
    if not decision.allowed:
        raise TestGenerationError(f"unsafe model test command: {decision.reason}")
    for fixture_path in case.fixture_files:
        path = Path(fixture_path)
        if path.is_absolute() or ".." in path.parts:
            raise TestGenerationError(f"unsafe fixture path: {fixture_path}")
    for value in [*case.args, *case.env.values(), *case.fixture_files.keys()]:
        if value.startswith(("http://", "https://", "ssh://", "git@")):
            raise TestGenerationError("network locations are not allowed in structured test cases")


def _observe_case_on_gold(case: ExecutableTestCase, executable_path: Path) -> ExecutableTestCase:
    with tempfile.TemporaryDirectory(prefix="pbgen-model-case-") as temp_dir:
        cwd = Path(temp_dir)
        for relative, content in case.fixture_files.items():
            target = cwd / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        env = os.environ.copy()
        env.update(case.env)
        try:
            completed = subprocess.run(
                [str(executable_path), *case.args],
                input=case.stdin,
                check=False,
                text=True,
                capture_output=True,
                cwd=cwd,
                env=env,
                timeout=case.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TestGenerationError(f"gold observation timed out after {case.timeout_seconds}s") from exc
        except OSError as exc:
            raise TestGenerationError(f"could not observe gold behavior: {exc}") from exc
    observed = apply_observed_outputs(
        case,
        stdout=completed.stdout,
        stderr=completed.stderr,
        extra_provenance={"gold_observed": "true"},
    )
    return observed.model_copy(update={"expected_exit_code": completed.returncode})


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


def _expected_output(value: object) -> ExpectedOutput:
    if value is None:
        return ExpectedOutput()
    if isinstance(value, str):
        return ExpectedOutput(exact=value)
    if isinstance(value, dict):
        return ExpectedOutput.model_validate(value)
    raise TestGenerationError("expected_stdout/expected_stderr must be a string or object")


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_test_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    if not cleaned:
        return "model_case"
    if not cleaned.startswith("test_"):
        return f"test_{cleaned}"
    return cleaned


def _case_signature(case: ExecutableTestCase) -> str:
    payload = {
        "args": case.args,
        "stdin": case.stdin,
        "env": case.env,
        "fixture_files": case.fixture_files,
        "expected_exit_code": case.expected_exit_code,
        "stdout": case.expected_stdout.model_dump(mode="json"),
        "stderr": case.expected_stderr.model_dump(mode="json"),
    }
    return json.dumps(payload, sort_keys=True)


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
