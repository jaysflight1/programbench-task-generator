from pathlib import Path

import pytest

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.reporting.run_summary import (
    build_run_summary,
    write_batch_summary,
    write_run_summary,
)
from pbgen.schemas import RunSummaryReport
from pbgen.serialization import read_data, write_data


def test_build_run_summary_reads_existing_artifacts(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    paths = _write_complete_artifacts(config, task_id="demo")

    summary = build_run_summary("demo", config)

    assert summary.task_id == "demo"
    assert summary.repo_url == "https://example.test/repo.git"
    assert summary.commit_sha == "abc123"
    assert summary.language == "python"
    assert summary.build_system == "script"
    assert summary.generated_tests == 7
    assert summary.gold_pass_rate == pytest.approx(1.0)
    assert summary.dummy_pass_rate == pytest.approx(0.02)
    assert summary.deterministic_pass_rate == pytest.approx(1.0)
    assert summary.line_coverage == pytest.approx(0.91)
    assert summary.redundancy_score == pytest.approx(0.12)
    assert summary.final_score == pytest.approx(0.88)
    assert summary.qc_queue_size == 2
    assert summary.solver_package == paths.packages / "solver"
    assert summary.evaluator_package == paths.packages / "evaluator"
    assert "QC queue contains 2 open item(s)." in summary.limitations
    assert (
        "Economic-importance scoring is intentionally omitted from this implementation."
        in summary.limitations
    )
    assert "Resolve, justify, or explicitly accept QC queue items." in summary.next_steps


def test_write_run_summary_writes_json_and_markdown(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    paths = _write_complete_artifacts(config, task_id="demo")

    summary, markdown_path = write_run_summary("demo", config)

    assert markdown_path == paths.root / "RUN_SUMMARY.md"
    assert (paths.reports / "RUN_SUMMARY.json").exists()
    persisted = read_data(paths.reports / "RUN_SUMMARY.json")
    assert persisted["task_id"] == summary.task_id
    assert persisted["line_coverage"] == pytest.approx(0.91)

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Run Summary: demo" in markdown
    assert "Task-construction artifact summary" in markdown
    assert "## Headline Metrics" in markdown
    assert "Action needed: 2 QC queue item(s) remain open." in markdown


def test_build_run_summary_missing_artifacts_uses_conservative_defaults(tmp_path: Path) -> None:
    summary = build_run_summary("empty", PBGenConfig(workspace_root=tmp_path))

    assert summary.repo_url == "unknown"
    assert summary.commit_sha == "unknown"
    assert summary.generated_tests == 0
    assert summary.gold_pass_rate == pytest.approx(0.0)
    assert summary.dummy_pass_rate == pytest.approx(1.0)
    assert summary.deterministic_pass_rate == pytest.approx(0.0)
    assert summary.line_coverage is None
    assert summary.final_score == pytest.approx(0.0)
    assert summary.qc_queue_size == 0
    assert summary.solver_package is None
    assert summary.evaluator_package is None
    assert "Task spec is missing, so repository metadata is unknown." in summary.limitations
    assert "No generated tests were found." in summary.limitations
    assert "Run repository intake to produce task_spec.yaml." in summary.next_steps


def test_write_batch_summary_writes_json_and_markdown(tmp_path: Path) -> None:
    solver = tmp_path / "solver"
    evaluator = tmp_path / "evaluator"
    solver.mkdir()
    evaluator.mkdir()
    ready = RunSummaryReport(
        task_id="ready",
        repo_url="https://example.test/ready.git",
        commit_sha="abc123",
        language="python",
        build_system="script",
        generated_tests=5,
        gold_pass_rate=1.0,
        dummy_pass_rate=0.0,
        deterministic_pass_rate=1.0,
        line_coverage=0.9,
        redundancy_score=0.1,
        final_score=0.92,
        qc_queue_size=0,
        solver_package=solver,
        evaluator_package=evaluator,
    )
    failed = RunSummaryReport(
        task_id="failed",
        repo_url="unknown",
        commit_sha="unknown",
        generated_tests=0,
        gold_pass_rate=0.0,
        dummy_pass_rate=1.0,
        deterministic_pass_rate=0.0,
        final_score=0.0,
        qc_queue_size=0,
        limitations=["Reward shape report is missing; final score is reported as 0%."],
    )

    report = write_batch_summary("batch-1", [ready, failed], tmp_path / "batch")

    assert report.total_tasks == 2
    assert report.successful_tasks == 1
    assert report.failed_tasks == 1
    persisted = read_data(tmp_path / "batch" / "BATCH_RUN_REPORT.json")
    assert persisted["total_tasks"] == 2
    markdown = (tmp_path / "batch" / "BATCH_RUN_REPORT.md").read_text(encoding="utf-8")
    assert "# Batch Run Summary: batch-1" in markdown
    assert "| ready | 92.0% | 5 | 0 | Solver and evaluator packages present |" in markdown
    assert "| failed | 0.0% | 0 | 0 | Solver and evaluator packages missing |" in markdown


def _write_complete_artifacts(config: PBGenConfig, *, task_id: str) -> ArtifactPaths:
    paths = ArtifactPaths(config, task_id)
    write_data(
        paths.task_spec,
        {
            "task_id": task_id,
            "repo_url": "https://example.test/repo.git",
            "commit_sha": "abc123",
            "language": "python",
            "build_system": "script",
        },
    )
    write_data(
        paths.reports / "suite_quality_report.json",
        {
            "task_id": task_id,
            "num_tests": 7,
            "gold_pass_rate": 1.0,
            "dummy_pass_rate": 0.02,
            "deterministic_pass_rate": 1.0,
            "line_coverage": 0.75,
            "assertion_strength_score": 0.95,
            "high_lint_count": 0,
            "medium_lint_count": 1,
            "redundancy_score": 0.12,
            "qc_queue_size": 5,
        },
    )
    write_data(
        paths.reports / "reward_shape_report.json",
        {
            "task_id": task_id,
            "correctness_gate_passed": True,
            "correctness_score": 1.0,
            "assertion_strength_score": 0.95,
            "coverage_score": 0.91,
            "redundancy_penalty": 0.12,
            "determinism_score": 1.0,
            "dummy_rejection_score": 0.98,
            "efficiency_multiplier": 1.0,
            "final_score": 0.88,
            "notes": [
                "Python coverage was measured and included in the quality score.",
                "Economic-importance scoring is intentionally omitted from this implementation.",
            ],
        },
    )
    write_data(
        paths.reports / "coverage_report_iteration_0.json",
        {"task_id": task_id, "iteration": 0, "line_coverage": 0.75},
    )
    write_data(
        paths.reports / "coverage_report_iteration_3.json",
        {"task_id": task_id, "iteration": 3, "line_coverage": 0.91},
    )
    write_data(
        paths.qc / "qc_queue.json",
        {
            "task_id": task_id,
            "items": [
                {
                    "test_id": "test_duplicate",
                    "queue": "redundant high-assertion queue",
                    "reason": "cluster contains similar tests",
                    "severity": "medium",
                },
                {
                    "test_id": "test_assertion",
                    "queue": "weak assertion queue",
                    "reason": "assertion too broad",
                    "severity": "high",
                },
            ],
        },
    )
    (paths.generated_tests).mkdir(parents=True)
    (paths.generated_tests / "test_behavior.py").write_text("def test_placeholder():\n    pass\n")
    (paths.packages / "solver").mkdir(parents=True)
    (paths.packages / "evaluator").mkdir(parents=True)
    return paths
