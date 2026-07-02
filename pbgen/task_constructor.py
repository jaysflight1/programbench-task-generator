"""Task-construction product workflow."""

from __future__ import annotations

from pathlib import Path

from pbgen.build.build_agent import build_gold
from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.errors import PBGenError
from pbgen.qc.qc_export import export_qc_queue
from pbgen.repo_discovery.checkout import init_task
from pbgen.reporting.run_summary import write_run_summary
from pbgen.schemas import BuildCandidate, QCQueueReport, RunSummaryReport, TaskProfile
from pbgen.security import enforce_command_allowed
from pbgen.serialization import read_data, write_data
from pbgen.task_profile import apply_profile_to_config, profile_primary_binary
from pbgen.testgen.behavioral_surface import discover_behavior_surface
from pbgen.testgen.controller import CoverageGuidedTestController


def construct_task_profile(
    profile: TaskProfile,
    config: PBGenConfig,
    *,
    task_id_override: str | None = None,
    iterations_override: int | None = None,
    build_system: str | None = None,
) -> RunSummaryReport:
    """Construct benchmark artifacts without releasing solver/evaluator packages."""

    task_id = task_id_from_profile(profile, task_id_override)
    run_config = apply_profile_to_config(profile, config)
    validate_profile_source(profile)

    spec = init_task(
        task_id=task_id,
        config=run_config,
        local_path=profile.local_path,
        repo_url=profile.repo_url,
        commit_sha=profile.commit_sha,
        primary_binary=profile_primary_binary(profile),
    )
    paths = ArtifactPaths(run_config, task_id)
    if profile.expected_language and profile.expected_language != spec.language:
        spec = spec.model_copy(update={"language": profile.expected_language})
        write_data(paths.task_spec, spec.model_dump(mode="json"))
    if profile.build_command is not None:
        if not run_config.allow_custom_build_command:
            raise PBGenError("Custom build commands require trusted_local: true in the task profile.")
        enforce_command_allowed(
            profile.build_command,
            policy=run_config.execution_policy,
            allow_patterns=run_config.safe_command_allow_patterns,
            deny_patterns=run_config.safe_command_deny_patterns,
            trusted=run_config.trusted_local_execution,
            command_kind="build",
        )
        spec = spec.model_copy(
            update={
                "build_system": "custom-command",
                "build_candidates": [
                    BuildCandidate(
                        build_system="custom-command",
                        language=spec.language,
                        confidence=1.0,
                        commands=[profile.build_command],
                        output_hints=[profile.primary_binary] if profile.primary_binary else [],
                    ),
                    *spec.build_candidates,
                ],
            }
        )
        write_data(paths.task_spec, spec.model_dump(mode="json"))

    build_gold(task_id, run_config, build_system=build_system or "auto")
    discover_behavior_surface(task_id, run_config)
    CoverageGuidedTestController(run_config).run(
        task_id,
        iterations=iterations_override or profile.iterations,
    )

    from pbgen.cli import evaluate_suite

    suite, _reward, _qc = evaluate_suite(
        task_id,
        run_config,
        benchmark_commands=profile.benchmark_commands,
    )
    if suite.qc_queue_size >= 0:
        export_final_qc(task_id, run_config)
    summary, _markdown_path = write_run_summary(task_id, run_config)
    return summary


def task_id_from_profile(profile: TaskProfile, override: str | None) -> str:
    """Resolve a task id from explicit override, profile metadata, or repository name."""

    if override:
        return override
    if profile.task_id:
        return profile.task_id
    if profile.local_path is not None:
        return profile.local_path.name
    if profile.repo_url:
        return Path(profile.repo_url.rstrip("/").removesuffix(".git")).name
    raise PBGenError("Task profile must include task_id, local_path, or repo_url.")


def validate_profile_source(profile: TaskProfile) -> None:
    """Validate that a task profile points at an existing source selection mode."""

    if profile.local_path is not None:
        return
    if profile.repo_url and profile.commit_sha:
        return
    raise PBGenError("Task profile must include local_path or both repo_url and commit_sha.")


def export_final_qc(task_id: str, config: PBGenConfig) -> None:
    """Export final QC JSON into CSV/Markdown when the queue exists."""

    paths = ArtifactPaths(config, task_id)
    qc_json = paths.qc / "qc_queue.json"
    if not qc_json.exists():
        return
    report = QCQueueReport.model_validate(read_data(qc_json))
    export_qc_queue(report, paths.qc)
