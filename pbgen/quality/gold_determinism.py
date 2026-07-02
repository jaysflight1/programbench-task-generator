"""Gold determinism checks for generated tests."""

from __future__ import annotations

from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.logging.event_log import EventLogger


def run_gold_determinism(
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    event_log_path: Path,
    config: PBGenConfig,
    iteration: int | None = None,
) -> float:
    """Run tests repeatedly against gold and return deterministic pass rate."""

    pass_rates: list[float] = []
    for _ in range(config.determinism_runs):
        pass_rates.append(run_generated_suite(task_id, tests_path, executable_path).pass_rate)
    deterministic_rate = 1.0 if pass_rates and all(rate == 1.0 for rate in pass_rates) else min(pass_rates or [0.0])
    EventLogger(event_log_path).append(
        task_id=task_id,
        stage="quality",
        event_type="determinism_check_run",
        iteration=iteration,
        metrics={"runs": config.determinism_runs, "deterministic_pass_rate": deterministic_rate},
    )
    return deterministic_rate
