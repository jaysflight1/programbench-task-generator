"""Build human-readable QC queues from quality reports."""

from __future__ import annotations

from pbgen.schemas import AssertionLintReport, QCItem, QCQueueReport, RedundancyReport

RECOMMENDATIONS_BY_QUEUE = {
    "weak assertion queue": "repair or discard before final suite",
    "flaky test queue": "isolate flaky test and rerun determinism",
    "dummy-passing test queue": "strengthen behavioral assertions",
    "redundant high-assertion queue": "downweight or keep only if behavior variant is justified",
}


def build_qc_queue(
    task_id: str,
    lint_report: AssertionLintReport,
    deterministic_pass_rate: float,
    dummy_pass_rate: float,
    redundancy_report: RedundancyReport,
    iteration: int | None = None,
) -> QCQueueReport:
    """Create QC items for weak, flaky, dummy-passing, and redundant tests."""

    items: list[QCItem] = []
    for flag in lint_report.flags:
        items.append(
            QCItem(
                test_id=flag.test_name or "unknown",
                queue="weak assertion queue",
                reason=f"{flag.rule_id}: {flag.message}",
                severity=str(flag.severity),
                file_path=flag.file_path,
                recommendation=RECOMMENDATIONS_BY_QUEUE["weak assertion queue"],
                iteration=iteration,
            )
        )
    if deterministic_pass_rate < 1.0:
        items.append(
            QCItem(
                test_id="suite",
                queue="flaky test queue",
                reason=f"deterministic pass rate is {deterministic_pass_rate:.3f}",
                severity="high",
                recommendation=RECOMMENDATIONS_BY_QUEUE["flaky test queue"],
                iteration=iteration,
            )
        )
    if dummy_pass_rate > 0.0:
        items.append(
            QCItem(
                test_id="suite",
                queue="dummy-passing test queue",
                reason=f"best dummy pass rate is {dummy_pass_rate:.3f}",
                severity="high",
                recommendation=RECOMMENDATIONS_BY_QUEUE["dummy-passing test queue"],
                iteration=iteration,
            )
        )
    for redundant_item in redundancy_report.items:
        if redundant_item.cluster_size > 1:
            items.append(
                QCItem(
                    test_id=redundant_item.test_id,
                    queue="redundant high-assertion queue",
                    reason=(
                        f"cluster {redundant_item.cluster_id} contains "
                        f"{redundant_item.cluster_size} similar tests"
                    ),
                    severity="medium",
                    recommendation=RECOMMENDATIONS_BY_QUEUE["redundant high-assertion queue"],
                    iteration=iteration,
                )
            )
    counts_by_queue: dict[str, int] = {}
    for item in items:
        counts_by_queue[item.queue] = counts_by_queue.get(item.queue, 0) + 1
    return QCQueueReport(
        task_id=task_id,
        items=items,
        summary={"total_items": len(items), "counts_by_queue": counts_by_queue},
    )
