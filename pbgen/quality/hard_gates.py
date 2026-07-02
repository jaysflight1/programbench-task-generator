"""Hard quality gates that filter generated tests before final evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.logging.event_log import EventLogger
from pbgen.quality.dummy_runner import DummyBinaryRunner
from pbgen.quality.gold_determinism import run_gold_determinism_details
from pbgen.schemas import (
    AssertionLintReport,
    DummyRunReport,
    ExecutableTestCase,
    ExecutableTestSuite,
    GoldDeterminismReport,
    HardGateRejectedTest,
    HardGateReport,
    LintSeverity,
    TestArtifactRecord,
)
from pbgen.serialization import read_data, write_data
from pbgen.testgen.test_writer import render_pytest_compatibility


@dataclass(frozen=True)
class HardGateResult:
    """Hard gate report plus source metric reports used by callers."""

    report: HardGateReport
    determinism_report: GoldDeterminismReport
    dummy_report: DummyRunReport


def apply_hard_quality_gates(
    *,
    task_id: str,
    tests_path: Path,
    executable_path: Path,
    lint_report: AssertionLintReport,
    dummy_work_dir: Path,
    report_path: Path,
    event_log_path: Path,
    config: PBGenConfig,
    iteration: int | None = None,
) -> HardGateResult:
    """Reject generated tests that fail hard quality gates."""

    determinism_report = run_gold_determinism_details(
        task_id,
        tests_path,
        executable_path,
        event_log_path,
        config,
        iteration=iteration,
    )
    dummy_report = DummyBinaryRunner().run_details(
        task_id,
        tests_path,
        dummy_work_dir,
        event_log_path,
        iteration=iteration,
    )
    rejection_reasons = _rejection_reasons(
        lint_report,
        determinism_report,
        dummy_report,
    )
    accepted_count, canonical_filter_applied = _filter_canonical_suites(
        tests_path,
        set(rejection_reasons),
    )
    rejected_tests = [
        HardGateRejectedTest(test_id=test_id, reasons=reasons)
        for test_id, reasons in sorted(rejection_reasons.items())
    ]
    report = HardGateReport(
        task_id=task_id,
        iteration=iteration,
        suite_passed=accepted_count > 0 and not rejected_tests,
        accepted_test_count=accepted_count,
        rejected_test_count=len(rejected_tests),
        rejected_tests=rejected_tests,
        canonical_filter_applied=canonical_filter_applied,
    )
    write_data(report_path, report.model_dump(mode="json"))
    EventLogger(event_log_path).append(
        task_id=task_id,
        stage="quality",
        event_type="hard_quality_gate_applied",
        iteration=iteration,
        metrics={
            "accepted_test_count": accepted_count,
            "rejected_test_count": len(rejected_tests),
            "canonical_filter_applied": canonical_filter_applied,
            "suite_passed": report.suite_passed,
        },
        qc_flags=["hard_gate_rejections"] if rejected_tests else [],
    )
    return HardGateResult(
        report=report,
        determinism_report=determinism_report,
        dummy_report=dummy_report,
    )


def _rejection_reasons(
    lint_report: AssertionLintReport,
    determinism_report: GoldDeterminismReport,
    dummy_report: DummyRunReport,
) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    for flag in lint_report.flags:
        if flag.severity != LintSeverity.HIGH:
            continue
        test_id = flag.test_name or "unknown"
        reasons.setdefault(test_id, []).append(f"high assertion lint: {flag.rule_id}")
    for test_id, deterministic in determinism_report.per_test_deterministic.items():
        if not deterministic:
            reasons.setdefault(test_id, []).append("gold determinism failed")
    for test_id, passed_dummy in dummy_report.per_test_dummy_passes.items():
        if passed_dummy:
            reasons.setdefault(test_id, []).append("passed at least one dummy executable")
    return reasons


def _filter_canonical_suites(tests_path: Path, rejected_test_ids: set[str]) -> tuple[int, bool]:
    suites = _suite_paths(tests_path)
    if not suites:
        return 0, False
    accepted_count = 0
    filter_applied = False
    for suite_path in suites:
        suite = ExecutableTestSuite.model_validate(read_data(suite_path))
        accepted_cases = [
            case for case in suite.cases if case.test_id not in rejected_test_ids
        ]
        accepted_count += len(accepted_cases)
        if len(accepted_cases) == len(suite.cases):
            continue
        filter_applied = True
        filtered_suite = suite.model_copy(update={"cases": accepted_cases})
        write_data(suite_path, filtered_suite.model_dump(mode="json"))
        _rewrite_rendered_tests(suite_path, accepted_cases)
    return accepted_count, filter_applied


def _suite_paths(tests_path: Path) -> list[Path]:
    roots = [tests_path] if tests_path.is_dir() else [tests_path.parent]
    paths: list[Path] = []
    for root in roots:
        for path in sorted(root.glob("test_cases_iteration*.json")):
            if path.name.endswith("_artifact.json"):
                continue
            paths.append(path)
    return paths


def _rewrite_rendered_tests(
    suite_path: Path,
    accepted_cases: list[ExecutableTestCase],
) -> None:
    artifact_path = suite_path.with_name(f"{suite_path.stem}_artifact.json")
    if not artifact_path.exists():
        return
    record = TestArtifactRecord.model_validate(read_data(artifact_path))
    rendered = render_pytest_compatibility(accepted_cases)
    for rendered_path in record.rendered_paths:
        rendered_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_path.write_text(rendered, encoding="utf-8")
