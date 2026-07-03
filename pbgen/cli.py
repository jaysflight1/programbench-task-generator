"""Command-line interface for the ProgramBench generator."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path
from collections.abc import Sequence

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
            if args.trusted_local:
                config.trusted_local_execution = True
                config.execution_policy = "trusted-local"
            report = evaluate_source_submission(
                CandidateSubmission(
                    package_path=Path(args.package),
                    submission_source=Path(args.submission_source),
                    build_script=Path(args.build_script),
                    output_dir=Path(args.output_dir) if args.output_dir else None,
                ),
                config,
            )
            print(f"Submission pass rate: {report.pass_rate:.3f}")
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
        "--trusted-local",
        action="store_true",
        help="Explicitly allow local candidate build execution for trusted fixtures",
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
