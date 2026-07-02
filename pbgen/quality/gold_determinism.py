"""Gold determinism checks for generated tests."""

from __future__ import annotations

from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.logging.event_log import EventLogger
from pbgen.schemas import GoldDeterminismReport, TestRunResult


def run_gold_determinism(
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    event_log_path: Path,
    config: PBGenConfig,
    iteration: int | None = None,
) -> float:
    """Run tests repeatedly against gold and return deterministic pass rate."""

    return run_gold_determinism_details(
        task_id,
        tests_path,
        executable_path,
        event_log_path,
        config,
        iteration=iteration,
    ).deterministic_pass_rate


def run_gold_determinism_details(
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    event_log_path: Path,
    config: PBGenConfig,
    iteration: int | None = None,
) -> GoldDeterminismReport:
    """Run tests repeatedly against gold and return per-test determinism details."""

    results: list[TestRunResult] = []
    for _ in range(config.determinism_runs):
        results.append(run_generated_suite(task_id, tests_path, executable_path))
    deterministic_rate = _per_test_deterministic_pass_rate(results)
    per_test = _per_test_deterministic(results)
    EventLogger(event_log_path).append(
        task_id=task_id,
        stage="quality",
        event_type="determinism_check_run",
        iteration=iteration,
        metrics={
            "runs": config.determinism_runs,
            "pass_rates": [result.pass_rate for result in results],
            "deterministic_pass_rate": deterministic_rate,
            "per_test_deterministic": per_test,
        },
    )
    return GoldDeterminismReport(
        task_id=task_id,
        deterministic_pass_rate=deterministic_rate,
        runs=config.determinism_runs,
        pass_rates=[result.pass_rate for result in results],
        per_test_deterministic=per_test,
    )


def _per_test_deterministic_pass_rate(results: list[TestRunResult]) -> float:
    per_test = _per_test_deterministic(results)
    if not per_test:
        pass_rates = [result.pass_rate for result in results]
        return 1.0 if pass_rates and all(rate == 1.0 for rate in pass_rates) else min(pass_rates or [0.0])
    return sum(1 for passed in per_test.values() if passed) / len(per_test)


def _per_test_deterministic(results: list[TestRunResult]) -> dict[str, bool]:
    if not results:
        return {}

    all_test_ids = {
        outcome.test_id
        for result in results
        for outcome in result.outcomes
    }
    if not all_test_ids:
        return {}

    stable: dict[str, bool] = {}
    for test_id in sorted(all_test_ids):
        observed = [
            outcome.outcome
            for result in results
            for outcome in result.outcomes
            if outcome.test_id == test_id
        ]
        stable[test_id] = len(observed) == len(results) and all(outcome == "passed" for outcome in observed)
    return stable
