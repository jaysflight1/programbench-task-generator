"""Coverage-guided test generation controller."""

from __future__ import annotations

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.coverage.adapters import coverage_unavailable_report
from pbgen.coverage.registry import run_registered_coverage, write_coverage_artifacts
from pbgen.errors import CoverageError
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.logging.event_log import EventLogger
from pbgen.qc.qc_queue import build_qc_queue
from pbgen.quality.assertion_linter import AssertionQualityLinter
from pbgen.quality.dummy_runner import DummyBinaryRunner
from pbgen.quality.gold_determinism import run_gold_determinism
from pbgen.quality.redundancy import RedundancyAnalyzer
from pbgen.schemas import BehaviorSurface, CoverageGap, TaskSpec
from pbgen.serialization import read_data, write_data
from pbgen.testgen.backends import create_test_generation_backend
from pbgen.testgen.model_backend import ModelClient
from pbgen.testgen.prompt_builder import TestGenerationPrompt
from pbgen.testgen.test_repair import repair_or_discard_bad_tests
from pbgen.testgen.test_writer import TestGenerationBackend


class CoverageGuidedTestController:
    """ProgramBench-style iterative controller with MVP local generation."""

    def __init__(
        self,
        config: PBGenConfig,
        backend: TestGenerationBackend | None = None,
        model_client: ModelClient | None = None,
    ) -> None:
        self.config = config
        self.backend = backend or create_test_generation_backend(config, model_client=model_client)

    def run(self, task_id: str, iterations: int | None = None) -> list[str]:
        """Generate tests and perform a smoke gold run for each iteration."""

        paths = ArtifactPaths(self.config, task_id)
        logger = EventLogger(paths.event_log)
        surface = BehaviorSurface.model_validate(read_data(paths.behavior_surface))
        spec = TaskSpec.model_validate(read_data(paths.task_spec))
        max_iterations = min(
            iterations or self.config.max_generation_iterations,
            self.config.max_generation_iterations,
        )
        generated: list[str] = []
        gaps: list[CoverageGap] = []
        previous_coverage: float | None = None
        for iteration in range(max_iterations):
            prompt = TestGenerationPrompt(
                task_id=task_id,
                behavior_surface=surface,
                coverage_gaps=gaps,
                iteration=iteration,
                executable_path=paths.executable,
                execution_policy=self.config.execution_policy,
                safe_command_allow_patterns=self.config.safe_command_allow_patterns,
                safe_command_deny_patterns=self.config.safe_command_deny_patterns,
                trusted_local_execution=self.config.trusted_local_execution,
            )
            test_paths = self.backend.generate_tests(prompt, paths.generated_tests)
            accepted = repair_or_discard_bad_tests(test_paths)
            generated = [path.as_posix() for path in accepted]
            logger.append(
                task_id=task_id,
                stage="test_generation",
                event_type="test_generated",
                iteration=iteration,
                actor="system",
                prompt_version=getattr(self.backend, "prompt_version", None),
                metrics={"files": len(accepted)},
            )
            result = run_generated_suite(task_id, paths.generated_tests, paths.executable)
            self._run_iteration_quality_gates(task_id, iteration, paths, logger)
            coverage_report = None
            if self.config.coverage_enabled:
                try:
                    coverage_report = run_registered_coverage(
                        spec,
                        paths,
                        self.config,
                        iteration=iteration,
                    )
                except CoverageError as exc:
                    coverage_report = coverage_unavailable_report(
                        task_id,
                        iteration,
                        "coverage-error",
                        str(exc),
                    )
                    write_coverage_artifacts(coverage_report, paths.reports)
                    logger.append(
                        task_id=task_id,
                        stage="coverage",
                        event_type="coverage_measured",
                        iteration=iteration,
                        metrics={"error": str(exc)},
                        qc_flags=["coverage_failed"],
                    )
                else:
                    write_coverage_artifacts(coverage_report, paths.reports)
                    logger.append(
                        task_id=task_id,
                        stage="coverage",
                        event_type="coverage_measured",
                        iteration=iteration,
                        metrics={
                            "line_coverage": coverage_report.line_coverage,
                            "gaps": len(coverage_report.gaps),
                        },
                    )
                    for gap in coverage_report.gaps:
                        logger.append(
                            task_id=task_id,
                            stage="coverage",
                            event_type="coverage_gap_identified",
                            iteration=iteration,
                            metrics=gap.model_dump(mode="json"),
                        )
                    gaps = coverage_report.gaps
            if result.pass_rate < 1.0:
                continue
            if coverage_report is None or coverage_report.line_coverage is None:
                break
            current_coverage = coverage_report.line_coverage
            delta = (
                current_coverage - previous_coverage
                if previous_coverage is not None
                else current_coverage
            )
            previous_coverage = current_coverage
            if current_coverage >= self.config.coverage_target:
                break
            if iteration > 0 and delta < self.config.min_coverage_delta_per_iteration:
                break
        return generated

    def _run_iteration_quality_gates(
        self,
        task_id: str,
        iteration: int,
        paths: ArtifactPaths,
        logger: EventLogger,
    ) -> None:
        lint_report = AssertionQualityLinter(self.config).lint_path(task_id, paths.generated_tests)
        write_data(
            paths.reports / f"lint_report_iteration_{iteration}.json",
            lint_report.model_dump(mode="json"),
        )
        logger.append(
            task_id=task_id,
            stage="quality",
            event_type="test_linted",
            iteration=iteration,
            metrics={"high": lint_report.high_count, "medium": lint_report.medium_count},
        )
        deterministic_rate = run_gold_determinism(
            task_id,
            paths.generated_tests,
            paths.executable,
            paths.event_log,
            self.config,
            iteration=iteration,
        )
        dummy_pass_rate = DummyBinaryRunner().run(
            task_id,
            paths.generated_tests,
            paths.root / "dummies" / f"iteration_{iteration}",
            paths.event_log,
            iteration=iteration,
        )
        redundancy_report = RedundancyAnalyzer().analyze(
            task_id,
            paths.generated_tests,
            paths.reports / f"redundancy_report_iteration_{iteration}.json",
            paths.event_log,
            iteration=iteration,
        )
        qc_report = build_qc_queue(
            task_id,
            lint_report,
            deterministic_rate,
            dummy_pass_rate,
            redundancy_report,
            iteration=iteration,
        )
        write_data(
            paths.qc / f"qc_queue_iteration_{iteration}.json",
            qc_report.model_dump(mode="json"),
        )
        for item in qc_report.items:
            logger.append(
                task_id=task_id,
                stage="quality",
                event_type="qc_flag_created",
                iteration=iteration,
                metrics=item.model_dump(mode="json"),
                qc_flags=[item.queue],
            )
