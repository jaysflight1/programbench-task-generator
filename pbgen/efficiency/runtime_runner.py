"""Runtime measurement protocol using median measured trials."""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from pathlib import Path

from pbgen.subprocess_utils import run_command


def median_runtime_ms(
    executable: Path,
    command_args: Sequence[str],
    *,
    trials: int = 5,
    warmups: int = 1,
) -> float:
    """Measure median runtime in milliseconds with warmups."""

    for _ in range(warmups):
        run_command([str(executable), *command_args], timeout_seconds=20)
    measurements: list[float] = []
    for _ in range(trials):
        start = time.perf_counter()
        run_command([str(executable), *command_args], timeout_seconds=20)
        measurements.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(measurements)


def median_corpus_runtime_ms(
    executable: Path,
    benchmark_commands: Sequence[Sequence[str]],
    *,
    trials: int = 5,
    warmups: int = 1,
) -> float:
    """Measure each benchmark command and return the median command runtime."""

    command_medians = [
        median_runtime_ms(executable, command_args, trials=trials, warmups=warmups)
        for command_args in benchmark_commands
    ]
    return statistics.median(command_medians)
