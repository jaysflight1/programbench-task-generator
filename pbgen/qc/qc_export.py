"""CSV and Markdown exports for QC queues."""

from __future__ import annotations

import csv
from pathlib import Path

from pbgen.schemas import QCItem, QCQueueReport

QUEUE_GROUPS = (
    ("Weak Assertions", "weak assertion queue"),
    ("Flaky", "flaky test queue"),
    ("Dummy-Passing", "dummy-passing test queue"),
    ("Redundant", "redundant high-assertion queue"),
)


def export_qc_queue(report: QCQueueReport, output_dir: Path) -> tuple[Path, Path]:
    """Export QC queue as CSV and Markdown."""

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "qc_queue.csv"
    md_path = output_dir / "qc_queue.md"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "test_id",
                "queue",
                "severity",
                "reason",
                "file_path",
                "recommendation",
                "iteration",
            ],
        )
        writer.writeheader()
        for item in report.items:
            writer.writerow(
                {
                    "test_id": item.test_id,
                    "queue": item.queue,
                    "severity": item.severity,
                    "reason": item.reason,
                    "file_path": str(item.file_path or ""),
                    "recommendation": item.recommendation or "",
                    "iteration": "" if item.iteration is None else item.iteration,
                }
            )

    counts_by_queue = _counts_by_queue(report.items)
    lines = [
        "# QC Queue",
        "",
        "## Suite Decision",
        "",
        f"**Decision:** {_suite_decision(report.items)}",
        "",
        f"**Total QC items:** {len(report.items)}",
        "",
        "## Queue Counts",
        "",
        "| Queue | Count |",
        "|---|---:|",
    ]
    for title, queue in QUEUE_GROUPS:
        lines.append(f"| {title} | {counts_by_queue.get(queue, 0)} |")
    known_queues = {queue for _, queue in QUEUE_GROUPS}
    other_count = sum(count for queue, count in counts_by_queue.items() if queue not in known_queues)
    if other_count:
        lines.append(f"| Other | {other_count} |")

    grouped = _group_items(report.items)
    for title, queue in QUEUE_GROUPS:
        lines.extend(["", f"## {title}", ""])
        items = grouped.get(queue, [])
        if not items:
            lines.append(f"No {title.lower()} QC items.")
            continue
        lines.extend(_item_table(items))

    other_items = [item for item in report.items if item.queue not in known_queues]
    if other_items:
        lines.extend(["", "## Other", ""])
        lines.extend(_item_table(other_items))

    if not report.items:
        lines.extend(["", "No QC items generated."])

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def _counts_by_queue(items: list[QCItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.queue] = counts.get(item.queue, 0) + 1
    return counts


def _group_items(items: list[QCItem]) -> dict[str, list[QCItem]]:
    grouped: dict[str, list[QCItem]] = {}
    for item in items:
        grouped.setdefault(item.queue, []).append(item)
    return grouped


def _suite_decision(items: list[QCItem]) -> str:
    if not items:
        return "Ready: no QC items generated."
    if any(item.severity == "high" for item in items):
        return "Hold: address high-severity QC items before final suite."
    return "Review: QC items remain, but no high-severity blockers were reported."


def _item_table(items: list[QCItem]) -> list[str]:
    lines = [
        "| Test | Severity | Reason | Recommendation | File |",
        "|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            "| "
            f"{_md_cell(item.test_id)} | "
            f"{_md_cell(item.severity)} | "
            f"{_md_cell(item.reason)} | "
            f"{_md_cell(item.recommendation)} | "
            f"{_md_cell(item.file_path)} |"
        )
    return lines


def _md_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", "<br>").replace("|", "\\|")
