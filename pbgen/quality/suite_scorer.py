"""Suite quality, reward shaping, and QC report generation."""

from __future__ import annotations

from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.logging.event_log import EventLogger
from pbgen.qc.qc_queue import build_qc_queue
from pbgen.schemas import (
    AssertionLintReport,
    CoverageReport,
    EfficiencyResult,
    MutationLiteReport,
    QCQueueReport,
    RedundancyReport,
    RewardShapeReport,
    SuiteQualityReport,
    TestRunResult,
)
from pbgen.serialization import write_data


MISSING_COVERAGE_FINAL_SCORE_CAP = 0.90
MISSING_COVERAGE_NOTE = (
    "Coverage was unavailable; final score was capped to avoid overclaiming benchmark quality."
)


def score_suite(
    *,
    task_id: str,
    gold_result: TestRunResult,
    lint_report: AssertionLintReport,
    deterministic_pass_rate: float,
    dummy_pass_rate: float,
    redundancy_report: RedundancyReport,
    efficiency_result: EfficiencyResult,
    mutation_report: MutationLiteReport | None = None,
    coverage_report: CoverageReport | None = None,
    reports_dir: Path,
    qc_dir: Path,
    event_log_path: Path,
    config: PBGenConfig,
) -> tuple[SuiteQualityReport, RewardShapeReport, QCQueueReport]:
    """Compute objective ProgramBench-style quality and reward-shape reports."""

    assertion_strength = _clamp(
        1.0
        - min(1.0, lint_report.high_count / max(gold_result.total_tests, 1))
        - 0.25 * min(1.0, lint_report.medium_count / max(gold_result.total_tests, 1))
    )
    dummy_rejection_score = _clamp(1.0 - dummy_pass_rate)
    redundancy_penalty = redundancy_report.redundancy_score
    correctness_score = gold_result.pass_rate
    correctness_gate_passed = correctness_score >= config.correctness_gate_for_efficiency
    coverage_score = coverage_report.line_coverage if coverage_report else None
    coverage_available = coverage_score is not None
    coverage_component = coverage_score if coverage_score is not None else 0.0

    quality_score = _clamp(
        0.45 * correctness_score
        + 0.25 * assertion_strength
        + 0.10 * coverage_component
        + 0.10 * deterministic_pass_rate
        + 0.10 * dummy_rejection_score
        - 0.05 * redundancy_penalty
    )
    if not correctness_gate_passed:
        final_score = correctness_score
        efficiency_multiplier = None
    else:
        efficiency_multiplier = efficiency_result.efficiency_multiplier or 1.0
        final_score = _clamp(quality_score * efficiency_multiplier)
        if not coverage_available:
            final_score = min(final_score, MISSING_COVERAGE_FINAL_SCORE_CAP)

    qc_report = build_qc_queue(
        task_id,
        lint_report,
        deterministic_pass_rate,
        dummy_pass_rate,
        redundancy_report,
        mutation_survival_rate=(
            mutation_report.mutation_survival_rate if mutation_report else None
        ),
        per_test_mutation_survived=(
            mutation_report.per_test_mutation_survived if mutation_report else None
        ),
    )
    qc_dir.mkdir(parents=True, exist_ok=True)
    suite_report = SuiteQualityReport(
        task_id=task_id,
        num_tests=gold_result.total_tests,
        gold_pass_rate=gold_result.pass_rate,
        dummy_pass_rate=dummy_pass_rate,
        deterministic_pass_rate=deterministic_pass_rate,
        line_coverage=coverage_score,
        assertion_strength_score=assertion_strength,
        high_lint_count=lint_report.high_count,
        medium_lint_count=lint_report.medium_count,
        redundancy_score=redundancy_report.redundancy_score,
        qc_queue_size=len(qc_report.items),
    )
    reward_report = RewardShapeReport(
        task_id=task_id,
        correctness_gate_passed=correctness_gate_passed,
        correctness_score=correctness_score,
        assertion_strength_score=assertion_strength,
        coverage_score=coverage_score,
        redundancy_penalty=redundancy_penalty,
        determinism_score=deterministic_pass_rate,
        dummy_rejection_score=dummy_rejection_score,
        efficiency_multiplier=efficiency_multiplier,
        final_score=final_score,
        notes=[
            (
                _coverage_note(coverage_report)
                if coverage_available
                else MISSING_COVERAGE_NOTE
            ),
            "Economic-importance scoring is intentionally omitted from this implementation.",
            _mutation_note(mutation_report),
        ],
    )

    reports_dir.mkdir(parents=True, exist_ok=True)
    write_data(reports_dir / "suite_quality_report.json", suite_report.model_dump(mode="json"))
    write_data(reports_dir / "reward_shape_report.json", reward_report.model_dump(mode="json"))
    write_data(qc_dir / "qc_queue.json", qc_report.model_dump(mode="json"))
    EventLogger(event_log_path).append(
        task_id=task_id,
        stage="quality",
        event_type="suite_finalized",
        metrics={"final_score": final_score, "qc_queue_size": len(qc_report.items)},
    )
    return suite_report, reward_report, qc_report


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _coverage_note(coverage_report: CoverageReport | None) -> str:
    backend = coverage_report.coverage_backend if coverage_report else None
    if backend:
        return f"Coverage was measured with {backend} and included in the quality score."
    return "Coverage was measured and included in the quality score."


def _mutation_note(mutation_report: MutationLiteReport | None) -> str:
    if mutation_report is None:
        return "Mutation-lite checks were not available for this suite."
    return (
        "Mutation-lite checks ran against "
        f"{mutation_report.mutation_count} synthetic wrong executables; "
        f"survival rate was {mutation_report.mutation_survival_rate:.3f}."
    )
