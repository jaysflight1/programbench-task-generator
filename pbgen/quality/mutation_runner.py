"""Mutation-lite rejection probes for generated executable tests."""

from __future__ import annotations

from pathlib import Path
import stat

from pbgen.eval.submission_runner import run_generated_suite
from pbgen.logging.event_log import EventLogger
from pbgen.schemas import MutationLiteReport, TestRunResult
from pbgen.serialization import write_data


class MutationLiteRunner:
    """Run generated tests against synthetic wrong executables."""

    def run(
        self,
        task_id: str,
        tests_path: Path,
        work_dir: Path,
        report_path: Path,
        event_log_path: Path,
        iteration: int | None = None,
    ) -> MutationLiteReport:
        """Write mutation-lite probes and return the rejection report."""

        work_dir.mkdir(parents=True, exist_ok=True)
        mutants = [
            self._write_mutant(work_dir / "changed_exit_code", "import sys\nsys.exit(7)\n"),
            self._write_mutant(
                work_dir / "altered_stdout",
                'import sys\nprint("MUTATED STDOUT")\nsys.exit(0)\n',
            ),
            self._write_mutant(
                work_dir / "stderr_only",
                'import sys\nprint("MUTATED STDERR", file=sys.stderr)\nsys.exit(0)\n',
            ),
            self._write_mutant(
                work_dir / "generic_help",
                'import sys\nprint("Usage: program [options]")\nsys.exit(0)\n',
            ),
            self._write_mutant(
                work_dir / "echo_args",
                "import sys\nprint(' '.join(sys.argv[1:]))\nsys.exit(0)\n",
            ),
        ]
        results = {
            mutant.name: run_generated_suite(task_id, tests_path, mutant)
            for mutant in mutants
        }
        per_test = _per_test_mutation_survived(results)
        survival_rate = _mutation_survival_rate(results, per_test)
        report = MutationLiteReport(
            task_id=task_id,
            mutation_count=len(mutants),
            mutation_survival_rate=survival_rate,
            mutation_pass_rates={
                name: result.pass_rate for name, result in sorted(results.items())
            },
            per_test_mutation_survived=per_test,
        )
        write_data(report_path, report.model_dump(mode="json"))
        EventLogger(event_log_path).append(
            task_id=task_id,
            stage="quality",
            event_type="mutation_lite_check_run",
            iteration=iteration,
            metrics=report.model_dump(mode="json"),
            qc_flags=["mutation_survivors"] if survival_rate > 0.0 else [],
        )
        return report

    def _write_mutant(self, path: Path, body: str) -> Path:
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path


def _mutation_survival_rate(
    results: dict[str, TestRunResult],
    per_test: dict[str, bool],
) -> float:
    if per_test:
        return sum(1 for survived in per_test.values() if survived) / len(per_test)
    rates = [result.pass_rate for result in results.values()]
    return max(rates) if rates else 0.0


def _per_test_mutation_survived(results: dict[str, TestRunResult]) -> dict[str, bool]:
    survived_by_test: dict[str, bool] = {}
    for result in results.values():
        for outcome in result.outcomes:
            survived_by_test.setdefault(outcome.test_id, False)
            if outcome.outcome == "passed":
                survived_by_test[outcome.test_id] = True
    return dict(sorted(survived_by_test.items()))
