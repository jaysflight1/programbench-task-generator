"""OpenAI-backed candidate solver for released ProgramBench task packages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import urllib.error
import urllib.request

from pbgen.errors import PBGenError
from pbgen.schemas import (
    OpenAISolverRoundReport,
    OpenAISolverRunReport,
    SolverVisibleFile,
    TaskSpec,
)
from pbgen.serialization import read_data, write_data
from pbgen.subprocess_utils import CommandResult, LocalCommandRunner


DEFAULT_SOLVER_MODEL = "gpt-5.5"
DEFAULT_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
MAX_PUBLIC_FILE_BYTES = 200_000
MAX_TOTAL_CONTEXT_BYTES = 1_200_000
MAX_GENERATED_FILES = 256
MAX_GENERATED_BYTES = 3_000_000

_TEXT_FILE_SUFFIXES = {
    "",
    ".cfg",
    ".csv",
    ".ini",
    ".json",
    ".md",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_PUBLIC_ROOTS = {"docs", "assets", "public"}
_RESTRICTED_PARTS = {
    ".git",
    "candidate_runs",
    "evaluator",
    "generated_tests",
    "gold",
    "hidden",
    "hidden_tests",
    "logs",
    "original_source",
    "reports",
    "solver_runs",
}
_SAFE_OUTPUT_PART = re.compile(r"^[A-Za-z0-9._+-][A-Za-z0-9._+ -]*$")


@dataclass(frozen=True)
class SolverVisibleContext:
    """Public files from a solver package that may be shown to the model."""

    task_id: str
    solver_package: Path
    files: dict[str, str]
    manifest: list[SolverVisibleFile]


@dataclass(frozen=True)
class SolverFile:
    """One file emitted by the candidate model."""

    path: str
    content: str


@dataclass(frozen=True)
class SolverProposal:
    """Structured model proposal for a candidate source tree."""

    files: list[SolverFile]
    build_script_path: str
    build_script_content: str
    notes: str | None = None


@dataclass(frozen=True)
class OpenAISolverConfig:
    """Runtime options for the OpenAI candidate solver."""

    solver_package: Path
    output_dir: Path
    model_name: str = DEFAULT_SOLVER_MODEL
    attempt_id: str = "attempt-1"
    max_rounds: int = 3
    reasoning_effort: str = "xhigh"
    endpoint: str = DEFAULT_RESPONSES_ENDPOINT
    timeout_seconds: int = 900
    max_output_tokens: int | None = None
    input_cost_per_1m: float | None = None
    output_cost_per_1m: float | None = None


@dataclass(frozen=True)
class ModelResponse:
    """Normalized response returned by an OpenAI client implementation."""

    raw: dict[str, object]
    text: str
    usage: dict[str, int] = field(default_factory=dict)


class OpenAIResponsesClient:
    """Minimal Responses API client used by the candidate solver."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str = DEFAULT_RESPONSES_ENDPOINT,
        timeout_seconds: int = 900,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get(
            "PBGEN_HOSTED_MODEL_API_KEY"
        )
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def create_response(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        reasoning_effort: str,
        max_output_tokens: int | None,
    ) -> ModelResponse:
        """Call the Responses API and return normalized JSON/text metadata."""

        if not self.api_key:
            raise PBGenError(
                "OpenAI solver requires OPENAI_API_KEY or PBGEN_HOSTED_MODEL_API_KEY."
            )
        payload: dict[str, object] = {
            "model": model_name,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "reasoning": {"effort": reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "programbench_candidate_solution",
                    "strict": True,
                    "schema": _proposal_json_schema(),
                }
            },
        }
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
            raise PBGenError(f"OpenAI solver request failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise PBGenError(f"OpenAI solver request failed: {exc.reason}") from exc
        if not isinstance(raw, dict):
            raise PBGenError("OpenAI solver response was not a JSON object.")
        return ModelResponse(
            raw=raw,
            text=_extract_response_text(raw),
            usage=_extract_usage(raw),
        )


def solve_with_openai(
    config: OpenAISolverConfig,
    *,
    client: OpenAIResponsesClient | None = None,
) -> OpenAISolverRunReport:
    """Generate a candidate source tree for one solver package."""

    context = collect_solver_visible_context(config.solver_package)
    output_dir = config.output_dir.expanduser().resolve()
    candidate_dir = output_dir / "candidate"
    prompts_dir = output_dir / "prompts"
    responses_dir = output_dir / "responses"
    for path in [candidate_dir, prompts_dir, responses_dir]:
        path.mkdir(parents=True, exist_ok=True)

    rounds: list[OpenAISolverRoundReport] = []
    api_calls = 0
    total_usage: dict[str, int] = {}
    feedback: list[str] = []
    final_build_script: Path | None = None
    status = "failed"
    reason: str | None = None
    active_client = client or OpenAIResponsesClient(
        endpoint=config.endpoint,
        timeout_seconds=config.timeout_seconds,
    )

    for round_index in range(1, config.max_rounds + 1):
        system_prompt = _system_prompt()
        user_prompt = _user_prompt(context, feedback=feedback, round_index=round_index)
        prompt_path = prompts_dir / f"round_{round_index}_prompt.txt"
        prompt_text = system_prompt + "\n\n" + user_prompt
        prompt_path.write_text(prompt_text, encoding="utf-8")
        prompt_hash = _sha256_text(prompt_text)
        response_path = responses_dir / f"round_{round_index}_response.json"
        files_written: list[str] = []
        round_feedback: list[str] = []
        build_ok = False
        smoke_ok = False
        api_call_succeeded = False
        response_hash = _sha256_text("")
        usage: dict[str, int] = {}

        try:
            response = active_client.create_response(
                model_name=config.model_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                reasoning_effort=config.reasoning_effort,
                max_output_tokens=config.max_output_tokens,
            )
            api_calls += 1
            api_call_succeeded = True
            usage = response.usage
            _merge_usage(total_usage, usage)
            write_data(response_path, response.raw)
            response_hash = _sha256_text(json.dumps(response.raw, sort_keys=True))
            proposal = parse_solver_proposal(response.text)
            _reset_candidate_dir(candidate_dir)
            files_written = write_solver_proposal(candidate_dir, proposal)
            final_build_script = candidate_dir / proposal.build_script_path
            build_result, smoke_results = run_public_smoke(candidate_dir, final_build_script)
            build_ok = build_result.ok and (candidate_dir / "out" / "program").is_file()
            smoke_ok = build_ok and all(result.ok for result in smoke_results)
            round_feedback = _feedback_from_smoke(build_result, smoke_results, candidate_dir)
            if build_ok:
                status = "completed" if smoke_ok else "completed_with_smoke_warnings"
                reason = None if smoke_ok else "candidate built but public smoke checks failed"
                rounds.append(
                    OpenAISolverRoundReport(
                        round_index=round_index,
                        prompt_path=prompt_path,
                        response_path=response_path,
                        prompt_sha256=prompt_hash,
                        response_sha256=response_hash,
                        accepted=True,
                        api_call_succeeded=api_call_succeeded,
                        token_usage=usage,
                        files_written=files_written,
                        build_ok=build_ok,
                        smoke_ok=smoke_ok,
                        feedback=round_feedback,
                    )
                )
                break
            feedback = round_feedback
        except PBGenError as exc:
            response_path.write_text(
                json.dumps({"error": str(exc)}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            response_hash = _sha256_text(response_path.read_text(encoding="utf-8"))
            feedback = [str(exc)]
            reason = str(exc)

        rounds.append(
            OpenAISolverRoundReport(
                round_index=round_index,
                prompt_path=prompt_path,
                response_path=response_path,
                prompt_sha256=prompt_hash,
                response_sha256=response_hash,
                accepted=False,
                api_call_succeeded=api_call_succeeded,
                token_usage=usage,
                files_written=files_written,
                build_ok=build_ok,
                smoke_ok=smoke_ok,
                feedback=feedback,
            )
        )

    if final_build_script is None:
        reason = reason or "solver did not produce a buildable candidate"
    report = OpenAISolverRunReport(
        task_id=context.task_id,
        solver_package=context.solver_package,
        output_dir=output_dir,
        candidate_source=candidate_dir,
        build_script=final_build_script,
        model_name=config.model_name,
        attempt_id=config.attempt_id,
        reasoning_effort=config.reasoning_effort,
        max_rounds=config.max_rounds,
        api_calls=api_calls,
        token_usage=total_usage,
        estimated_cost_usd=_estimate_cost(total_usage, config),
        visible_file_manifest=context.manifest,
        rounds=rounds,
        status=status,
        reason=reason,
    )
    write_data(output_dir / "openai_solver_run.json", report.model_dump(mode="json"))
    return report


def collect_solver_visible_context(solver_package: Path) -> SolverVisibleContext:
    """Collect only public files from a released solver package."""

    root = solver_package.expanduser().resolve()
    if not root.is_dir():
        raise PBGenError(f"Solver package does not exist: {solver_package}")
    task_spec = _load_task_spec(root)
    public_paths = _public_solver_paths(root, task_spec)
    files: dict[str, str] = {}
    manifest: list[SolverVisibleFile] = []
    total_bytes = 0
    for path in public_paths:
        relative = path.relative_to(root).as_posix()
        if not _is_solver_visible_path(relative):
            continue
        data = path.read_bytes()
        if len(data) > MAX_PUBLIC_FILE_BYTES:
            continue
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_CONTEXT_BYTES:
            break
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        files[relative] = text
        manifest.append(
            SolverVisibleFile(
                path=relative,
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
            )
        )
    if "TASK.md" not in files or "SUBMISSION.md" not in files or "task.yaml" not in files:
        raise PBGenError("Solver package must contain TASK.md, SUBMISSION.md, and task.yaml.")
    return SolverVisibleContext(
        task_id=task_spec.task_id,
        solver_package=root,
        files=files,
        manifest=manifest,
    )


def parse_solver_proposal(raw_text: str) -> SolverProposal:
    """Parse and validate a model-produced candidate solution proposal."""

    data = _json_object_from_text(raw_text)
    raw_files = data.get("files")
    raw_build = data.get("build_script")
    if not isinstance(raw_files, list) or not isinstance(raw_build, dict):
        raise PBGenError("OpenAI solver output must contain files[] and build_script.")
    files: list[SolverFile] = []
    total_bytes = 0
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise PBGenError("Each generated file must be a JSON object.")
        path = raw_file.get("path")
        content = raw_file.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise PBGenError("Each generated file requires string path and content fields.")
        safe_path = _validate_generated_path(path)
        total_bytes += len(content.encode("utf-8"))
        files.append(SolverFile(path=safe_path, content=content))
    build_path = raw_build.get("path")
    build_content = raw_build.get("content")
    if not isinstance(build_path, str) or not isinstance(build_content, str):
        raise PBGenError("build_script requires string path and content fields.")
    safe_build_path = _validate_generated_path(build_path)
    total_bytes += len(build_content.encode("utf-8"))
    if len(files) > MAX_GENERATED_FILES:
        raise PBGenError(f"Generated candidate exceeds {MAX_GENERATED_FILES} files.")
    if total_bytes > MAX_GENERATED_BYTES:
        raise PBGenError(f"Generated candidate exceeds {MAX_GENERATED_BYTES} bytes.")
    notes = data.get("notes")
    return SolverProposal(
        files=files,
        build_script_path=safe_build_path,
        build_script_content=build_content,
        notes=notes if isinstance(notes, str) else None,
    )


def write_solver_proposal(candidate_dir: Path, proposal: SolverProposal) -> list[str]:
    """Write a validated solver proposal to a candidate directory."""

    written: list[str] = []
    for generated in proposal.files:
        path = _safe_join(candidate_dir, generated.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated.content, encoding="utf-8")
        written.append(generated.path)
    build_script = _safe_join(candidate_dir, proposal.build_script_path)
    build_script.parent.mkdir(parents=True, exist_ok=True)
    build_script.write_text(proposal.build_script_content, encoding="utf-8")
    build_script.chmod(build_script.stat().st_mode | stat.S_IXUSR)
    if proposal.build_script_path not in written:
        written.append(proposal.build_script_path)
    return sorted(set(written))


def run_public_smoke(
    candidate_dir: Path,
    build_script: Path,
    *,
    timeout_seconds: int = 60,
) -> tuple[CommandResult, list[CommandResult]]:
    """Run bounded public build/help smoke checks for repair feedback."""

    runner = LocalCommandRunner()
    env = _public_smoke_env(candidate_dir)
    build_command = _build_script_command(build_script)
    build_result = runner.run(
        build_command,
        cwd=candidate_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    executable = candidate_dir / "out" / "program"
    smoke_results: list[CommandResult] = []
    if build_result.ok and executable.is_file():
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        for args in [["--help"], ["--version"]]:
            smoke_results.append(
                runner.run(
                    [str(executable), *args],
                    cwd=candidate_dir,
                    env=env,
                    timeout_seconds=15,
                )
            )
    return build_result, smoke_results


def _public_smoke_env(candidate_dir: Path) -> dict[str, str]:
    home = candidate_dir / ".home"
    tmp = candidate_dir / ".tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp),
    }
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        env["SYSTEMROOT"] = system_root
    return env


def _load_task_spec(root: Path) -> TaskSpec:
    task_yaml = root / "task.yaml"
    if not task_yaml.exists():
        raise PBGenError(f"Solver package is missing task.yaml: {root}")
    return TaskSpec.model_validate(read_data(task_yaml))


def _public_solver_paths(root: Path, task_spec: TaskSpec) -> list[Path]:
    required: list[Path] = []
    paths: set[Path] = set()
    for name in ["TASK.md", "SUBMISSION.md", "task.yaml"]:
        path = root / name
        if path.is_file():
            required.append(path)
    for public_root in _PUBLIC_ROOTS:
        directory = root / public_root
        if directory.is_dir():
            paths.update(_iter_public_text_files(directory))
    for relative in [*task_spec.docs_paths, *task_spec.asset_paths]:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file() and _looks_textual(path):
            paths.add(path)
        elif path.is_dir():
            paths.update(_iter_public_text_files(path))
    required_set = set(required)
    return required + sorted(path for path in paths if path not in required_set)


def _iter_public_text_files(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink() and _looks_textual(path)
    ]


def _looks_textual(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_FILE_SUFFIXES


def _is_solver_visible_path(relative: str) -> bool:
    parts = set(Path(relative).parts)
    return not bool(parts & _RESTRICTED_PARTS)


def _system_prompt() -> str:
    return (
        "You are solving a released ProgramBench-style task as a candidate model. "
        "You may use only the public solver package files shown in the prompt. "
        "Do not rely on hidden tests, evaluator files, original repository source, internet access, "
        "or absolute local paths. Return JSON only."
    )


def _user_prompt(
    context: SolverVisibleContext,
    *,
    feedback: Sequence[str],
    round_index: int,
) -> str:
    lines = [
        f"Task id: {context.task_id}",
        f"Repair round: {round_index}",
        "",
        "Create a candidate source tree and one build script. The build script must run from the "
        "candidate source directory and produce an executable at out/program.",
        "",
        "Return exactly this JSON shape:",
        json.dumps(_proposal_template(), indent=2, sort_keys=True),
        "",
        "Public solver files:",
    ]
    for path, content in sorted(context.files.items()):
        lines.extend(
            [
                f"\n--- BEGIN FILE {path} ---",
                content,
                f"--- END FILE {path} ---",
            ]
        )
    if feedback:
        lines.extend(["", "Previous round feedback to repair:"])
        lines.extend(f"- {item}" for item in feedback)
    return "\n".join(lines)


def _proposal_template() -> dict[str, object]:
    return {
        "files": [
            {"path": "src/program.py", "content": "source code here"}
        ],
        "build_script": {
            "path": "build.py",
            "content": "build script content here",
        },
        "notes": "brief strategy summary",
    }


def _proposal_json_schema() -> dict[str, object]:
    file_schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "files": {"type": "array", "items": file_schema},
            "build_script": file_schema,
            "notes": {"type": "string"},
        },
        "required": ["files", "build_script", "notes"],
    }


def _json_object_from_text(raw_text: str) -> dict[str, object]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise PBGenError("OpenAI solver output was not parseable JSON.") from exc
        loaded = json.loads(text[start : end + 1])
    if not isinstance(loaded, dict):
        raise PBGenError("OpenAI solver output must be a JSON object.")
    return loaded


def _validate_generated_path(path: str) -> str:
    raw = Path(path)
    if raw.is_absolute():
        raise PBGenError(f"Generated file path must be relative: {path}")
    if not path or path.endswith("/"):
        raise PBGenError(f"Generated file path must name a file: {path}")
    parts = raw.parts
    if ".." in parts:
        raise PBGenError(f"Generated file path must not contain '..': {path}")
    lowered = {part.lower() for part in parts}
    if lowered & _RESTRICTED_PARTS:
        raise PBGenError(f"Generated file path uses restricted location: {path}")
    if any(part.startswith(".") for part in parts):
        raise PBGenError(f"Generated file path must not use hidden paths: {path}")
    if any(_SAFE_OUTPUT_PART.match(part) is None for part in parts):
        raise PBGenError(f"Generated file path contains unsafe characters: {path}")
    return raw.as_posix()


def _safe_join(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PBGenError(f"Generated path escapes candidate directory: {relative}") from exc
    return path


def _reset_candidate_dir(candidate_dir: Path) -> None:
    if candidate_dir.exists():
        shutil.rmtree(candidate_dir)
    candidate_dir.mkdir(parents=True)


def _build_script_command(build_script: Path) -> list[str]:
    if build_script.suffix == ".py":
        return ["python3", str(build_script)]
    build_script.chmod(build_script.stat().st_mode | stat.S_IXUSR)
    return [str(build_script)]


def _feedback_from_smoke(
    build_result: CommandResult,
    smoke_results: Sequence[CommandResult],
    candidate_dir: Path,
) -> list[str]:
    feedback: list[str] = []
    executable = candidate_dir / "out" / "program"
    if not build_result.ok:
        feedback.append(_summarize_result("build failed", build_result))
    elif not executable.is_file():
        feedback.append("build succeeded but did not create out/program")
    for result in smoke_results:
        if not result.ok:
            feedback.append(_summarize_result("public smoke failed", result))
    return feedback


def _summarize_result(label: str, result: CommandResult) -> str:
    stdout = result.stdout[-1000:]
    stderr = result.stderr[-1000:]
    return (
        f"{label}: command={result.args!r} exit={result.returncode} "
        f"stdout={stdout!r} stderr={stderr!r}"
    )


def _extract_response_text(raw: Mapping[str, object]) -> str:
    output_text = raw.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    chunks: list[str] = []
    output = raw.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if isinstance(content_item, dict):
                    text = content_item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
    choices = raw.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    chunks.append(message["content"])
    if not chunks:
        raise PBGenError("OpenAI solver response did not contain output text.")
    return "\n".join(chunks)


def _extract_usage(raw: Mapping[str, object]) -> dict[str, int]:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(key, str) and isinstance(value, int):
            normalized[key] = value
    return normalized


def _merge_usage(total: dict[str, int], current: Mapping[str, int]) -> None:
    for key, value in current.items():
        total[key] = total.get(key, 0) + value


def _estimate_cost(
    usage: Mapping[str, int],
    config: OpenAISolverConfig,
) -> float | None:
    input_price = config.input_cost_per_1m
    output_price = config.output_cost_per_1m
    if input_price is None:
        input_price = _float_env("OPENAI_SOLVER_INPUT_COST_PER_1M")
    if output_price is None:
        output_price = _float_env("OPENAI_SOLVER_OUTPUT_COST_PER_1M")
    if input_price is None or output_price is None:
        return None
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return (input_tokens / 1_000_000 * input_price) + (
        output_tokens / 1_000_000 * output_price
    )


def _float_env(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
