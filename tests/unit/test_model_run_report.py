from __future__ import annotations

from pathlib import Path

from pbgen.reporting.model_run_report import write_model_run_report
from pbgen.serialization import write_data


def test_write_model_run_report_compares_baseline_and_model(tmp_path: Path) -> None:
    baseline = _artifact(
        tmp_path / "baseline",
        task_id="demo_local",
        tests=10,
        coverage=0.4,
        gaps=5,
        score=0.8,
    )
    model = _artifact(
        tmp_path / "model",
        task_id="demo_model",
        tests=14,
        coverage=0.6,
        gaps=3,
        score=0.9,
        model=True,
    )

    output = write_model_run_report([(baseline, model)], tmp_path / "MODEL_RUN_REPORT.md")

    text = output.read_text(encoding="utf-8")
    assert "# Model-Backed Generation Report" in text
    assert "demo_model" in text
    assert "60.0%" in text
    assert "+20.0 pp" in text
    assert "-2" in text
    assert "1/1" in text


def _artifact(
    root: Path,
    *,
    task_id: str,
    tests: int,
    coverage: float,
    gaps: int,
    score: float,
    model: bool = False,
) -> Path:
    reports = root / "reports"
    write_data(
        reports / "suite_quality_report.json",
        {
            "task_id": task_id,
            "num_tests": tests,
            "gold_pass_rate": 1.0,
            "deterministic_pass_rate": 1.0,
            "dummy_pass_rate": 0.0,
            "high_lint_count": 0,
            "medium_lint_count": 0,
            "line_coverage": coverage,
            "redundancy_score": 0.0,
        },
    )
    write_data(reports / "reward_shape_report.json", {"task_id": task_id, "final_score": score})
    write_data(
        reports / "coverage_report_iteration_0.json",
        {
            "task_id": task_id,
            "iteration": 0,
            "coverage_available": True,
            "coverage_backend": "fixture",
            "line_coverage": coverage,
            "gaps": [{"file_path": "demo.py"} for _ in range(gaps)],
        },
    )
    write_data(reports / "leak_check_report.json", {"passed": True})
    if model:
        write_data(
            reports / "model_generation_iteration_0.json",
            {
                "behavior_category_counts": {"flag": 1},
                "diagnostics": [
                    {"accepted": True, "test_id": "test_flag"},
                    {"accepted": False, "reason": "duplicate"},
                ],
            },
        )
        write_data(
            reports / "model_request_iteration_0.json",
            {"client_metadata": {"estimated_cost_usd": 0.01}},
        )
    return root
