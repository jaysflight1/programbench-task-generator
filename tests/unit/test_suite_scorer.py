import pytest

from pbgen import schemas
from pbgen.config import PBGenConfig
from pbgen.quality.suite_scorer import score_suite
from pbgen.serialization import read_data


MISSING_COVERAGE_NOTE = (
    "Coverage was unavailable; final score was capped to avoid overclaiming benchmark quality."
)


@pytest.mark.parametrize(
    "coverage_report",
    [
        None,
        schemas.CoverageReport(task_id="demo", iteration=0, line_coverage=None),
    ],
)
def test_missing_coverage_caps_perfect_correctness_score(tmp_path, coverage_report) -> None:
    suite_report, reward_report, _ = _score_suite(
        tmp_path,
        coverage_report=coverage_report,
        efficiency_multiplier=1.25,
    )

    assert suite_report.line_coverage is None
    assert reward_report.coverage_score is None
    assert reward_report.final_score == pytest.approx(0.90)
    assert reward_report.final_score != 1.0
    assert MISSING_COVERAGE_NOTE in reward_report.notes

    persisted_reward = read_data(tmp_path / "reports" / "reward_shape_report.json")
    assert persisted_reward["coverage_score"] is None
    assert persisted_reward["final_score"] == pytest.approx(0.90)
    assert MISSING_COVERAGE_NOTE in persisted_reward["notes"]


def test_measured_coverage_contributes_normally(tmp_path) -> None:
    suite_report, reward_report, _ = _score_suite(
        tmp_path,
        coverage_report=schemas.CoverageReport(task_id="demo", iteration=0, line_coverage=0.5),
    )

    assert suite_report.line_coverage == pytest.approx(0.5)
    assert reward_report.coverage_score == pytest.approx(0.5)
    assert reward_report.final_score == pytest.approx(0.95)
    assert "Python coverage was measured and included in the quality score." in reward_report.notes
    assert MISSING_COVERAGE_NOTE not in reward_report.notes


def test_missing_coverage_uses_zero_component_before_cap(tmp_path) -> None:
    _, reward_report, _ = _score_suite(
        tmp_path,
        coverage_report=None,
        deterministic_pass_rate=0.0,
    )

    assert reward_report.correctness_gate_passed
    assert reward_report.final_score == pytest.approx(0.80)


def test_missing_coverage_failed_correctness_gate_preserves_correctness_score(tmp_path) -> None:
    _, reward_report, _ = _score_suite(
        tmp_path,
        coverage_report=None,
        efficiency_multiplier=1.25,
        passed_tests=3,
    )

    assert not reward_report.correctness_gate_passed
    assert reward_report.efficiency_multiplier is None
    assert reward_report.final_score == pytest.approx(0.75)


def _score_suite(
    tmp_path,
    *,
    coverage_report: schemas.CoverageReport | None,
    efficiency_multiplier: float = 1.0,
    deterministic_pass_rate: float = 1.0,
    total_tests: int = 4,
    passed_tests: int = 4,
):
    return score_suite(
        task_id="demo",
        gold_result=schemas.TestRunResult(
            task_id="demo",
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=total_tests - passed_tests,
            exit_status=0,
            stdout="",
            stderr="",
        ),
        lint_report=schemas.AssertionLintReport(task_id="demo"),
        deterministic_pass_rate=deterministic_pass_rate,
        dummy_pass_rate=0.0,
        redundancy_report=schemas.RedundancyReport(task_id="demo", items=[], redundancy_score=0.0),
        efficiency_result=schemas.EfficiencyResult(
            task_id="demo",
            eligible=True,
            efficiency_multiplier=efficiency_multiplier,
        ),
        coverage_report=coverage_report,
        reports_dir=tmp_path / "reports",
        qc_dir=tmp_path / "qc",
        event_log_path=tmp_path / "logs" / "events.jsonl",
        config=PBGenConfig(workspace_root=tmp_path),
    )
