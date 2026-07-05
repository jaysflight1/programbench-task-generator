from __future__ import annotations

from pathlib import Path

import pytest

from pbgen.eval.programbench_metrics import attach_programbench_metrics
from pbgen.reporting.programbench_performance import write_programbench_performance_report
from pbgen.schemas import CandidateEvaluationReport
from pbgen.serialization import read_data, write_data


def test_write_programbench_performance_report(tmp_path: Path) -> None:
    resolved = _write_candidate_report(
        tmp_path / "resolved.json",
        _candidate("task-a", passed=10, total=10, model_name="model-a", api_calls=10, cost_usd=1.0),
    )
    almost = _write_candidate_report(
        tmp_path / "almost.json",
        _candidate("task-b", passed=19, total=20, model_name="model-a", api_calls=20, cost_usd=3.0),
    )

    report, json_path, markdown_path = write_programbench_performance_report(
        [resolved, almost],
        tmp_path / "PROGRAMBENCH_PERFORMANCE.md",
    )

    assert report.percent_resolved == pytest.approx(0.5)
    assert report.percent_almost_resolved == pytest.approx(1.0)
    assert report.average_api_calls_per_task == pytest.approx(15.0)
    assert json_path == tmp_path / "PROGRAMBENCH_PERFORMANCE.json"
    persisted = read_data(json_path)
    assert persisted["resolved_count"] == 1
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "% Resolved" in markdown
    assert "| task-b | no | yes | 95.0% | yes | no | 20 | $3.0000 |" in markdown


def _write_candidate_report(path: Path, report: CandidateEvaluationReport) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_data(path, attach_programbench_metrics(report).model_dump(mode="json"))
    return path


def _candidate(
    task_id: str,
    *,
    passed: int,
    total: int,
    model_name: str,
    api_calls: int,
    cost_usd: float,
) -> CandidateEvaluationReport:
    return CandidateEvaluationReport(
        task_id=task_id,
        resolved=passed == total and total > 0,
        tests_passed=passed,
        total_tests=total,
        pass_rate=passed / total,
        build_success=True,
        runtime_policy="trusted-local",
        model_name=model_name,
        api_calls=api_calls,
        cost_usd=cost_usd,
    )
