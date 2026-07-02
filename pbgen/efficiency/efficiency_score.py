"""Correctness-gated efficiency score calculation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.efficiency.runtime_runner import median_corpus_runtime_ms
from pbgen.eval.executable_runner import load_canonical_suites
from pbgen.schemas import EfficiencyResult, ExecutableTestCase
from pbgen.serialization import write_data


def score_efficiency(
    task_id: str,
    reference_executable: Path,
    candidate_executable: Path,
    correctness_score: float,
    output_path: Path,
    config: PBGenConfig,
    *,
    benchmark_commands: Sequence[Sequence[str]] | None = None,
    accepted_test_cases_path: Path | None = None,
) -> EfficiencyResult:
    """Score efficiency only when correctness has passed the configured gate."""

    if correctness_score < config.correctness_gate_for_efficiency:
        result = EfficiencyResult(
            task_id=task_id,
            eligible=False,
            reason=(
                f"correctness {correctness_score:.3f} below gate "
                f"{config.correctness_gate_for_efficiency:.3f}"
            ),
        )
        write_data(output_path, result.model_dump(mode="json"))
        return result

    commands, command_sources = _benchmark_command_corpus(
        benchmark_commands,
        accepted_test_cases_path=accepted_test_cases_path,
    )
    if not commands:
        result = EfficiencyResult(
            task_id=task_id,
            eligible=False,
            reason="no benchmark commands available",
            benchmark_command_count=0,
            benchmark_command_sources=[],
        )
        write_data(output_path, result.model_dump(mode="json"))
        return result

    reference_ms = median_corpus_runtime_ms(
        reference_executable,
        commands,
        trials=config.benchmark_trials,
        warmups=config.benchmark_warmups,
    )
    candidate_ms = median_corpus_runtime_ms(
        candidate_executable,
        commands,
        trials=config.benchmark_trials,
        warmups=config.benchmark_warmups,
    )
    ratio = reference_ms / candidate_ms if candidate_ms > 0 else config.efficiency_multiplier_max
    multiplier = max(config.efficiency_multiplier_min, min(config.efficiency_multiplier_max, ratio))
    result = EfficiencyResult(
        task_id=task_id,
        eligible=True,
        reason=None,
        benchmark_command_count=len(commands),
        benchmark_command_sources=command_sources,
        reference_median_runtime_ms=reference_ms,
        candidate_median_runtime_ms=candidate_ms,
        runtime_ratio=ratio,
        efficiency_multiplier=multiplier,
    )
    write_data(output_path, result.model_dump(mode="json"))
    return result


def _benchmark_command_corpus(
    benchmark_commands: Sequence[Sequence[str]] | None,
    *,
    accepted_test_cases_path: Path | None = None,
) -> tuple[list[list[str]], list[str]]:
    commands: list[list[str]] = []
    sources: list[str] = []

    for command_args in benchmark_commands or []:
        _append_unique_command(commands, sources, list(command_args), "declared_benchmark")

    if accepted_test_cases_path is not None:
        for case in _load_efficiency_candidate_cases(accepted_test_cases_path):
            _append_unique_command(commands, sources, list(case.args), "accepted_test_case")

    return commands, sorted(set(sources))


def _load_efficiency_candidate_cases(tests_path: Path) -> list[ExecutableTestCase]:
    cases: list[ExecutableTestCase] = []
    for suite in load_canonical_suites(tests_path):
        for case in suite.cases:
            if _case_is_runtime_safe(case):
                cases.append(case)
    return cases


def _case_is_runtime_safe(case: ExecutableTestCase) -> bool:
    return (
        case.expected_exit_code == 0
        and not case.stdin
        and not case.env
        and not case.fixture_files
        and case.timeout_seconds > 0
    )


def _append_unique_command(
    commands: list[list[str]],
    sources: list[str],
    command_args: list[str],
    source: str,
) -> None:
    if command_args not in commands:
        commands.append(command_args)
    sources.append(source)
