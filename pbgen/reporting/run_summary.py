"""Artifact-only run summary reporting."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.schemas import (
    BatchRunReport,
    CoverageReport,
    PBModel,
    QCQueueReport,
    RewardShapeReport,
    RunSummaryReport,
    SuiteQualityReport,
    TaskSpec,
)
from pbgen.serialization import read_data, write_data


_COVERAGE_REPORT_PATTERN = re.compile(r"coverage_report_iteration_(\d+)\.json$")
_QC_QUEUE_PATTERN = re.compile(r"qc_queue_iteration_(\d+)\.json$")

_ModelT = TypeVar("_ModelT", bound=PBModel)


def build_run_summary(task_id: str, config: PBGenConfig) -> RunSummaryReport:
    """Build a code-readiness summary from existing artifacts only."""

    paths = ArtifactPaths(config, task_id)
    limitations: list[str] = []
    next_steps: list[str] = []

    spec = _load_model(paths.task_spec, TaskSpec, limitations, "task spec")
    suite_report = _load_model(
        paths.reports / "suite_quality_report.json",
        SuiteQualityReport,
        limitations,
        "suite quality report",
    )
    reward_report = _load_model(
        paths.reports / "reward_shape_report.json",
        RewardShapeReport,
        limitations,
        "reward shape report",
    )
    coverage_report = _load_latest_iteration_report(
        paths.reports,
        _COVERAGE_REPORT_PATTERN,
        CoverageReport,
        limitations,
        "coverage report",
    )
    qc_report = _load_qc_report(paths, limitations)

    repo_url = spec.repo_url if spec else "unknown"
    commit_sha = spec.commit_sha if spec else "unknown"
    language = spec.language if spec else None
    build_system = spec.build_system if spec else None

    generated_tests = _generated_tests_count(paths)
    if suite_report:
        generated_tests = suite_report.num_tests

    gold_pass_rate = suite_report.gold_pass_rate if suite_report else 0.0
    dummy_pass_rate = suite_report.dummy_pass_rate if suite_report else 1.0
    deterministic_pass_rate = suite_report.deterministic_pass_rate if suite_report else 0.0
    line_coverage = _latest_line_coverage(suite_report, coverage_report)
    redundancy_score = suite_report.redundancy_score if suite_report else None
    final_score = reward_report.final_score if reward_report else 0.0

    qc_queue_size = _qc_queue_size(qc_report, suite_report)
    solver_package = _existing_package_path(paths.packages / "solver")
    evaluator_package = _existing_package_path(paths.packages / "evaluator")

    _add_artifact_limitations(
        spec=spec,
        suite_report=suite_report,
        reward_report=reward_report,
        coverage_report=coverage_report,
        qc_report=qc_report,
        solver_package=solver_package,
        evaluator_package=evaluator_package,
        limitations=limitations,
        next_steps=next_steps,
    )
    _add_metric_limitations_and_steps(
        generated_tests=generated_tests,
        gold_pass_rate=gold_pass_rate,
        dummy_pass_rate=dummy_pass_rate,
        deterministic_pass_rate=deterministic_pass_rate,
        qc_queue_size=qc_queue_size,
        line_coverage=line_coverage,
        config=config,
        limitations=limitations,
        next_steps=next_steps,
    )
    if reward_report and reward_report.notes:
        limitations.extend(_reward_notes_as_limitations(reward_report.notes))
    if not next_steps:
        next_steps.append("Proceed to code-readiness review and preserve the current artifacts.")

    return RunSummaryReport(
        task_id=task_id,
        repo_url=repo_url,
        commit_sha=commit_sha,
        language=language,
        build_system=build_system,
        generated_tests=generated_tests,
        gold_pass_rate=gold_pass_rate,
        dummy_pass_rate=dummy_pass_rate,
        deterministic_pass_rate=deterministic_pass_rate,
        line_coverage=line_coverage,
        redundancy_score=redundancy_score,
        final_score=final_score,
        qc_queue_size=qc_queue_size,
        solver_package=solver_package,
        evaluator_package=evaluator_package,
        limitations=_dedupe(limitations),
        next_steps=_dedupe(next_steps),
    )


def write_run_summary(task_id: str, config: PBGenConfig) -> tuple[RunSummaryReport, Path]:
    """Write JSON under reports/ and CEO-readable Markdown at the artifact root."""

    paths = ArtifactPaths(config, task_id)
    summary = build_run_summary(task_id, config)
    json_path = paths.reports / "RUN_SUMMARY.json"
    markdown_path = paths.root / "RUN_SUMMARY.md"
    write_data(json_path, summary.model_dump(mode="json"))
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_run_summary_markdown(summary), encoding="utf-8")
    return summary, markdown_path


def write_batch_summary(
    batch_id: str,
    summaries: list[RunSummaryReport],
    output_path: Path,
) -> BatchRunReport:
    """Write batch JSON and Markdown summaries for collected task summaries."""

    json_path, markdown_path = _batch_output_paths(output_path)
    successful_tasks = sum(1 for summary in summaries if _summary_successful(summary))
    report = BatchRunReport(
        batch_id=batch_id,
        tasks=summaries,
        total_tasks=len(summaries),
        successful_tasks=successful_tasks,
        failed_tasks=len(summaries) - successful_tasks,
        notes=[
            "Batch summary is artifact-only; no repositories were executed and no models were invoked.",
            "Successful tasks have a nonzero final score and complete solver/evaluator packages.",
        ],
    )
    write_data(json_path, report.model_dump(mode="json"))
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_batch_summary_markdown(report), encoding="utf-8")
    return report


def render_run_summary_markdown(summary: RunSummaryReport) -> str:
    """Render a concise, CEO-readable Markdown summary."""

    qc_status = (
        "Clear: no QC queue items are currently open."
        if summary.qc_queue_size == 0
        else f"Action needed: {summary.qc_queue_size} QC queue item(s) remain open."
    )
    package_status = _package_status(summary)
    lines = [
        f"# Run Summary: {summary.task_id}",
        "",
        "## What Ran",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Scope | Task-construction artifact summary for this generated benchmark run. |",
        f"| Repository | {_md_value(summary.repo_url)} |",
        f"| Commit | {_md_value(summary.commit_sha)} |",
        f"| Language | {_md_value(summary.language)} |",
        f"| Build system | {_md_value(summary.build_system)} |",
        f"| Packages | {package_status} |",
        "",
        "## Headline Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Generated tests | {summary.generated_tests} |",
        f"| Final score | {_format_percent(summary.final_score)} |",
        f"| Gold pass rate | {_format_percent(summary.gold_pass_rate)} |",
        f"| Dummy pass rate | {_format_percent(summary.dummy_pass_rate)} |",
        f"| Deterministic pass rate | {_format_percent(summary.deterministic_pass_rate)} |",
        f"| Line coverage | {_format_optional_percent(summary.line_coverage)} |",
        f"| Redundancy score | {_format_optional_percent(summary.redundancy_score)} |",
        "",
        "## QC Status",
        "",
        qc_status,
        "",
        "## Limitations",
        "",
        *_bullet_lines(summary.limitations),
        "",
        "## Next Steps",
        "",
        *_bullet_lines(summary.next_steps),
        "",
    ]
    return "\n".join(lines)


def render_batch_summary_markdown(report: BatchRunReport) -> str:
    """Render a concise Markdown summary for multiple task runs."""

    lines = [
        f"# Batch Run Summary: {report.batch_id}",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total tasks | {report.total_tasks} |",
        f"| Successful tasks | {report.successful_tasks} |",
        f"| Failed tasks | {report.failed_tasks} |",
        "",
        "## Tasks",
        "",
        "| Task | Final score | Tests | QC items | Packages |",
        "|---|---:|---:|---:|---|",
    ]
    for summary in report.tasks:
        lines.append(
            "| "
            f"{summary.task_id} | "
            f"{_format_percent(summary.final_score)} | "
            f"{summary.generated_tests} | "
            f"{summary.qc_queue_size} | "
            f"{_package_status(summary)} |"
        )
    lines.extend(["", "## Notes", "", *_bullet_lines(report.notes), ""])
    return "\n".join(lines)


def _load_model(
    path: Path,
    model_type: type[_ModelT],
    limitations: list[str],
    label: str,
) -> _ModelT | None:
    if not path.exists():
        return None
    try:
        return model_type.model_validate(read_data(path))
    except (OSError, ValueError, ValidationError) as exc:
        limitations.append(f"Could not read {label} at {path}: {exc}")
        return None


def _load_latest_iteration_report(
    directory: Path,
    pattern: re.Pattern[str],
    model_type: type[_ModelT],
    limitations: list[str],
    label: str,
) -> _ModelT | None:
    candidates = _sorted_iteration_files(directory, pattern)
    for path in reversed(candidates):
        report = _load_model(path, model_type, limitations, label)
        if report is not None:
            return report
    return None


def _load_qc_report(paths: ArtifactPaths, limitations: list[str]) -> QCQueueReport | None:
    final_qc = _load_model(paths.qc / "qc_queue.json", QCQueueReport, limitations, "QC queue")
    if final_qc is not None:
        return final_qc
    return _load_latest_iteration_report(
        paths.qc,
        _QC_QUEUE_PATTERN,
        QCQueueReport,
        limitations,
        "QC queue",
    )


def _sorted_iteration_files(directory: Path, pattern: re.Pattern[str]) -> list[Path]:
    if not directory.exists():
        return []
    matches: list[tuple[int, Path]] = []
    for path in directory.glob("*.json"):
        match = pattern.search(path.name)
        if match:
            matches.append((int(match.group(1)), path))
    return [path for _, path in sorted(matches, key=lambda item: item[0])]


def _latest_line_coverage(
    suite_report: SuiteQualityReport | None,
    coverage_report: CoverageReport | None,
) -> float | None:
    if coverage_report is not None:
        return coverage_report.line_coverage
    if suite_report is not None:
        return suite_report.line_coverage
    return None


def _qc_queue_size(
    qc_report: QCQueueReport | None,
    suite_report: SuiteQualityReport | None,
) -> int:
    if qc_report is not None:
        return len(qc_report.items)
    if suite_report is not None:
        return suite_report.qc_queue_size
    return 0


def _generated_tests_count(paths: ArtifactPaths) -> int:
    if not paths.generated_tests.exists():
        return 0
    return sum(
        1
        for path in paths.generated_tests.rglob("*.py")
        if "__pycache__" not in path.parts and path.is_file()
    )


def _existing_package_path(path: Path) -> Path | None:
    return path if path.is_dir() else None


def _add_artifact_limitations(
    *,
    spec: TaskSpec | None,
    suite_report: SuiteQualityReport | None,
    reward_report: RewardShapeReport | None,
    coverage_report: CoverageReport | None,
    qc_report: QCQueueReport | None,
    solver_package: Path | None,
    evaluator_package: Path | None,
    limitations: list[str],
    next_steps: list[str],
) -> None:
    if spec is None:
        limitations.append("Task spec is missing, so repository metadata is unknown.")
        next_steps.append("Run repository intake to produce task_spec.yaml.")
    if suite_report is None:
        limitations.append("Suite quality report is missing; readiness metrics are conservative defaults.")
        next_steps.append("Run suite scoring to produce reports/suite_quality_report.json.")
    if reward_report is None:
        limitations.append("Reward shape report is missing; final score is reported as 0%.")
        next_steps.append("Run reward shaping to produce reports/reward_shape_report.json.")
    if coverage_report is None:
        limitations.append("Coverage report is missing or unavailable.")
        next_steps.append("Capture coverage or document why coverage is unavailable.")
    if qc_report is None:
        limitations.append("QC queue is missing, so open review items may be undercounted.")
        next_steps.append("Generate or export the QC queue before handoff.")
    if solver_package is None or evaluator_package is None:
        limitations.append("Cleanroom solver/evaluator packages are incomplete.")
        next_steps.append("Package cleanroom solver and evaluator artifacts.")


def _add_metric_limitations_and_steps(
    *,
    generated_tests: int,
    gold_pass_rate: float,
    dummy_pass_rate: float,
    deterministic_pass_rate: float,
    qc_queue_size: int,
    line_coverage: float | None,
    config: PBGenConfig,
    limitations: list[str],
    next_steps: list[str],
) -> None:
    if generated_tests == 0:
        limitations.append("No generated tests were found.")
        next_steps.append("Generate behavioral tests before declaring the task code-ready.")
    if gold_pass_rate < 1.0:
        limitations.append("Generated tests do not all pass against the gold executable.")
        next_steps.append("Repair or discard tests until gold pass rate is 100%.")
    if dummy_pass_rate > config.dummy_max_pass_rate:
        limitations.append("Dummy pass rate is above the configured threshold.")
        next_steps.append("Strengthen assertions so dummy implementations fail.")
    if deterministic_pass_rate < 1.0:
        limitations.append("Determinism gate is not fully passing.")
        next_steps.append("Investigate flaky tests and rerun determinism checks.")
    if qc_queue_size > 0:
        limitations.append(f"QC queue contains {qc_queue_size} open item(s).")
        next_steps.append("Resolve, justify, or explicitly accept QC queue items.")
    if line_coverage is not None and line_coverage < config.coverage_target:
        limitations.append("Line coverage is below the configured target.")
        next_steps.append("Add focused behavioral tests for remaining coverage gaps.")


def _reward_notes_as_limitations(notes: list[str]) -> list[str]:
    return [
        note
        for note in notes
        if "omitted" in note.lower()
        or "unavailable" in note.lower()
        or "capped" in note.lower()
    ]


def _batch_output_paths(output_path: Path) -> tuple[Path, Path]:
    if output_path.suffix:
        if output_path.suffix == ".json":
            json_path = output_path
            markdown_path = output_path.with_suffix(".md")
        elif output_path.suffix == ".md":
            markdown_path = output_path
            json_path = output_path.with_suffix(".json")
        else:
            json_path = output_path.with_suffix(".json")
            markdown_path = output_path.with_suffix(".md")
        return json_path, markdown_path
    return output_path / "BATCH_RUN_REPORT.json", output_path / "BATCH_RUN_REPORT.md"


def _summary_successful(summary: RunSummaryReport) -> bool:
    return (
        summary.final_score > 0.0
        and summary.solver_package is not None
        and summary.evaluator_package is not None
    )


def _package_status(summary: RunSummaryReport) -> str:
    if summary.solver_package is not None and summary.evaluator_package is not None:
        return "Solver and evaluator packages present"
    if summary.solver_package is not None:
        return "Solver package present; evaluator package missing"
    if summary.evaluator_package is not None:
        return "Evaluator package present; solver package missing"
    return "Solver and evaluator packages missing"


def _format_percent(value: float) -> str:
    return f"{value:.1%}"


def _format_optional_percent(value: float | None) -> str:
    return "Unavailable" if value is None else _format_percent(value)


def _md_value(value: object | None) -> str:
    if value is None:
        return "Unknown"
    text = str(value)
    return text if text else "Unknown"


def _bullet_lines(values: list[str]) -> list[str]:
    if not values:
        return ["- None recorded."]
    return [f"- {value}" for value in values]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped
