"""Compatibility orchestration helpers for profile-driven task runs."""

from __future__ import annotations

from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.released_package import release_task_package
from pbgen.reporting.run_summary import write_batch_summary, write_run_summary
from pbgen.schemas import BatchRunReport, RunSummaryReport, TaskProfile
from pbgen.task_constructor import construct_task_profile
from pbgen.task_profile import apply_profile_to_config, load_batch_manifest, resolve_profile_paths


def run_task_profile(
    profile: TaskProfile,
    config: PBGenConfig,
    *,
    task_id_override: str | None = None,
    iterations_override: int | None = None,
    build_system: str | None = None,
) -> RunSummaryReport:
    """Compatibility wrapper: construct, release, then rewrite the final summary."""

    construction_summary = construct_task_profile(
        profile,
        config,
        task_id_override=task_id_override,
        iterations_override=iterations_override,
        build_system=build_system,
    )
    run_config = apply_profile_to_config(profile, config)
    release_task_package(construction_summary.task_id, run_config)
    summary, _markdown_path = write_run_summary(construction_summary.task_id, run_config)
    return summary


def run_batch_manifest(
    manifest_path: Path,
    config: PBGenConfig,
    *,
    output_path: Path | None = None,
) -> BatchRunReport:
    """Run a manifest of selected task profiles through the compatibility workflow."""

    batch_id, profiles = load_batch_manifest(manifest_path)
    profiles = [resolve_profile_paths(profile, manifest_path.parent) for profile in profiles]
    summaries = [run_task_profile(profile, config) for profile in profiles]
    output = output_path or (config.artifacts_dir or config.workspace_root / "artifacts") / batch_id
    return write_batch_summary(batch_id, summaries, output)
