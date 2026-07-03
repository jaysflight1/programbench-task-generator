"""Normalize observed executable output into portable assertions."""

from __future__ import annotations

from dataclasses import dataclass
import re

from pbgen.schemas import ExecutableTestCase, ExpectedOutput


_LOCAL_PATH_PATTERNS = [
    re.compile(r"/Users/[^/\s]+/", re.IGNORECASE),
    re.compile(r"/private/tmp/", re.IGNORECASE),
    re.compile(r"/(?:var/folders|tmp)/", re.IGNORECASE),
    re.compile(r"\bartifacts/[A-Za-z0-9_.-]+/", re.IGNORECASE),
]
_TRACEBACK_MARKERS = (
    "Traceback (most recent call last):",
    'File "',
)


@dataclass(frozen=True)
class OutputNormalizationResult:
    """Portable expected output assertions derived from one gold observation."""

    stdout: ExpectedOutput
    stderr: ExpectedOutput
    provenance: dict[str, str]


def normalize_observed_outputs(stdout: str, stderr: str) -> OutputNormalizationResult:
    """Return stable output assertions for observed gold stdout/stderr."""

    stderr_assertion, provenance = _normalize_stderr(stderr)
    return OutputNormalizationResult(
        stdout=ExpectedOutput(exact=stdout),
        stderr=stderr_assertion,
        provenance=provenance,
    )


def apply_observed_outputs(
    case: ExecutableTestCase,
    *,
    stdout: str,
    stderr: str,
    extra_provenance: dict[str, str] | None = None,
) -> ExecutableTestCase:
    """Return a copy of a case with portable observed output assertions."""

    normalized = normalize_observed_outputs(stdout, stderr)
    return case.model_copy(
        update={
            "expected_stdout": normalized.stdout,
            "expected_stderr": normalized.stderr,
            "provenance": {
                **case.provenance,
                **(extra_provenance or {}),
                **normalized.provenance,
            },
        }
    )


def _normalize_stderr(stderr: str) -> tuple[ExpectedOutput, dict[str, str]]:
    if not stderr or not _needs_stderr_normalization(stderr):
        return ExpectedOutput(exact=stderr), {"stderr_normalized": "false"}
    stable_lines = _stable_stderr_lines(stderr)
    if not stable_lines:
        return (
            ExpectedOutput(exact=stderr),
            {
                "stderr_normalized": "skipped",
                "stderr_normalization_reason": "no_stable_portable_lines",
            },
        )
    return (
        ExpectedOutput(contains=stable_lines),
        {
            "stderr_normalized": "true",
            "stderr_normalization_reason": "traceback_or_local_path",
        },
    )


def _needs_stderr_normalization(stderr: str) -> bool:
    return any(pattern.search(stderr) for pattern in _LOCAL_PATH_PATTERNS) or any(
        marker in stderr for marker in _TRACEBACK_MARKERS
    )


def _stable_stderr_lines(stderr: str) -> list[str]:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    stable: list[str] = []
    for line in reversed(lines):
        if _is_unstable_stderr_line(line):
            continue
        stable.append(line)
        if len(stable) >= 3:
            break
    return list(reversed(stable))


def _is_unstable_stderr_line(line: str) -> bool:
    if any(pattern.search(line) for pattern in _LOCAL_PATH_PATTERNS):
        return True
    if line == "Traceback (most recent call last):":
        return True
    if line.startswith('File "') or line.startswith("^") or line.startswith("raise "):
        return True
    return len(line) < 10
