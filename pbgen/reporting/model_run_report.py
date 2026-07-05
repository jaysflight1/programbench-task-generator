"""Markdown comparison report for raw model-backed task generation runs."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from pbgen.serialization import read_data


_COVERAGE_PATTERN = re.compile(r"coverage_report_iteration_(\d+)\.json$")
_MODEL_DIAGNOSTICS_PATTERN = re.compile(r"model_generation_iteration_(\d+)\.json$")
_MODEL_REQUEST_PATTERN = re.compile(r"model_request_iteration_(\d+)\.json$")


def write_model_run_report(
    pairs: list[tuple[Path, Path]],
    output_path: Path,
) -> Path:
    """Write a baseline-vs-model report for one or more artifact pairs."""

    rows = [_comparison_row(baseline, model) for baseline, model in pairs]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_report(rows), encoding="utf-8")
    return output_path


def _comparison_row(baseline: Path, model: Path) -> dict[str, Any]:
    baseline_metrics = _artifact_metrics(baseline)
    model_metrics = _artifact_metrics(model)
    return {
        "baseline_path": baseline.as_posix(),
        "model_path": model.as_posix(),
        "task": model_metrics["task_id"] or model.name,
        "baseline": baseline_metrics,
        "model": model_metrics,
        "coverage_delta": _delta(model_metrics["line_coverage"], baseline_metrics["line_coverage"]),
        "gap_delta": _delta(model_metrics["coverage_gaps"], baseline_metrics["coverage_gaps"]),
    }


def _artifact_metrics(root: Path) -> dict[str, Any]:
    reports = root / "reports"
    suite = _read_optional(reports / "suite_quality_report.json")
    reward = _read_optional(reports / "reward_shape_report.json")
    coverage = _latest_report(reports, _COVERAGE_PATTERN)
    diagnostics = [_read_optional(path) for path in _iteration_reports(reports, _MODEL_DIAGNOSTICS_PATTERN)]
    requests = [_read_optional(path) for path in _iteration_reports(reports, _MODEL_REQUEST_PATTERN)]
    leak = _read_optional(reports / "leak_check_report.json")
    docker = _read_optional(reports / "no_network_validation_report.json")
    accepted = 0
    rejected = 0
    category_counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        for item in diagnostic.get("diagnostics", []):
            if not isinstance(item, dict):
                continue
            if item.get("accepted") is True:
                accepted += 1
            else:
                rejected += 1
        for category, count in diagnostic.get("behavior_category_counts", {}).items():
            if isinstance(category, str) and isinstance(count, int):
                category_counts[category] = category_counts.get(category, 0) + count
    return {
        "task_id": suite.get("task_id") or reward.get("task_id"),
        "num_tests": suite.get("num_tests"),
        "gold_pass_rate": suite.get("gold_pass_rate"),
        "deterministic_pass_rate": suite.get("deterministic_pass_rate"),
        "dummy_pass_rate": suite.get("dummy_pass_rate"),
        "high_lint_count": suite.get("high_lint_count"),
        "medium_lint_count": suite.get("medium_lint_count"),
        "redundancy_score": suite.get("redundancy_score"),
        "line_coverage": suite.get("line_coverage") or coverage.get("line_coverage"),
        "coverage_available": coverage.get("coverage_available"),
        "coverage_backend": coverage.get("coverage_backend"),
        "coverage_gaps": _coverage_gap_count(coverage),
        "final_score": reward.get("final_score"),
        "accepted_model_proposals": accepted,
        "rejected_model_proposals": rejected,
        "behavior_category_counts": category_counts,
        "model_cost_usd": _sum_model_cost(requests),
        "leak_check_passed": leak.get("passed"),
        "docker_no_network_status": docker.get("status") or "not recorded",
    }


def _render_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Model-Backed Generation Report",
        "",
        "This report compares deterministic local baselines against raw model-backed runs. "
        "Human QC, if performed, should be recorded as a separate curated artifact set.",
        "",
        "## Headline Comparison",
        "",
        "| Task | Tests | Gold | Determinism | Dummy | High lint | Coverage | Gaps | Score | Model accepted/rejected | Leak | Docker/no-network |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        model = row["model"]
        lines.append(
            "| "
            f"{row['task']} | "
            f"{_fmt(model['num_tests'])} | "
            f"{_pct(model['gold_pass_rate'])} | "
            f"{_pct(model['deterministic_pass_rate'])} | "
            f"{_pct(model['dummy_pass_rate'])} | "
            f"{_fmt(model['high_lint_count'])} | "
            f"{_pct(model['line_coverage'])} | "
            f"{_fmt(model['coverage_gaps'])} | "
            f"{_pct(model['final_score'])} | "
            f"{_fmt(model['accepted_model_proposals'])}/{_fmt(model['rejected_model_proposals'])} | "
            f"{_yes_no(model['leak_check_passed'])} | "
            f"{model['docker_no_network_status']} |"
        )
    lines.extend(["", "## Baseline Deltas", ""])
    for row in rows:
        baseline = row["baseline"]
        model = row["model"]
        lines.extend(
            [
                f"### {row['task']}",
                "",
                f"- Baseline artifact: `{row['baseline_path']}`",
                f"- Model artifact: `{row['model_path']}`",
                f"- Coverage delta: {_signed_pct(row['coverage_delta'])}",
                f"- Coverage gap delta: {_signed_int(row['gap_delta'])}",
                f"- Baseline score: {_pct(baseline['final_score'])}; model score: {_pct(model['final_score'])}",
                f"- Model cost estimate: {_money(model['model_cost_usd'])}",
                f"- Behavior categories: `{model['behavior_category_counts']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _latest_report(reports: Path, pattern: re.Pattern[str]) -> dict[str, Any]:
    paths = _iteration_reports(reports, pattern)
    return _read_optional(paths[-1]) if paths else {}


def _iteration_reports(reports: Path, pattern: re.Pattern[str]) -> list[Path]:
    if not reports.is_dir():
        return []
    return sorted(
        [path for path in reports.iterdir() if pattern.match(path.name)],
        key=lambda path: _iteration_index(path, pattern),
    )


def _iteration_index(path: Path, pattern: re.Pattern[str]) -> int:
    match = pattern.match(path.name)
    return int(match.group(1)) if match else -1


def _read_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_data(path)
    except (OSError, ValueError):
        return {}


def _coverage_gap_count(coverage: dict[str, Any]) -> int | None:
    gaps = coverage.get("gaps")
    return len(gaps) if isinstance(gaps, list) else None


def _sum_model_cost(requests: list[dict[str, Any]]) -> float | None:
    total = 0.0
    found = False
    for request in requests:
        metadata = request.get("client_metadata")
        if not isinstance(metadata, dict):
            continue
        value = metadata.get("estimated_cost_usd")
        if isinstance(value, int | float):
            total += float(value)
            found = True
    return total if found else None


def _delta(new: object, old: object) -> float | int | None:
    if isinstance(new, int | float) and isinstance(old, int | float):
        return new - old
    return None


def _fmt(value: object) -> str:
    return "n/a" if value is None else str(value)


def _pct(value: object) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _signed_pct(value: object) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    return f"{float(value) * 100:+.1f} pp"


def _signed_int(value: object) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    return f"{int(value):+d}"


def _money(value: object) -> str:
    if not isinstance(value, int | float):
        return "not reported"
    return f"${float(value):.4f}"


def _yes_no(value: object) -> str:
    if value is True:
        return "passed"
    if value is False:
        return "failed"
    return "n/a"
