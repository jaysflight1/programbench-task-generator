"""Command-line interface for the ProgramBench generator."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

from pbgen.build.build_agent import build_gold
from pbgen.candidate_evaluator import evaluate_executable_candidate, evaluate_source_submission
from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.efficiency.efficiency_score import score_efficiency
from pbgen.errors import PBGenError
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.quality.assertion_linter import lint_and_log
from pbgen.quality.dummy_runner import DummyBinaryRunner
from pbgen.quality.gold_determinism import run_gold_determinism_details
from pbgen.quality.hard_gates import apply_hard_quality_gates
from pbgen.quality.mutation_runner import MutationLiteRunner
from pbgen.quality.redundancy import RedundancyAnalyzer
from pbgen.quality.suite_scorer import score_suite
from pbgen.pipeline import run_batch_manifest, run_task_profile
from pbgen.qc.qc_export import export_qc_queue
from pbgen.released_package import release_task_package
from pbgen.repo_discovery.checkout import init_task
from pbgen.reporting.model_run_report import write_model_run_report
from pbgen.reporting.programbench_performance import write_programbench_performance_report
from pbgen.reporting.run_summary import write_run_summary
from pbgen.schemas import (
    CandidateSubmission,
    CoverageReport,
    QCQueueReport,
    RewardShapeReport,
    SuiteQualityReport,
    TaskProfile,
)
from pbgen.serialization import read_data
from pbgen.solver.openai_solver import (
    DEFAULT_RESPONSES_ENDPOINT,
    DEFAULT_SOLVER_MODEL,
    OpenAISolverConfig,
    solve_with_openai,
)
from pbgen.submission_export import create_submission_archive
from pbgen.task_constructor import construct_task_profile
from pbgen.task_profile import load_task_profile, resolve_profile_paths
from pbgen.testgen.behavioral_surface import discover_behavior_surface
from pbgen.testgen.controller import CoverageGuidedTestController


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    config = PBGenConfig(workspace_root=Path.cwd())
    try:
        if args.command == "init-task":
            spec = init_task(
                task_id=args.task_id,
                config=config,
                local_path=Path(args.local_path) if args.local_path else None,
                repo_url=args.repo_url,
                commit_sha=args.commit,
                primary_binary=args.primary_binary,
            )
            print(f"Initialized task {spec.task_id}")
        elif args.command == "build-gold":
            artifact = build_gold(args.task_id, config, build_system=args.build_system)
            print(f"Built gold executable: {artifact.executable_path}")
        elif args.command == "discover-surface":
            surface = discover_behavior_surface(args.task_id, config)
            print(f"Discovered {len(surface.commands)} behavior commands")
        elif args.command == "generate-tests":
            if args.coverage_target is not None:
                config.coverage_target = args.coverage_target
            if args.min_coverage_delta is not None:
                config.min_coverage_delta_per_iteration = args.min_coverage_delta
            _apply_generation_args(config, args)
            generated = CoverageGuidedTestController(config).run(args.task_id, args.iterations)
            print(f"Generated tests: {', '.join(generated)}")
        elif args.command == "evaluate-suite":
            suite, reward, _qc = evaluate_suite(args.task_id, config)
            print(f"Gold pass rate: {suite.gold_pass_rate:.3f}; final score: {reward.final_score:.3f}")
        elif args.command == "package-cleanroom":
            manifest = release_task_package(args.task_id, config)
            print(f"Packaged solver output: {manifest.solver_package}")
        elif args.command == "export-qc":
            paths = ArtifactPaths(config, args.task_id)
            qc_report = QCQueueReport.model_validate(read_data(paths.qc / "qc_queue.json"))
            csv_path, md_path = export_qc_queue(qc_report, paths.qc)
            print(f"Exported QC queue: {csv_path} and {md_path}")
        elif args.command == "benchmark-solution":
            benchmark_result = evaluate_executable_candidate(
                args.task_id,
                config,
                Path(args.submission),
            )
            print(f"Submission pass rate: {benchmark_result.pass_rate:.3f}")
        elif args.command == "write-summary":
            _summary, markdown_path = write_run_summary(args.task_id, config)
            print(f"Wrote run summary: {markdown_path}")
        elif args.command == "write-model-run-report":
            pairs = [(Path(baseline), Path(model)) for baseline, model in args.artifact_pair]
            output = write_model_run_report(pairs, Path(args.output))
            print(f"Wrote model run report: {output}")
        elif args.command == "write-performance-report":
            _report, json_path, markdown_path = write_programbench_performance_report(
                [Path(path) for path in args.candidate_report],
                Path(args.output),
                model_name=args.model_name,
            )
            print(f"Wrote ProgramBench performance report: {json_path} and {markdown_path}")
        elif args.command == "solve-with-openai":
            solver_report = solve_with_openai(
                OpenAISolverConfig(
                    solver_package=Path(args.solver_package),
                    output_dir=Path(args.output_dir),
                    model_name=args.model_name or _default_solver_model_name(),
                    attempt_id=args.attempt_id,
                    max_rounds=args.max_rounds,
                    reasoning_effort=args.reasoning_effort
                    or os.environ.get("OPENAI_SOLVER_REASONING_EFFORT", "xhigh"),
                    endpoint=args.endpoint or os.environ.get(
                        "OPENAI_SOLVER_ENDPOINT",
                        DEFAULT_RESPONSES_ENDPOINT,
                    ),
                    timeout_seconds=args.timeout_seconds,
                    max_output_tokens=args.max_output_tokens,
                    input_cost_per_1m=args.input_cost_per_1m,
                    output_cost_per_1m=args.output_cost_per_1m,
                )
            )
            print(
                f"Wrote OpenAI solver candidate: {solver_report.candidate_source} "
                f"(status: {solver_report.status}; calls: {solver_report.api_calls})"
            )
        elif args.command == "construct-task":
            profile_path = Path(args.profile)
            profile = resolve_profile_paths(load_task_profile(profile_path), profile_path.parent)
            profile = _profile_with_generation_args(profile, args)
            summary = construct_task_profile(
                profile,
                config,
                task_id_override=args.task_id,
                iterations_override=args.iterations,
                build_system=args.build_system,
            )
            print(f"Constructed task {summary.task_id}; final score: {summary.final_score:.3f}")
        elif args.command == "release-package":
            manifest = release_task_package(args.task_id, config)
            print(f"Released task package: {manifest.evaluator_package}")
        elif args.command == "evaluate-submission":
            if args.execution_policy:
                config.execution_policy = args.execution_policy
            if args.docker_image:
                config.docker_image = args.docker_image
            if args.trusted_local:
                config.trusted_local_execution = True
                config.execution_policy = "trusted-local"
            evaluation_report = evaluate_source_submission(
                CandidateSubmission(
                    package_path=Path(args.package),
                    submission_source=Path(args.submission_source),
                    build_script=Path(args.build_script),
                    output_dir=Path(args.output_dir) if args.output_dir else None,
                    model_name=args.model_name,
                    attempt_id=args.attempt_id,
                    api_calls=args.api_calls,
                    cost_usd=args.cost_usd,
                    cheating_flagged=args.cheating_flagged,
                    disqualification_reason=args.disqualification_reason,
                ),
                config,
            )
            metrics = evaluation_report.programbench_metrics
            suffix = (
                f"; ProgramBench resolved: {metrics.resolved}; almost: {metrics.almost_resolved}"
                if metrics
                else ""
            )
            print(f"Submission pass rate: {evaluation_report.pass_rate:.3f}{suffix}")
        elif args.command == "run-task":
            profile_path = Path(args.profile)
            profile = resolve_profile_paths(load_task_profile(profile_path), profile_path.parent)
            profile = _profile_with_generation_args(profile, args)
            summary = run_task_profile(
                profile,
                config,
                task_id_override=args.task_id,
                iterations_override=args.iterations,
                build_system=args.build_system,
            )
            print(f"Ran task {summary.task_id}; final score: {summary.final_score:.3f}")
        elif args.command == "batch-run":
            batch_report = run_batch_manifest(
                Path(args.manifest),
                config,
                output_path=Path(args.output) if args.output else None,
            )
            print(
                f"Ran batch {batch_report.batch_id}: "
                f"{batch_report.successful_tasks}/{batch_report.total_tasks} successful"
            )
        elif args.command == "export-submission":
            result = create_submission_archive(
                Path(args.project_root),
                Path(args.output) if args.output else None,
            )
            print(
                f"Wrote clean submission archive: {result.archive_path} "
                f"({result.included_count} files, "
                f"{result.excluded_known_clutter_count} clutter paths excluded)"
            )
        else:
            parser.print_help()
            return 1
    except PBGenError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


def evaluate_suite(
    task_id: str,
    config: PBGenConfig,
    *,
    benchmark_commands: Sequence[Sequence[str]] | None = None,
) -> tuple[SuiteQualityReport, RewardShapeReport, QCQueueReport]:
    """Run gold evaluation, quality gates, redundancy, efficiency, and scoring."""

    paths = ArtifactPaths(config, task_id)
    gold_result = run_generated_suite(task_id, paths.generated_tests, paths.executable)
    lint_report = lint_and_log(task_id, paths.generated_tests, paths.event_log, config)
    apply_hard_quality_gates(
        task_id=task_id,
        tests_path=paths.generated_tests,
        executable_path=paths.executable,
        lint_report=lint_report,
        dummy_work_dir=paths.root / "dummies",
        report_path=paths.reports / "hard_gate_report.json",
        event_log_path=paths.event_log,
        config=config,
    )
    gold_result = run_generated_suite(task_id, paths.generated_tests, paths.executable)
    lint_report = lint_and_log(task_id, paths.generated_tests, paths.event_log, config)
    determinism_report = run_gold_determinism_details(
        task_id,
        paths.generated_tests,
        paths.executable,
        paths.event_log,
        config,
    )
    dummy_report = DummyBinaryRunner().run_details(
        task_id,
        paths.generated_tests,
        paths.root / "dummies" / "accepted",
        paths.event_log,
    )
    deterministic_rate = determinism_report.deterministic_pass_rate
    dummy_pass_rate = dummy_report.dummy_pass_rate
    redundancy_report = RedundancyAnalyzer().analyze(
        task_id,
        paths.generated_tests,
        paths.reports / "redundancy_report.json",
        paths.event_log,
    )
    mutation_report = MutationLiteRunner().run(
        task_id,
        paths.generated_tests,
        paths.root / "mutations",
        paths.reports / "mutation_lite_report.json",
        paths.event_log,
    )
    efficiency_result = score_efficiency(
        task_id,
        paths.executable,
        paths.executable,
        gold_result.pass_rate,
        paths.reports / "efficiency_manifest.json",
        config,
        benchmark_commands=benchmark_commands,
        accepted_test_cases_path=paths.generated_tests,
    )
    return score_suite(
        task_id=task_id,
        gold_result=gold_result,
        lint_report=lint_report,
        deterministic_pass_rate=deterministic_rate,
        dummy_pass_rate=dummy_pass_rate,
        redundancy_report=redundancy_report,
        efficiency_result=efficiency_result,
        mutation_report=mutation_report,
        coverage_report=_latest_coverage_report(paths.reports),
        reports_dir=paths.reports,
        qc_dir=paths.qc,
        event_log_path=paths.event_log,
        config=config,
    )


def _latest_coverage_report(reports_dir: Path) -> CoverageReport | None:
    reports = sorted(
        reports_dir.glob("coverage_report_iteration_*.json"),
        key=lambda path: _coverage_iteration(path.name),
    )
    if not reports:
        return None
    return CoverageReport.model_validate(read_data(reports[-1]))


def _coverage_iteration(filename: str) -> int:
    match = re.search(r"coverage_report_iteration_(\d+)\.json", filename)
    return int(match.group(1)) if match else -1


def _apply_generation_args(config: PBGenConfig, args: argparse.Namespace) -> None:
    generation_backend = getattr(args, "generation_backend", None)
    if generation_backend is not None:
        config.generation_backend = generation_backend
    model_command = getattr(args, "model_command", None)
    if model_command:
        config.model_command = shlex.split(model_command)
    model_name = getattr(args, "model_name", None)
    if model_name:
        config.model_name = model_name
    model_temperature = getattr(args, "model_temperature", None)
    if model_temperature is not None:
        config.model_temperature = model_temperature
    model_timeout_seconds = getattr(args, "model_timeout_seconds", None)
    if model_timeout_seconds is not None:
        config.model_timeout_seconds = model_timeout_seconds
    model_max_output_chars = getattr(args, "model_max_output_chars", None)
    if model_max_output_chars is not None:
        config.model_max_output_chars = model_max_output_chars
    if getattr(args, "model_require_structured_cases", False):
        config.model_require_structured_cases = True


def _profile_with_generation_args(profile: TaskProfile, args: argparse.Namespace) -> TaskProfile:
    updates: dict[str, object] = {}
    generation_backend = getattr(args, "generation_backend", None)
    if generation_backend is not None:
        updates["generation_backend"] = generation_backend
    model_command = getattr(args, "model_command", None)
    if model_command:
        updates["model_command"] = shlex.split(model_command)
    model_name = getattr(args, "model_name", None)
    if model_name:
        updates["model_name"] = model_name
    model_temperature = getattr(args, "model_temperature", None)
    if model_temperature is not None:
        updates["model_temperature"] = model_temperature
    model_timeout_seconds = getattr(args, "model_timeout_seconds", None)
    if model_timeout_seconds is not None:
        updates["model_timeout_seconds"] = model_timeout_seconds
    model_max_output_chars = getattr(args, "model_max_output_chars", None)
    if model_max_output_chars is not None:
        updates["model_max_output_chars"] = model_max_output_chars
    if getattr(args, "model_require_structured_cases", False):
        updates["model_require_structured_cases"] = True
    return profile.model_copy(update=updates) if updates else profile


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pbgen", description="ProgramBench-style task generator")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init-task", help="Initialize a task from a local path or Git repo")
    init.add_argument("--local-path", help="Local source repository path")
    init.add_argument("--repo-url", help="Git repository URL")
    init.add_argument("--commit", help="Commit SHA for Git repository")
    init.add_argument("--task-id", required=True)
    init.add_argument("--primary-binary", help="Preferred executable name/path when multiple exist")

    build = sub.add_parser("build-gold", help="Build the gold reference executable")
    build.add_argument("--task-id", required=True)
    build.add_argument(
        "--build-system",
        default="auto",
        choices=["auto", "script", "python-package", "make", "c-single", "cargo", "go", "cmake", "maven", "gradle"],
    )

    for name, help_text in [
        ("discover-surface", "Discover public executable behavior"),
        ("evaluate-suite", "Evaluate generated tests and quality gates"),
        ("package-cleanroom", "Create separated solver/evaluator packages"),
        ("export-qc", "Export QC queues as CSV and Markdown"),
        ("write-summary", "Write a CEO-readable run summary from existing artifacts"),
    ]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--task-id", required=True)

    model_report = sub.add_parser(
        "write-model-run-report",
        help="Compare local baseline artifacts against raw model-backed task artifacts",
    )
    model_report.add_argument(
        "--artifact-pair",
        action="append",
        nargs=2,
        metavar=("BASELINE_ARTIFACT", "MODEL_ARTIFACT"),
        required=True,
        help="Baseline artifact dir and corresponding model artifact dir; repeat per task",
    )
    model_report.add_argument("--output", required=True, help="Markdown report output path")

    performance_report = sub.add_parser(
        "write-performance-report",
        help="Aggregate candidate evaluations into ProgramBench model-performance metrics",
    )
    performance_report.add_argument(
        "--candidate-report",
        action="append",
        required=True,
        help="Path to a candidate_evaluation_report.json file; repeat per task",
    )
    performance_report.add_argument("--output", required=True, help="Markdown/JSON output path")
    performance_report.add_argument("--model-name", help="Optional model name override")

    solver = sub.add_parser(
        "solve-with-openai",
        help="Generate a candidate source submission from a released solver package",
    )
    solver.add_argument("--solver-package", required=True, help="Path to released solver package")
    solver.add_argument("--output-dir", required=True, help="Directory for candidate source and run metadata")
    solver.add_argument("--model-name", help="OpenAI model name; defaults to solver/model environment")
    solver.add_argument("--attempt-id", default="attempt-1", help="Candidate attempt identifier")
    solver.add_argument("--max-rounds", type=int, default=3, help="Maximum repair rounds")
    solver.add_argument("--reasoning-effort", help="Reasoning effort for reasoning models")
    solver.add_argument("--endpoint", help="Responses API endpoint override")
    solver.add_argument("--timeout-seconds", type=int, default=900, help="Per-call model timeout")
    solver.add_argument("--max-output-tokens", type=int, help="Maximum model output tokens")
    solver.add_argument("--input-cost-per-1m", type=float, help="Optional input-token cost per 1M tokens")
    solver.add_argument("--output-cost-per-1m", type=float, help="Optional output-token cost per 1M tokens")

    gen = sub.add_parser("generate-tests", help="Generate behavioral tests")
    gen.add_argument("--task-id", required=True)
    gen.add_argument("--iterations", type=int, default=5)
    gen.add_argument("--coverage-target", type=float, help="Override coverage target for this generation run")
    gen.add_argument(
        "--min-coverage-delta",
        type=float,
        help="Override the minimum coverage delta stopping threshold",
    )
    _add_generation_backend_args(gen)

    bench = sub.add_parser("benchmark-solution", help="Run hidden tests against an executable submission")
    bench.add_argument("--task-id", required=True)
    bench.add_argument("--submission", required=True, help="Path to candidate executable")

    construct = sub.add_parser("construct-task", help="Construct task artifacts without releasing packages")
    _add_profile_run_args(construct)

    release = sub.add_parser("release-package", help="Release solver/evaluator package from constructed artifacts")
    release.add_argument("--task-id", required=True)

    evaluate = sub.add_parser("evaluate-submission", help="Evaluate a source submission against a released package")
    evaluate.add_argument("--package", required=True, help="Path to released evaluator package or manifest")
    evaluate.add_argument("--submission-source", required=True, help="Candidate source tree")
    evaluate.add_argument("--build-script", required=True, help="Candidate build script")
    evaluate.add_argument(
        "--output-dir",
        help="Optional directory for candidate run artifacts and reports outside the evaluator package",
    )
    evaluate.add_argument(
        "--execution-policy",
        choices=["trusted-local", "sandboxed-local", "docker-no-network"],
        help="Override execution policy for this candidate evaluation",
    )
    evaluate.add_argument(
        "--docker-image",
        help="Docker image to use for docker-no-network candidate evaluation",
    )
    evaluate.add_argument(
        "--trusted-local",
        action="store_true",
        help="Explicitly allow local candidate build execution for trusted fixtures",
    )
    evaluate.add_argument("--model-name", help="Candidate model name for ProgramBench metrics")
    evaluate.add_argument("--attempt-id", help="Candidate run/attempt identifier")
    evaluate.add_argument("--api-calls", type=int, help="API calls used by the model on this task")
    evaluate.add_argument("--cost-usd", type=float, help="Model cost in USD for this task")
    evaluate.add_argument(
        "--cheating-flagged",
        action="store_true",
        help="Mark this candidate as cheating-flagged/disqualified for ProgramBench scoring",
    )
    evaluate.add_argument(
        "--disqualification-reason",
        help="Optional disqualification reason, such as source lookup or reference wrapper",
    )

    run = sub.add_parser("run-task", help="Run the deterministic local pipeline from a task profile")
    _add_profile_run_args(run)

    batch = sub.add_parser("batch-run", help="Run several selected task profiles from a manifest")
    batch.add_argument("--manifest", required=True, help="Path to batch manifest YAML/JSON")
    batch.add_argument("--output", help="Optional batch summary output path")

    export = sub.add_parser("export-submission", help="Write a clean review archive for this project")
    export.add_argument(
        "--project-root",
        default=".",
        help="Project root to export; defaults to the current directory",
    )
    export.add_argument("--output", help="Optional zip output path")
    return parser


def _default_solver_model_name() -> str:
    return (
        os.environ.get("OPENAI_SOLVER_MODEL")
        or os.environ.get("PBGEN_HOSTED_MODEL_NAME")
        or DEFAULT_SOLVER_MODEL
    )


def _add_generation_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--generation-backend",
        choices=["local", "model"],
        help="Test-generation backend; defaults to config/profile value",
    )
    parser.add_argument(
        "--model-command",
        help="External model command. It receives the prompt on stdin and writes a response on stdout.",
    )
    parser.add_argument("--model-name", help="Optional model name metadata for configured model clients")
    parser.add_argument("--model-temperature", type=float, help="Optional model temperature metadata")
    parser.add_argument("--model-timeout-seconds", type=int, help="Timeout for one model call")
    parser.add_argument(
        "--model-max-output-chars",
        type=int,
        help="Maximum accepted stdout size from the model command",
    )
    parser.add_argument(
        "--model-require-structured-cases",
        action="store_true",
        help="Reject legacy raw pytest output and require top-level JSON test_cases",
    )


def _add_profile_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="Path to pbgen_task.yaml/json")
    parser.add_argument("--task-id", help="Override task id from the profile")
    parser.add_argument("--iterations", type=int, help="Override generation iteration count")
    parser.add_argument(
        "--build-system",
        default="auto",
        choices=[
            "auto",
            "script",
            "python-package",
            "make",
            "c-single",
            "cargo",
            "go",
            "cmake",
            "maven",
            "gradle",
        ],
        help="Build backend override for the selected profile",
    )
    _add_generation_backend_args(parser)


if __name__ == "__main__":
    raise SystemExit(main())
