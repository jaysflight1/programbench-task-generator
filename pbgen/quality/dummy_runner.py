"""Run generated tests against trivial wrong executables."""

from __future__ import annotations

from pathlib import Path
import stat

from pbgen.eval.submission_runner import run_generated_suite
from pbgen.logging.event_log import EventLogger


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
        rates = [run_generated_suite(task_id, tests_path, dummy).pass_rate for dummy in dummy_paths]
        dummy_pass_rate = max(rates) if rates else 0.0
        EventLogger(event_log_path).append(
            task_id=task_id,
            stage="quality",
            event_type="dummy_check_run",
            iteration=iteration,
            metrics={"dummy_pass_rate": dummy_pass_rate, "dummies": len(dummy_paths)},
        )
        return dummy_pass_rate

    def _write_dummy(self, path: Path, body: str) -> Path:
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path
