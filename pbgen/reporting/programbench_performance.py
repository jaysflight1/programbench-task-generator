"""ProgramBench-style model performance reporting."""

from __future__ import annotations

from pathlib import Path

from pbgen.eval.programbench_metrics import (
    aggregate_programbench_metrics,
    programbench_metrics_from_candidate_report,
)
from pbgen.schemas import CandidateEvaluationReport, ProgramBenchModelPerformanceReport
from pbgen.serialization import read_data, write_data


def write_programbench_performance_report(
    candidate_report_paths: list[Path],
    output_path: Path,
    *,
    model_name: str | None = None,
) -> tuple[ProgramBenchModelPerformanceReport, Path, Path]:
    """Write JSON and Markdown ProgramBench model-performance reports."""

    metrics = []
    for path in candidate_report_paths:
        candidate = CandidateEvaluationReport.model_validate(read_data(path))
        metrics.append(
            candidate.programbench_metrics
            or programbench_metrics_from_candidate_report(candidate)
        )
    report = aggregate_programbench_metrics(metrics, model_name=model_name)
    json_path, markdown_path = _output_paths(output_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    write_data(json_path, report.model_dump(mode="json"))
    markdown_path.write_text(render_programbench_performance_markdown(report), encoding="utf-8")
    return report, json_path, markdown_path


def render_programbench_performance_markdown(
    report: ProgramBenchModelPerformanceReport,
) -> str:
    """Render a compact ProgramBench-style model-performance table."""

    lines = [
        "# ProgramBench Model Performance",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Model | {_fmt(report.model_name)} |",
        f"| Tasks | {report.task_count} |",
        f"| % Resolved | {_pct(report.percent_resolved)} |",
        f"| % Almost Resolved | {_pct(report.percent_almost_resolved)} |",
        f"| Macro avg % Tests Passed | {_pct(report.macro_average_test_pass_rate)} |",
        f"| Micro avg % Tests Passed | {_pct(report.micro_average_test_pass_rate)} |",
        f"| Build success rate | {_pct(report.build_success_rate)} |",
        f"| Disqualification rate | {_pct(report.disqualification_rate)} |",
        f"| Avg API calls/task | {_num(report.average_api_calls_per_task)} |",
        f"| Avg cost/task | {_money(report.average_cost_usd_per_task)} |",
        "",
        "## Task Instances",
        "",
        "| Task | Resolved | Almost | Tests Passed | Build | Disqualified | Calls | Cost |",
        "|---|---|---|---:|---|---|---:|---:|",
    ]
    for item in report.task_metrics:
        lines.append(
            "| "
            f"{item.task_id} | "
            f"{_yes_no(item.resolved)} | "
            f"{_yes_no(item.almost_resolved)} | "
            f"{_pct(item.test_pass_rate)} | "
            f"{_yes_no(item.build_success)} | "
            f"{_yes_no(item.disqualified)} | "
            f"{_fmt(item.api_calls)} | "
            f"{_money(item.cost_usd)} |"
        )
    lines.extend(
        [
            "",
            "% Resolved requires all hidden tests to pass and no cheating/disqualification flag. "
            "% Almost Resolved uses the ProgramBench 95% test-pass threshold.",
            "",
        ]
    )
    return "\n".join(lines)


def _output_paths(output_path: Path) -> tuple[Path, Path]:
    if output_path.suffix == ".json":
        return output_path, output_path.with_suffix(".md")
    if output_path.suffix == ".md":
        return output_path.with_suffix(".json"), output_path
    return output_path / "programbench_performance_report.json", output_path / "PROGRAMBENCH_PERFORMANCE.md"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _fmt(value: object | None) -> str:
    return "n/a" if value is None else str(value)
