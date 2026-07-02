"""Leak checks for solver-visible cleanroom packages."""

from __future__ import annotations

from pathlib import Path

from pbgen.logging.event_log import EventLogger
from pbgen.serialization import write_data


SOURCE_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go", ".java"}


def run_leak_check(task_id: str, solver_dir: Path, report_path: Path, event_log_path: Path) -> dict[str, object]:
    """Check solver-visible files for source/tests/log leakage."""

    findings: list[str] = []
    for path in solver_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(solver_dir).as_posix()
        lower = rel.lower()
        if "/test" in lower or lower.startswith("test"):
            findings.append(f"test-like path visible: {rel}")
        if "/.git" in lower or lower.startswith(".git"):
            findings.append(f"git metadata visible: {rel}")
        if path.suffix.lower() in SOURCE_SUFFIXES and "executable/" not in rel:
            findings.append(f"source-like file visible: {rel}")
        if "generation_events" in lower or lower.endswith(".jsonl"):
            findings.append(f"generation log visible: {rel}")
    report = {"task_id": task_id, "passed": not findings, "findings": findings}
    write_data(report_path, report)
    EventLogger(event_log_path).append(
        task_id=task_id,
        stage="cleanroom",
        event_type="leak_check_run",
        metrics={"passed": not findings, "findings": len(findings)},
    )
    return report
