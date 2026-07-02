"""Leak checks for solver-visible cleanroom packages."""

from __future__ import annotations

from pathlib import Path
import re

from pbgen.logging.event_log import EventLogger
from pbgen.serialization import write_data


SOURCE_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go", ".java"}
TEXT_SUFFIXES = {
    ".cfg",
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_PATH_FRAGMENTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "generated_tests",
    "hidden_tests",
    "candidate_runs",
    "test_cases_iteration",
    "leak_check_report",
    "generation_events",
}
FORBIDDEN_CONTENT_PATTERNS = [
    re.compile(r"test_cases_iteration_\d+", re.IGNORECASE),
    re.compile(r"generated_tests", re.IGNORECASE),
    re.compile(r"hidden_tests", re.IGNORECASE),
    re.compile(r"generation_events", re.IGNORECASE),
    re.compile(r"/(?:private/)?(?:tmp|var/folders)/", re.IGNORECASE),
    re.compile(r"/Users/[^/\s]+/", re.IGNORECASE),
]


def run_leak_check(task_id: str, solver_dir: Path, report_path: Path, event_log_path: Path) -> dict[str, object]:
    """Check solver-visible files for source/tests/log leakage."""

    findings: list[str] = []
    scanned_files = 0
    for path in solver_dir.rglob("*"):
        if not path.is_file():
            continue
        scanned_files += 1
        rel = path.relative_to(solver_dir).as_posix()
        lower = rel.lower()
        if lower == "executable/program" or lower.startswith("gold/"):
            findings.append(f"gold/reference executable path visible: {rel}")
        if _path_is_test_like(lower):
            findings.append(f"test-like path visible: {rel}")
        for fragment in sorted(FORBIDDEN_PATH_FRAGMENTS):
            if fragment in lower:
                findings.append(f"forbidden path fragment {fragment!r} visible: {rel}")
        if path.suffix.lower() in SOURCE_SUFFIXES and "executable/" not in rel:
            findings.append(f"source-like file visible: {rel}")
        if lower.endswith(".jsonl"):
            findings.append(f"generation log visible: {rel}")
        if rel != "SOLVER_MANIFEST.json":
            findings.extend(_content_findings(path, rel))
    report = {
        "task_id": task_id,
        "passed": not findings,
        "findings": findings,
        "scanned_files": scanned_files,
    }
    write_data(report_path, report)
    EventLogger(event_log_path).append(
        task_id=task_id,
        stage="cleanroom",
        event_type="leak_check_run",
        metrics={"passed": not findings, "findings": len(findings)},
    )
    return report


def _path_is_test_like(lower: str) -> bool:
    parts = lower.split("/")
    return any(part.startswith("test") or part.endswith("_test") for part in parts)


def _content_findings(path: Path, rel: str) -> list[str]:
    data = path.read_bytes()
    findings: list[str] = []
    if _looks_binary(data):
        text = _binary_text(data)
        if _binary_leak_hint(text):
            findings.append(f"binary leak hint visible: {rel}")
        return findings
    if path.suffix.lower() not in TEXT_SUFFIXES and len(data) > 200_000:
        return findings
    text = data.decode("utf-8", errors="ignore")
    for pattern in FORBIDDEN_CONTENT_PATTERNS:
        if pattern.search(text):
            findings.append(f"forbidden content pattern {pattern.pattern!r} visible: {rel}")
    return findings


def _looks_binary(data: bytes) -> bool:
    return b"\0" in data[:4096]


def _binary_text(data: bytes) -> str:
    chunks = re.findall(rb"[\x20-\x7e]{6,}", data[:1_000_000])
    return "\n".join(chunk.decode("utf-8", errors="ignore") for chunk in chunks)


def _binary_leak_hint(text: str) -> bool:
    lowered = text.lower()
    return any(
        hint in lowered
        for hint in (
            "generated_tests",
            "hidden_tests",
            "test_cases_iteration",
            "generation_events",
        )
    )
