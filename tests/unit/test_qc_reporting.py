import csv
from pathlib import Path

from pbgen.qc.qc_export import export_qc_queue
from pbgen.qc.qc_queue import build_qc_queue
from pbgen.schemas import (
    AssertionLintFlag,
    AssertionLintReport,
    LintSeverity,
    QCItem,
    QCQueueReport,
    RedundancyItem,
    RedundancyReport,
)


def test_qc_queue_report_accepts_previous_json_shape() -> None:
    report = QCQueueReport.model_validate(
        {
            "task_id": "demo",
            "items": [
                {
                    "test_id": "test_old",
                    "queue": "weak assertion queue",
                    "reason": "ASSERT001: missing assertion",
                    "severity": "high",
                    "file_path": "tests/test_behavior.py",
                }
            ],
        }
    )

    assert report.summary == {}
    assert report.items[0].recommendation is None
    assert report.items[0].iteration is None


def test_build_qc_queue_populates_recommendations_and_summary() -> None:
    lint_report = AssertionLintReport(
        task_id="demo",
        flags=[
            AssertionLintFlag(
                rule_id="ASSERT001",
                severity=LintSeverity.HIGH,
                message="missing assertion",
                file_path=Path("tests/test_behavior.py"),
                test_name="test_no_assertion",
            )
        ],
    )
    redundancy_report = RedundancyReport(
        task_id="demo",
        redundancy_score=0.5,
        items=[
            RedundancyItem(
                test_id="test_duplicate",
                cluster_id="cluster-a",
                cluster_size=2,
                redundancy_penalty=0.25,
                recommended_action="downweight",
            )
        ],
    )

    report = build_qc_queue(
        "demo",
        lint_report,
        deterministic_pass_rate=0.75,
        dummy_pass_rate=0.25,
        redundancy_report=redundancy_report,
    )

    assert report.summary == {
        "total_items": 4,
        "counts_by_queue": {
            "weak assertion queue": 1,
            "flaky test queue": 1,
            "dummy-passing test queue": 1,
            "redundant high-assertion queue": 1,
        },
    }
    assert {item.recommendation for item in report.items} == {
        "repair or discard before final suite",
        "isolate flaky test and rerun determinism",
        "strengthen behavioral assertions",
        "downweight or keep only if behavior variant is justified",
    }


def test_build_qc_queue_adds_per_test_hard_gate_items() -> None:
    report = build_qc_queue(
        "demo",
        AssertionLintReport(task_id="demo"),
        deterministic_pass_rate=0.5,
        dummy_pass_rate=0.5,
        redundancy_report=RedundancyReport(task_id="demo", items=[], redundancy_score=0.0),
        per_test_deterministic={"test_flaky": False, "test_stable": True},
        per_test_dummy_passes={"test_dummy": True, "test_strong": False},
    )

    by_test = {item.test_id: item for item in report.items}
    assert by_test["test_flaky"].queue == "flaky test queue"
    assert by_test["test_dummy"].queue == "dummy-passing test queue"


def test_build_qc_queue_adds_mutation_lite_items() -> None:
    report = build_qc_queue(
        "demo",
        AssertionLintReport(task_id="demo"),
        deterministic_pass_rate=1.0,
        dummy_pass_rate=0.0,
        redundancy_report=RedundancyReport(task_id="demo", items=[], redundancy_score=0.0),
        mutation_survival_rate=0.5,
        per_test_mutation_survived={"test_weak": True, "test_strong": False},
    )

    by_test = {item.test_id: item for item in report.items}
    assert by_test["suite"].queue == "mutation-surviving test queue"
    assert by_test["test_weak"].queue == "mutation-surviving test queue"


def test_export_qc_queue_groups_counts_and_recommendations(tmp_path: Path) -> None:
    report = QCQueueReport(
        task_id="demo",
        items=[
            QCItem(
                test_id="test_no_assertion",
                queue="weak assertion queue",
                severity="high",
                reason="ASSERT001: missing assertion",
                file_path=Path("tests/test_behavior.py"),
                recommendation="repair or discard before final suite",
                iteration=1,
            ),
            QCItem(
                test_id="suite",
                queue="flaky test queue",
                severity="high",
                reason="deterministic pass rate is 0.750",
                recommendation="isolate flaky test and rerun determinism",
            ),
            QCItem(
                test_id="suite",
                queue="dummy-passing test queue",
                severity="high",
                reason="best dummy pass rate is 0.250",
                recommendation="strengthen behavioral assertions",
            ),
            QCItem(
                test_id="test_duplicate",
                queue="redundant high-assertion queue",
                severity="medium",
                reason="cluster cluster-a contains 2 similar tests",
                recommendation="downweight or keep only if behavior variant is justified",
                iteration=2,
            ),
        ],
    )

    csv_path, md_path = export_qc_queue(report, tmp_path)

    markdown = md_path.read_text(encoding="utf-8")
    for heading in (
        "## Suite Decision",
        "## Queue Counts",
        "## Weak Assertions",
        "## Flaky",
        "## Dummy-Passing",
        "## Redundant",
    ):
        assert heading in markdown
    for count_row in (
        "| Weak Assertions | 1 |",
        "| Flaky | 1 |",
        "| Dummy-Passing | 1 |",
        "| Redundant | 1 |",
    ):
        assert count_row in markdown
    assert "**Decision:** Hold: address high-severity QC items before final suite." in markdown
    assert "repair or discard before final suite" in markdown
    assert "isolate flaky test and rerun determinism" in markdown
    assert "strengthen behavioral assertions" in markdown
    assert "downweight or keep only if behavior variant is justified" in markdown
    assert "No QC items generated." not in markdown
    assert "| Test | Severity | Reason | Recommendation | File |\n|---|---|---|---|---|\n\n##" not in markdown

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == [
        "test_id",
        "queue",
        "severity",
        "reason",
        "file_path",
        "recommendation",
        "iteration",
    ]
    assert rows[1][-2:] == ["repair or discard before final suite", "1"]
    assert rows[4][-2:] == [
        "downweight or keep only if behavior variant is justified",
        "2",
    ]
