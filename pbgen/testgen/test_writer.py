"""Test-generation backend interface and local heuristic backend."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from pbgen.config import PBGenConfig
from pbgen.errors import TestGenerationError
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
DEFAULT_AGENTIC_CANDIDATE_BUDGET = 256
MAX_CAPTURED_OUTPUT_CHARS = 20_000


class TestGenerationBackend(ABC):
    """Interface for local or model-backed behavioral test generators."""

    @abstractmethod
    def generate_tests(self, prompt: TestGenerationPrompt, output_dir: Path) -> list[Path]:
        """Generate pytest files and return their paths."""


@dataclass(frozen=True)
class _CaseProposal:
    args: list[str]
    source: str
    source_path: str | None = None
    behavior_category: str | None = None
    stdin: str = ""
    env: dict[str, str] | None = None
    fixture_files: dict[str, str] | None = None
    provenance: dict[str, str] | None = None


class AgenticTestGenerationBackend(TestGenerationBackend):
    """Deterministic agentic backend with proposal, gold-observation, and revision."""

    prompt_version = "local_agentic_v1"

    def __init__(self, config: PBGenConfig | None = None) -> None:
        self.config = config or PBGenConfig()

    def generate_tests(self, prompt: TestGenerationPrompt, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        test_path = _next_iteration_path(output_dir, prompt.iteration)
        proposals = _case_proposals_for_prompt(
            prompt,
            budget=self.config.agentic_candidate_budget,
        )
        cases, diagnostics = _observe_and_revise_proposals(
            prompt,
            proposals,
            _resolve_executable(prompt, output_dir),
            revision_rounds=self.config.agentic_revision_rounds,
        )
        _write_agentic_diagnostics(output_dir, prompt.iteration, diagnostics)
        if not cases:
            return []
        suite = ExecutableTestSuite(
            task_id=prompt.task_id,
            iteration=prompt.iteration,
            cases=cases,
            generator=self.prompt_version,
            renderer="pytest",
        )
        return write_executable_test_suite(output_dir, suite, rendered_path=test_path)


class LocalHeuristicTestGenerationBackend(AgenticTestGenerationBackend):
    """Backward-compatible name for the deterministic local agentic backend."""


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


def render_pytest_compatibility(cases: list[ExecutableTestCase]) -> str:
    """Render canonical executable test cases as pytest compatibility tests."""

    return _render_pytest(cases)


def _case_proposals_for_prompt(
    prompt: TestGenerationPrompt,
    *,
    budget: int,
) -> list[_CaseProposal]:
    proposals: list[_CaseProposal] = [
        _proposal_from_example(example)
        for example in _examples_for_prompt(prompt, budget=budget)
    ]
    proposals.extend(_stdin_proposals(prompt.behavior_surface))
    proposals.extend(_file_input_proposals(prompt.behavior_surface))
    proposals.extend(_env_config_proposals(prompt.behavior_surface))
    proposals.extend(_side_effect_proposals(prompt.behavior_surface))
    proposals.extend(_coverage_edge_proposals(prompt.behavior_surface, prompt.coverage_gaps))
    safe = [proposal for proposal in proposals if _is_safe_proposal(prompt, proposal)]
    return _dedupe_proposals(safe)[: max(1, budget)]


def _proposal_from_example(example: CommandExample) -> _CaseProposal:
    return _CaseProposal(
        args=example.args,
        source=example.source,
        source_path=example.source_path,
        behavior_category=example.category,
        provenance={"example_source": example.source},
    )


def _stdin_proposals(surface: BehaviorSurface) -> list[_CaseProposal]:
    if not surface.stdin_supported:
        return []
    commands = [
        command.command
        for command in surface.commands
        if command.category == "subcommand" and not command.command.startswith("-")
    ]
    args_variants = [[command] for command in commands[:16]] or [[]]
    return [
        _CaseProposal(
            args=args,
            stdin="sample stdin\nsecond line\n",
            source="agentic-stdin",
            behavior_category="stdin",
            provenance={"stdin_supported": "true"},
        )
        for args in args_variants
    ]


def _file_input_proposals(surface: BehaviorSurface) -> list[_CaseProposal]:
    file_inputs = surface.file_inputs or ["input.txt"]
    commands = [
        command.command
        for command in surface.commands
        if command.category == "subcommand" and not command.command.startswith("-")
    ]
    proposals: list[_CaseProposal] = []
    for index, file_input in enumerate(file_inputs[:16]):
        fixture_name = _safe_fixture_name(file_input, fallback=f"input_{index}.txt")
        content = f"sample file payload {index}\n"
        if commands:
            for command in commands[:16]:
                proposals.append(
                    _CaseProposal(
                        args=[command, fixture_name],
                        fixture_files={fixture_name: content},
                        source="agentic-file-input",
                        behavior_category="file-input",
                    )
                )
        else:
            proposals.append(
                _CaseProposal(
                    args=[fixture_name],
                    fixture_files={fixture_name: content},
                    source="agentic-file-input",
                    behavior_category="file-input",
                )
            )
    return proposals


def _env_config_proposals(surface: BehaviorSurface) -> list[_CaseProposal]:
    proposals: list[_CaseProposal] = []
    help_args = ["--help"] if "--help" in surface.global_flags else []
    env_vars = [_safe_env_name(name) for name in surface.env_vars]
    for env_name in [name for name in env_vars if name][:24]:
        proposals.append(
            _CaseProposal(
                args=help_args,
                env={env_name: "pbgen-sample-value"},
                source="agentic-env",
                behavior_category="env",
                provenance={"env_var": env_name},
            )
        )
    config_flags = [
        flag
        for flag in surface.global_flags
        if flag in {"--config", "-c", "--config-file"}
    ]
    for index, config_file in enumerate(surface.config_files[:12]):
        fixture_name = _safe_fixture_name(config_file, fallback=f"config_{index}.json")
        args = [config_flags[0], fixture_name] if config_flags else [fixture_name]
        proposals.append(
            _CaseProposal(
                args=args,
                fixture_files={fixture_name: '{"mode": "sample"}\n'},
                source="agentic-config",
                behavior_category="config",
            )
        )
    return proposals


def _side_effect_proposals(surface: BehaviorSurface) -> list[_CaseProposal]:
    if not surface.side_effects:
        return []
    proposals: list[_CaseProposal] = []
    side_terms = _gap_terms(" ".join(surface.side_effects).lower())
    for command in surface.commands:
        if command.command.startswith("-"):
            continue
        if _term_variants(command.command) & side_terms:
            proposals.append(
                _CaseProposal(
                    args=[command.command],
                    source="agentic-side-effect",
                    behavior_category="side-effect",
                    provenance={"side_effects": " ".join(surface.side_effects[:4])},
                )
            )
    return proposals


def _coverage_edge_proposals(
    surface: BehaviorSurface,
    gaps: list[CoverageGap],
) -> list[_CaseProposal]:
    if not gaps:
        return []
    gap_text = _gap_text(gaps)
    proposals: list[_CaseProposal] = []
    for command in surface.commands:
        if command.command.startswith("-") or command.category != "subcommand":
            continue
        plan = _signature_plan_for_command(command)
        if plan is None or not plan.numeric:
            continue
        for value in ["0", "-1", "999999999", INVALID_NUMERIC_VALUE]:
            proposals.append(
                _CaseProposal(
                    args=[command.command, value],
                    source="agentic-coverage-gap",
                    behavior_category="edge-case",
                    provenance={"coverage_gap": gap_text[:500]},
                )
            )
    return proposals


def _observe_and_revise_proposals(
    prompt: TestGenerationPrompt,
    proposals: list[_CaseProposal],
    executable_path: Path | None,
    *,
    revision_rounds: int,
) -> tuple[list[ExecutableTestCase], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    accepted: list[ExecutableTestCase] = []
    seen: set[str] = set()
    if executable_path is None:
        diagnostics.append(
            {
                "accepted": False,
                "reason": "gold executable unavailable; no tests written",
            }
        )
        return [], diagnostics
    for index, proposal in enumerate(proposals):
        observed: ExecutableTestCase | None = None
        last_error: str | None = None
        revision_round = 0
        for revision_round, revised_proposal in enumerate(
            _proposal_revision_attempts(proposal, revision_rounds)
        ):
            try:
                case = _case_from_proposal(prompt, revised_proposal, index)
                observed = _observe_case_on_gold(case, executable_path)
                break
            except TestGenerationError as exc:
                last_error = str(exc)
        if observed is None:
            diagnostics.append(
                {
                    "accepted": False,
                    "proposal_index": index,
                    "args": proposal.args,
                    "reason": last_error or "proposal could not be observed on gold",
                }
            )
            continue
        signature = _case_signature(observed)
        if signature in seen:
            diagnostics.append(
                {
                    "accepted": False,
                    "proposal_index": index,
                    "test_id": observed.test_id,
                    "args": observed.args,
                    "reason": "duplicate observed behavior",
                }
            )
            continue
        seen.add(signature)
        accepted.append(observed)
        diagnostics.append(
            {
                "accepted": True,
                "proposal_index": index,
                "test_id": observed.test_id,
                "args": observed.args,
                "behavior_category": observed.behavior_category,
                "revision_round": revision_round,
                "revision": "expected behavior replaced with observed gold behavior",
            }
        )
    return accepted, diagnostics


def _proposal_revision_attempts(
    proposal: _CaseProposal,
    revision_rounds: int,
) -> list[_CaseProposal]:
    attempts = [proposal]
    if proposal.stdin:
        attempts.append(
            replace(
                proposal,
                stdin="sample stdin\n",
                provenance={
                    **(proposal.provenance or {}),
                    "revision": "simplified stdin payload",
                },
            )
        )
    if proposal.fixture_files:
        attempts.append(
            replace(
                proposal,
                fixture_files={name: "sample\n" for name in proposal.fixture_files},
                provenance={
                    **(proposal.provenance or {}),
                    "revision": "simplified fixture payload",
                },
            )
        )
    if proposal.args and proposal.args[-1] == INVALID_NUMERIC_VALUE:
        attempts.append(
            replace(
                proposal,
                args=[*proposal.args[:-1], "0"],
                provenance={
                    **(proposal.provenance or {}),
                    "revision": "replaced invalid numeric edge with zero",
                },
            )
        )
    return attempts[: max(1, revision_rounds)]


def _case_from_proposal(
    prompt: TestGenerationPrompt,
    proposal: _CaseProposal,
    index: int,
) -> ExecutableTestCase:
    return ExecutableTestCase(
        test_id=_test_name_from_args(proposal.args, index, prompt.iteration),
        task_id=prompt.task_id,
        args=proposal.args,
        stdin=proposal.stdin,
        env=proposal.env or {},
        fixture_files=proposal.fixture_files or {},
        expected_exit_code=0,
        behavior_category=proposal.behavior_category,
        source=proposal.source,
        source_path=proposal.source_path,
        provenance=proposal.provenance or {},
    )


def _observe_case_on_gold(case: ExecutableTestCase, executable_path: Path) -> ExecutableTestCase:
    with tempfile.TemporaryDirectory(prefix="pbgen-agentic-case-") as temp_dir:
        cwd = Path(temp_dir)
        fixture_error = _write_fixture_files(cwd, case.fixture_files)
        if fixture_error:
            raise TestGenerationError(fixture_error)
        env = os.environ.copy()
        env.update(case.env)
        try:
            result = run_command(
                [str(executable_path), *case.args],
                cwd=cwd,
                env=env,
                stdin=case.stdin,
                timeout_seconds=case.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TestGenerationError(
                f"gold observation timed out after {case.timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise TestGenerationError(f"could not observe gold behavior: {exc}") from exc
    if len(result.stdout) + len(result.stderr) > MAX_CAPTURED_OUTPUT_CHARS:
        raise TestGenerationError("gold output exceeded capture limit")
    return case.model_copy(
        update={
            "expected_exit_code": result.returncode,
            "expected_stdout": ExpectedOutput(exact=result.stdout),
            "expected_stderr": ExpectedOutput(exact=result.stderr),
            "provenance": {
                **case.provenance,
                "gold_observed": "true",
                "revision": "observed_gold_behavior",
            },
        }
    )


def _examples_for_prompt(
    prompt: TestGenerationPrompt,
    *,
    budget: int = DEFAULT_AGENTIC_CANDIDATE_BUDGET,
) -> list[CommandExample]:
    examples = [
        example
        for example in prompt.behavior_surface.command_examples
        if _is_safe_example(prompt, example.args)
    ]
    examples.extend(_examples_from_surface(prompt.behavior_surface))
    examples.extend(_examples_for_gaps(prompt.behavior_surface, prompt.coverage_gaps))
    if not examples:
        examples.extend(_fallback_examples(prompt.behavior_surface))
    return _dedupe_examples(examples)[: max(1, budget)]


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


def _is_safe_proposal(prompt: TestGenerationPrompt, proposal: _CaseProposal) -> bool:
    if not _is_safe_example(prompt, proposal.args):
        return False
    for fixture_path in (proposal.fixture_files or {}):
        path = Path(fixture_path)
        if path.is_absolute() or ".." in path.parts:
            return False
    for key in (proposal.env or {}):
        if _safe_env_name(key) != key:
            return False
    values = [
        *proposal.args,
        proposal.stdin,
        *(proposal.env or {}).values(),
        *(proposal.fixture_files or {}).keys(),
    ]
    if any(value.startswith(("http://", "https://", "ssh://", "git@")) for value in values):
        return False
    return True


def _write_fixture_files(cwd: Path, fixture_files: dict[str, str]) -> str | None:
    for relative, content in fixture_files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            return f"unsafe fixture path: {relative}"
        target = cwd / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return None


def _safe_fixture_name(value: str, *, fallback: str) -> str:
    name = value.strip() or fallback
    name = name.replace("\\", "/")
    name = name.rsplit("/", maxsplit=1)[-1]
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    if not name:
        name = fallback
    if "." not in name:
        name = f"{name}.txt"
    return name


def _safe_env_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip().upper())
    if not cleaned:
        return ""
    if cleaned[0].isdigit():
        cleaned = f"PBGEN_{cleaned}"
    return cleaned


def _dedupe_proposals(proposals: list[_CaseProposal]) -> list[_CaseProposal]:
    seen: set[str] = set()
    deduped: list[_CaseProposal] = []
    for proposal in proposals:
        signature = _proposal_signature(proposal)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(proposal)
    return deduped


def _proposal_signature(proposal: _CaseProposal) -> str:
    return repr(
        (
            proposal.args,
            proposal.stdin,
            sorted((proposal.env or {}).items()),
            sorted((proposal.fixture_files or {}).items()),
        )
    )


def _case_signature(case: ExecutableTestCase) -> str:
    return repr(
        (
            case.args,
            case.stdin,
            sorted(case.env.items()),
            sorted(case.fixture_files.items()),
            case.expected_exit_code,
            case.expected_stdout.model_dump(mode="json"),
            case.expected_stderr.model_dump(mode="json"),
        )
    )


def _test_name_from_args(args: list[str], index: int, iteration: int) -> str:
    tokens = [re.sub(r"[^a-zA-Z0-9_]+", "_", token).strip("_") for token in args[:3]]
    suffix = "_".join(token.lower() for token in tokens if token) or "empty"
    return f"test_iter_{iteration}_{index}_{suffix}"


def _write_agentic_diagnostics(
    output_dir: Path,
    iteration: int,
    diagnostics: list[dict[str, Any]],
) -> None:
    accepted = sum(1 for item in diagnostics if item.get("accepted") is True)
    rejected = sum(1 for item in diagnostics if item.get("accepted") is False)
    write_data(
        _agentic_diagnostic_path(output_dir, iteration),
        {
            "iteration": iteration,
            "prompt_version": AgenticTestGenerationBackend.prompt_version,
            "accepted": accepted,
            "rejected": rejected,
            "diagnostics": diagnostics,
        },
    )


def _agentic_diagnostic_path(output_dir: Path, iteration: int) -> Path:
    return output_dir.parent / "reports" / f"agentic_generation_iteration_{iteration}.json"


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
