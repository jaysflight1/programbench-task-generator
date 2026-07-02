"""Run generated tests against trivial wrong executables."""

from __future__ import annotations

from pathlib import Path
import stat

from pbgen.eval.submission_runner import run_generated_suite
from pbgen.logging.event_log import EventLogger
from pbgen.schemas import TestRunResult


class DummyBinaryRunner:
    """Create dummy binaries and measure how many generated tests they pass."""

    def run(
        self,
        task_id: str,
        tests_path: Path,
        work_dir: Path,
        event_log_path: Path,
        iteration: int | None = None,
    ) -> float:
        work_dir.mkdir(parents=True, exist_ok=True)
        dummy_paths = [
            self._write_dummy(work_dir / "always_zero", "import sys\nsys.exit(0)\n"),
            self._write_dummy(work_dir / "generic_help", 'import sys\nprint("Usage: program [options]")\nsys.exit(0)\n'),
            self._write_dummy(work_dir / "echo_args", "import sys\nprint(' '.join(sys.argv[1:]))\nsys.exit(0)\n"),
        ]
        results = {
            dummy.name: run_generated_suite(task_id, tests_path, dummy)
            for dummy in dummy_paths
        }
        dummy_pass_rate = _per_test_dummy_pass_rate(results)
        EventLogger(event_log_path).append(
            task_id=task_id,
            stage="quality",
            event_type="dummy_check_run",
            iteration=iteration,
            metrics={
                "dummy_pass_rate": dummy_pass_rate,
                "dummies": len(dummy_paths),
                "dummy_pass_rates": {
                    name: result.pass_rate for name, result in sorted(results.items())
                },
                "per_test_dummy_passes": _per_test_dummy_passes(results),
            },
        )
        return dummy_pass_rate

    def _write_dummy(self, path: Path, body: str) -> Path:
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path


def _per_test_dummy_pass_rate(results: dict[str, TestRunResult]) -> float:
    per_test = _per_test_dummy_passes(results)
    if not per_test:
        rates = [result.pass_rate for result in results.values()]
        return max(rates) if rates else 0.0
    return sum(1 for passed in per_test.values() if passed) / len(per_test)


def _per_test_dummy_passes(results: dict[str, TestRunResult]) -> dict[str, bool]:
    passed_by_test: dict[str, bool] = {}
    for result in results.values():
        for outcome in result.outcomes:
            passed_by_test.setdefault(outcome.test_id, False)
            if outcome.outcome == "passed":
                passed_by_test[outcome.test_id] = True
    return dict(sorted(passed_by_test.items()))
