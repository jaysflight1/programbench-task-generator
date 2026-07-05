"""ProgramBench model-performance metrics for candidate evaluations."""

from __future__ import annotations

from collections.abc import Iterable

from pbgen.schemas import (
    CandidateEvaluationReport,
    ProgramBenchEvaluationMetrics,
    ProgramBenchModelPerformanceReport,
)


ALMOST_RESOLVED_THRESHOLD = 0.95


def programbench_metrics_from_candidate_report(
    report: CandidateEvaluationReport,
) -> ProgramBenchEvaluationMetrics:
    """Convert one candidate evaluation into ProgramBench-facing metrics."""

    disqualified = report.cheating_flagged or report.disqualification_reason is not None
    scored_pass_rate = 0.0 if disqualified else report.pass_rate
    return ProgramBenchEvaluationMetrics(
        task_id=report.task_id,
        model_name=report.model_name,
        attempt_id=report.attempt_id,
        resolved=report.build_success and report.resolved and not disqualified,
        almost_resolved=(
            report.build_success
            and scored_pass_rate >= ALMOST_RESOLVED_THRESHOLD
            and not disqualified
        ),
        test_pass_rate=scored_pass_rate,
        raw_test_pass_rate=report.pass_rate,
        tests_passed=0 if disqualified else report.tests_passed,
        total_tests=report.total_tests,
        build_success=report.build_success,
        cheating_flagged=report.cheating_flagged,
        disqualified=disqualified,
        disqualification_reason=report.disqualification_reason,
        api_calls=report.api_calls,
        cost_usd=report.cost_usd,
    )


def attach_programbench_metrics(
    report: CandidateEvaluationReport,
) -> CandidateEvaluationReport:
    """Return a copy of a candidate report with ProgramBench metrics attached."""

    return report.model_copy(
        update={"programbench_metrics": programbench_metrics_from_candidate_report(report)}
    )


def aggregate_programbench_metrics(
    metrics: Iterable[ProgramBenchEvaluationMetrics],
    *,
    model_name: str | None = None,
) -> ProgramBenchModelPerformanceReport:
    """Aggregate per-task ProgramBench metrics into model-level reporting metrics."""

    task_metrics = list(metrics)
    task_count = len(task_metrics)
    resolved_count = sum(1 for item in task_metrics if item.resolved)
    almost_resolved_count = sum(1 for item in task_metrics if item.almost_resolved)
    build_success_count = sum(1 for item in task_metrics if item.build_success)
    disqualified_count = sum(1 for item in task_metrics if item.disqualified)
    total_tests = sum(item.total_tests for item in task_metrics)
    total_tests_passed = sum(item.tests_passed for item in task_metrics)
    api_calls = [item.api_calls for item in task_metrics if item.api_calls is not None]
    costs = [item.cost_usd for item in task_metrics if item.cost_usd is not None]
    return ProgramBenchModelPerformanceReport(
        model_name=model_name or _single_model_name(task_metrics),
        task_count=task_count,
        resolved_count=resolved_count,
        almost_resolved_count=almost_resolved_count,
        percent_resolved=_rate(resolved_count, task_count),
        percent_almost_resolved=_rate(almost_resolved_count, task_count),
        macro_average_test_pass_rate=(
            sum(item.test_pass_rate for item in task_metrics) / task_count
            if task_count
            else 0.0
        ),
        micro_average_test_pass_rate=_rate(total_tests_passed, total_tests),
        build_success_rate=_rate(build_success_count, task_count),
        disqualified_count=disqualified_count,
        disqualification_rate=_rate(disqualified_count, task_count),
        total_api_calls=sum(api_calls) if api_calls else None,
        average_api_calls_per_task=(sum(api_calls) / len(api_calls) if api_calls else None),
        total_cost_usd=sum(costs) if costs else None,
        average_cost_usd_per_task=(sum(costs) / len(costs) if costs else None),
        task_metrics=task_metrics,
    )


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _single_model_name(metrics: list[ProgramBenchEvaluationMetrics]) -> str | None:
    names = {item.model_name for item in metrics if item.model_name}
    return next(iter(names)) if len(names) == 1 else None
