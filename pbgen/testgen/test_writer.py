"""Test-generation backend interface and local heuristic backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from pbgen.schemas import (
    BehaviorCommand,
    BehaviorSurface,
    CommandExample,
    CoverageGap,
    ExecutableTestCase,
    ExecutableTestSuite,
    ExpectedOutput,
    RecordedCommandBehavior,
    TestArtifactRecord,
)
from pbgen.serialization import write_data
from pbgen.security import is_command_allowed
from pbgen.subprocess_utils import run_command
from pbgen.testgen.prompt_builder import TestGenerationPrompt


SHELL_OPERATORS = {
    "|",
    "||",
    "&",
    "&&",
    ";",
    ">",
    ">>",
    "<",
    "<<",
    "2>",
    "2>>",
}
DESTRUCTIVE_COMMANDS = {
    "commit",
    "delete",
    "deploy",
    "destroy",
    "drop",
    "format",
    "init",
    "install",
    "push",
    "remove",
    "reset",
    "rm",
    "send",
    "truncate",
    "uninstall",
    "upload",
    "write",
}
HELP_FLAGS = {"-h", "--help", "help"}
NUMERIC_WORDS = {"NUM", "NUMBER", "INT", "INTEGER", "FLOAT", "DECIMAL"}
FILE_WORDS = {"FILE", "PATH", "DIR", "DIRECTORY"}
INVALID_NUMERIC_VALUE = "not-a-number"
MAX_CANDIDATES = 32
MAX_CAPTURED_OUTPUT_CHARS = 20_000


class TestGenerationBackend(ABC):
    """Interface for local or model-backed behavioral test generators."""

    @abstractmethod
    def generate_tests(self, prompt: TestGenerationPrompt, output_dir: Path) -> list[Path]:
        """Generate pytest files and return their paths."""


class LocalHeuristicTestGenerationBackend(TestGenerationBackend):
    """Deterministic local backend used by the MVP and demo."""

    prompt_version = "local_heuristic_v1"

    def generate_tests(self, prompt: TestGenerationPrompt, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        test_path = _next_iteration_path(output_dir, prompt.iteration)
        examples = _examples_for_prompt(prompt)
        behaviors = _record_behaviors(prompt, examples, _resolve_executable(prompt, output_dir), output_dir)
        if not behaviors:
            return []
        suite = _test_suite_from_behaviors(prompt.task_id, behaviors, prompt.iteration)
        return write_executable_test_suite(output_dir, suite, rendered_path=test_path)


@dataclass(frozen=True)
class _SignaturePlan:
    args: list[str]
    numeric: bool = False


def write_executable_test_suite(
    output_dir: Path,
    suite: ExecutableTestSuite,
    *,
    rendered_path: Path | None = None,
) -> list[Path]:
    """Persist a canonical executable suite and pytest compatibility renderer."""

    output_dir.mkdir(parents=True, exist_ok=True)
    test_path = rendered_path or _next_iteration_path(output_dir, suite.iteration)
    suite_path = _next_case_suite_path(output_dir, suite.iteration)
    write_data(suite_path, suite.model_dump(mode="json"))
    test_path.write_text(_render_pytest(suite.cases), encoding="utf-8")
    _write_artifact_record(suite.task_id, suite.iteration, suite, suite_path, [test_path])
    return [test_path]


def _examples_for_prompt(prompt: TestGenerationPrompt) -> list[CommandExample]:
    examples = [
        example
        for example in prompt.behavior_surface.command_examples
        if _is_safe_example(prompt, example.args)
    ]
    examples.extend(_examples_from_surface(prompt.behavior_surface))
    examples.extend(_examples_for_gaps(prompt.behavior_surface, prompt.coverage_gaps))
    if not examples:
        examples.extend(_fallback_examples(prompt.behavior_surface))
    return _dedupe_examples(examples)[:MAX_CANDIDATES]


def _examples_from_surface(surface: BehaviorSurface) -> list[CommandExample]:
    examples: list[CommandExample] = []
    command_names = {command.command for command in surface.commands}
    flags = set(surface.global_flags) | {name for name in command_names if name.startswith("-")}
    if "--help" in flags or "--help" in command_names:
        examples.append(CommandExample(args=["--help"], source="surface", category="help"))
    if "-h" in flags or "-h" in command_names:
        examples.append(CommandExample(args=["-h"], source="surface", category="help"))
    if "--version" in flags or "--version" in command_names:
        examples.append(CommandExample(args=["--version"], source="surface", category="version"))

    for command in surface.commands:
        if command.command.startswith("-") or command.category != "subcommand":
            continue
        plan = _signature_plan_for_command(command)
        if plan is None:
            continue
        examples.append(
            CommandExample(
                args=[command.command, *plan.args],
                source="surface",
                category="subcommand",
            )
        )
        for flag in surface.subcommand_flags.get(command.command, []):
            if flag in HELP_FLAGS:
                examples.append(
                    CommandExample(
                        args=[command.command, flag],
                        source="surface-flag",
                        category="help",
                    )
                )
    return examples


def _examples_for_gaps(surface: BehaviorSurface, gaps: list[CoverageGap]) -> list[CommandExample]:
    if not gaps:
        return []
    gap_text = _gap_text(gaps)
    target_terms = _gap_terms(gap_text)
    examples: list[CommandExample] = []
    for flag in surface.global_flags:
        if flag.startswith("-") and _flag_matches_gap(flag, target_terms):
            examples.append(CommandExample(args=[flag], source="coverage-gap", category="flag"))
    for command in surface.commands:
        if command.command.startswith("-") or command.category != "subcommand":
            continue
        plan = _signature_plan_for_command(command)
        if plan is None:
            continue
        command_terms = _term_variants(command.command)
        targeted = bool(command_terms & target_terms)
        numeric_gap = plan.numeric and _gap_requests_numeric_case(gap_text)
        if targeted:
            examples.append(
                CommandExample(
                    args=[command.command, *plan.args],
                    source="coverage-gap",
                    category="subcommand",
                )
            )
        if targeted or numeric_gap:
            if numeric_gap:
                examples.append(
                    CommandExample(
                        args=[command.command, INVALID_NUMERIC_VALUE],
                        source="coverage-gap",
                        category="error",
                    )
                )
    return examples


def _fallback_examples(surface: BehaviorSurface) -> list[CommandExample]:
    flags = set(surface.global_flags)
    if "--help" in flags or any(command.command == "--help" for command in surface.commands):
        return [CommandExample(args=["--help"], source="fallback", category="help")]
    if "--version" in flags or any(command.command == "--version" for command in surface.commands):
        return [CommandExample(args=["--version"], source="fallback", category="version")]
    return []


def _record_behaviors(
    prompt: TestGenerationPrompt,
    examples: list[CommandExample],
    executable_path: Path | None,
    output_dir: Path,
) -> list[RecordedCommandBehavior]:
    behaviors: list[RecordedCommandBehavior] = []
    for example in examples:
        if not _is_safe_example(prompt, example.args):
            continue
        if executable_path is None:
            recorded = _behavior_from_expected(example)
            if recorded is not None:
                behaviors.append(recorded)
            continue
        try:
            result = run_command(
                [str(executable_path), *example.args],
                cwd=output_dir,
                timeout_seconds=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if len(result.stdout) + len(result.stderr) > MAX_CAPTURED_OUTPUT_CHARS:
            continue
        behaviors.append(
            RecordedCommandBehavior(
                args=example.args,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                source=example.source,
                source_path=example.source_path,
            )
        )
    return _dedupe_behaviors(behaviors)


def _behavior_from_expected(example: CommandExample) -> RecordedCommandBehavior | None:
    if example.expected_exit_code is None:
        return None
    return RecordedCommandBehavior(
        args=example.args,
        exit_code=example.expected_exit_code,
        stdout=example.expected_stdout or "",
        stderr=example.expected_stderr or "",
        source=example.source,
        source_path=example.source_path,
    )


def _test_suite_from_behaviors(
    task_id: str,
    behaviors: list[RecordedCommandBehavior],
    iteration: int,
) -> ExecutableTestSuite:
    cases = []
    for index, behavior in enumerate(behaviors):
        cases.append(_test_case_from_behavior(task_id, behavior, index, iteration))
    return ExecutableTestSuite(
        task_id=task_id,
        iteration=iteration,
        cases=cases,
        generator=LocalHeuristicTestGenerationBackend.prompt_version,
        renderer="pytest",
    )


def _test_case_from_behavior(
    task_id: str,
    behavior: RecordedCommandBehavior,
    index: int,
    iteration: int,
) -> ExecutableTestCase:
    return ExecutableTestCase(
        test_id=_test_name(behavior, index, iteration),
        task_id=task_id,
        args=behavior.args,
        expected_exit_code=behavior.exit_code,
        expected_stdout=ExpectedOutput(exact=behavior.stdout),
        expected_stderr=ExpectedOutput(exact=behavior.stderr),
        behavior_category=None,
        source=behavior.source,
        source_path=behavior.source_path,
        provenance={"recorded_behavior_source": behavior.source},
    )


def _render_pytest(cases: list[ExecutableTestCase]) -> str:
    tests = []
    for case in cases:
        tests.append(
            f'''def {case.test_id}() -> None:
    result = run_cmd({case.args!r})
    assert result.returncode == {case.expected_exit_code!r}
{_render_output_assertions("stdout", case.expected_stdout)}\
{_render_output_assertions("stderr", case.expected_stderr)}\
'''
        )
    return HEADER + "\n\n" + "\n\n".join(tests) + "\n"


def _render_output_assertions(stream: str, expected: ExpectedOutput) -> str:
    lines: list[str] = []
    if expected.exact is not None:
        lines.append(f"    assert result.{stream} == {expected.exact!r}")
    for value in expected.contains:
        lines.append(f"    assert {value!r} in result.{stream}")
    for pattern in expected.regex:
        lines.append(f"    assert re.search({pattern!r}, result.{stream})")
    return "\n".join(lines) + ("\n" if lines else "")


HEADER = '''"""Generated behavioral tests for the cleanroom executable."""

from __future__ import annotations

import os
import re
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
'''


def _test_name(behavior: RecordedCommandBehavior, index: int, iteration: int) -> str:
    tokens = [re.sub(r"[^a-zA-Z0-9_]+", "_", token).strip("_") for token in behavior.args[:3]]
    suffix = "_".join(token.lower() for token in tokens if token) or "empty"
    return f"test_iter_{iteration}_{index}_{suffix}"


def _next_iteration_path(output_dir: Path, iteration: int) -> Path:
    stem = f"test_behavior_iter_{iteration}"
    path = output_dir / f"{stem}.py"
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = output_dir / f"{stem}_{index:02d}.py"
        if not candidate.exists():
            return candidate
        index += 1


def _next_case_suite_path(output_dir: Path, iteration: int) -> Path:
    stem = f"test_cases_iteration_{iteration}"
    path = output_dir / f"{stem}.json"
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = output_dir / f"{stem}_{index:02d}.json"
        if not candidate.exists():
            return candidate
        index += 1


def _write_artifact_record(
    task_id: str,
    iteration: int,
    suite: ExecutableTestSuite,
    suite_path: Path,
    rendered_paths: list[Path],
) -> None:
    record = TestArtifactRecord(
        task_id=task_id,
        iteration=iteration,
        canonical_suite_path=suite_path,
        rendered_paths=rendered_paths,
        case_count=len(suite.cases),
        renderer="pytest",
    )
    write_data(
        suite_path.with_name(f"{suite_path.stem}_artifact.json"),
        record.model_dump(mode="json"),
    )


def _resolve_executable(prompt: TestGenerationPrompt, output_dir: Path) -> Path | None:
    executable_path = getattr(prompt, "executable_path", None)
    if executable_path is not None:
        path = Path(executable_path)
        if path.exists():
            return path
    fallback = output_dir.parent / "gold" / "executable" / "program"
    if fallback.exists():
        return fallback
    return None


def _signature_plan_for_command(command: BehaviorCommand) -> _SignaturePlan | None:
    signature = _signature_tokens(command)
    if not signature:
        return _SignaturePlan(args=[])
    return _signature_plan(signature)


def _signature_tokens(command: BehaviorCommand) -> list[str]:
    if not command.notes:
        return []
    for line in command.notes.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.lower().startswith("usage:"):
            tokens = text.split()[1:]
            if tokens and tokens[0] == command.command:
                tokens = tokens[1:]
            return tokens
        signature_tokens: list[str] = []
        for token in text.split():
            cleaned = token.strip(",.;")
            if _is_signature_token(cleaned):
                signature_tokens.append(cleaned)
            elif signature_tokens:
                break
        if signature_tokens:
            return signature_tokens
    return []


def _signature_plan(signature: list[str]) -> _SignaturePlan | None:
    args: list[str] = []
    numeric = False
    skip_next_value = False
    for token in signature:
        cleaned = token.strip(",.;")
        optional = cleaned.startswith("[") and cleaned.endswith("]")
        cleaned = cleaned.strip("[]<>")
        if not cleaned or optional:
            continue
        if cleaned.startswith("-"):
            args.append(cleaned)
            skip_next_value = True
            continue
        if skip_next_value:
            skip_next_value = False
        if any(word in cleaned.upper() for word in FILE_WORDS):
            return None
        variadic = cleaned.endswith("...")
        cleaned = cleaned.removesuffix("...")
        if any(word in cleaned.upper() for word in NUMERIC_WORDS):
            numeric = True
            args.append("1")
            if variadic:
                args.append("2")
        elif cleaned.isupper():
            args.append("sample")
            if variadic:
                args.append("example")
    return _SignaturePlan(args=args, numeric=numeric)


def _is_signature_token(token: str) -> bool:
    cleaned = token.strip("[]<>").rstrip(",.;")
    if cleaned.startswith("-"):
        return True
    if cleaned.endswith("..."):
        cleaned = cleaned[:-3]
    return cleaned.isupper() and any(char.isalpha() for char in cleaned)


def _gap_text(gaps: list[CoverageGap]) -> str:
    return " ".join(
        " ".join(
            item
            for item in [
                gap.file_path,
                gap.function_name or "",
                gap.reason,
            ]
            if item
        ).lower()
        for gap in gaps
    )


def _gap_terms(text: str) -> set[str]:
    terms = set(re.findall(r"--?[a-z0-9][a-z0-9_-]*|[a-z0-9]+", text))
    expanded: set[str] = set()
    for term in terms:
        expanded.update(_term_variants(term))
    return terms | expanded


def _term_variants(value: str) -> set[str]:
    lowered = value.lower().strip()
    variants = {lowered}
    variants.add(lowered.lstrip("-"))
    variants.update(part for part in re.split(r"[^a-z0-9]+", lowered) if part)
    return {variant for variant in variants if variant}


def _flag_matches_gap(flag: str, target_terms: set[str]) -> bool:
    return bool(_term_variants(flag) & target_terms)


def _gap_requests_numeric_case(text: str) -> bool:
    return any(term in text for term in ("error", "invalid", "number", "numeric", "parse"))


def _is_safe_args(args: list[str]) -> bool:
    if len(args) > 16:
        return False
    if args and args[0].lower() in DESTRUCTIVE_COMMANDS and not (set(args) & HELP_FLAGS):
        return False
    for arg in args:
        if len(arg) > 200 or arg in SHELL_OPERATORS:
            return False
        if any(marker in arg for marker in ("`", "$(", "${")):
            return False
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", arg):
            return False
        if arg.startswith(("/", "~")) or ".." in arg.split("/"):
            return False
    return True


def _is_safe_example(prompt: TestGenerationPrompt, args: list[str]) -> bool:
    if not _is_safe_args(args):
        return False
    decision = is_command_allowed(
        ["program", *args],
        policy=prompt.execution_policy,
        allow_patterns=prompt.safe_command_allow_patterns,
        deny_patterns=prompt.safe_command_deny_patterns,
        trusted=prompt.trusted_local_execution,
        command_kind="generated-test",
    )
    return decision.allowed


def _dedupe_examples(examples: list[CommandExample]) -> list[CommandExample]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[CommandExample] = []
    for example in examples:
        key = tuple(example.args)
        if key not in seen:
            deduped.append(example)
            seen.add(key)
    return deduped


def _dedupe_behaviors(behaviors: list[RecordedCommandBehavior]) -> list[RecordedCommandBehavior]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[RecordedCommandBehavior] = []
    for behavior in behaviors:
        key = tuple(behavior.args)
        if key not in seen:
            deduped.append(behavior)
            seen.add(key)
    return deduped
