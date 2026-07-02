"""Correctness-gated efficiency score calculation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.efficiency.runtime_runner import median_corpus_runtime_ms
from pbgen.schemas import EfficiencyResult
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

    commands = _benchmark_command_corpus(benchmark_commands)
    if not commands:
        result = EfficiencyResult(
            task_id=task_id,
            eligible=False,
            reason="no benchmark commands available",
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
        reference_median_runtime_ms=reference_ms,
        candidate_median_runtime_ms=candidate_ms,
        runtime_ratio=ratio,
        efficiency_multiplier=multiplier,
    )
    write_data(output_path, result.model_dump(mode="json"))
    return result


def _benchmark_command_corpus(
    benchmark_commands: Sequence[Sequence[str]] | None,
) -> list[list[str]]:
    if not benchmark_commands:
        return []
    return [list(command_args) for command_args in benchmark_commands]
