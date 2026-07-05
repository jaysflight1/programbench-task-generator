from __future__ import annotations

import pytest

from pbgen.eval.programbench_metrics import (
    aggregate_programbench_metrics,
    programbench_metrics_from_candidate_report,
)
from pbgen.schemas import CandidateEvaluationReport


def test_programbench_metrics_track_resolved_and_almost_thresholds() -> None:
    resolved = _candidate("demo-a", passed=20, total=20, model_name="model-a")
    almost = _candidate("demo-b", passed=19, total=20, model_name="model-a")
    failed = _candidate("demo-c", passed=1, total=20, model_name="model-a")

    resolved_metrics = programbench_metrics_from_candidate_report(resolved)
    almost_metrics = programbench_metrics_from_candidate_report(almost)
    failed_metrics = programbench_metrics_from_candidate_report(failed)

    assert resolved_metrics.resolved is True
    assert resolved_metrics.almost_resolved is True
    assert almost_metrics.resolved is False
    assert almost_metrics.almost_resolved is True
    assert failed_metrics.resolved is False
    assert failed_metrics.almost_resolved is False


def test_disqualified_candidate_scores_as_failure_but_preserves_raw_pass_rate() -> None:
    report = _candidate(
        "demo",
        passed=10,
        total=10,
        cheating_flagged=True,
        disqualification_reason="source lookup",
    )

    metrics = programbench_metrics_from_candidate_report(report)

    assert metrics.disqualified is True
    assert metrics.resolved is False
    assert metrics.almost_resolved is False
    assert metrics.test_pass_rate == 0.0
    assert metrics.raw_test_pass_rate == 1.0
    assert metrics.tests_passed == 0
    assert metrics.disqualification_reason == "source lookup"


def test_aggregate_programbench_metrics_matches_table_level_definitions() -> None:
    metrics = [
        programbench_metrics_from_candidate_report(
            _candidate("resolved", passed=20, total=20, model_name="model-a", api_calls=10, cost_usd=1.0)
        ),
        programbench_metrics_from_candidate_report(
            _candidate("almost", passed=19, total=20, model_name="model-a", api_calls=20, cost_usd=3.0)
        ),
        programbench_metrics_from_candidate_report(
            _candidate("failed", passed=1, total=20, model_name="model-a")
        ),
    ]

    report = aggregate_programbench_metrics(metrics)

    assert report.model_name == "model-a"
    assert report.task_count == 3
    assert report.resolved_count == 1
    assert report.almost_resolved_count == 2
    assert report.percent_resolved == pytest.approx(1 / 3)
    assert report.percent_almost_resolved == pytest.approx(2 / 3)
    assert report.macro_average_test_pass_rate == pytest.approx((1.0 + 0.95 + 0.05) / 3)
    assert report.micro_average_test_pass_rate == pytest.approx(40 / 60)
    assert report.average_api_calls_per_task == pytest.approx(15.0)
    assert report.average_cost_usd_per_task == pytest.approx(2.0)


def _candidate(
    task_id: str,
    *,
    passed: int,
    total: int,
    model_name: str | None = None,
    build_success: bool = True,
    api_calls: int | None = None,
    cost_usd: float | None = None,
    cheating_flagged: bool = False,
    disqualification_reason: str | None = None,
) -> CandidateEvaluationReport:
    return CandidateEvaluationReport(
        task_id=task_id,
        resolved=passed == total and total > 0,
        tests_passed=passed,
        total_tests=total,
        pass_rate=passed / total if total else 0.0,
        build_success=build_success,
        runtime_policy="trusted-local",
        model_name=model_name,
        api_calls=api_calls,
        cost_usd=cost_usd,
        cheating_flagged=cheating_flagged,
        disqualification_reason=disqualification_reason,
    )
