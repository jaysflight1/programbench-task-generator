"""Local/Git repository intake for ProgramBench task specs."""

from __future__ import annotations

import shutil
from pathlib import Path

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.errors import PBGenError
from pbgen.logging.event_log import EventLogger
from pbgen.repo_discovery.candidate_filter import is_supported_local_candidate
from pbgen.repo_discovery.metadata import analyze_repository
from pbgen.schemas import BuildCandidate, EntrypointCandidate, TaskSpec
from pbgen.serialization import write_data
from pbgen.subprocess_utils import run_command


def init_task(
    *,
    task_id: str,
    config: PBGenConfig,
    local_path: Path | None = None,
    repo_url: str | None = None,
    commit_sha: str | None = None,
    primary_binary: str | None = None,
) -> TaskSpec:
    """Create a controlled artifact copy/checkout and write `task_spec.yaml`."""

    paths = ArtifactPaths(config, task_id)
    paths.ensure_base_dirs()
    logger = EventLogger(paths.event_log)
    logger.append(task_id=task_id, stage="repo_discovery", event_type="repo_selected")

    if paths.repo.exists():
        shutil.rmtree(paths.repo)

    if local_path is not None:
        source = local_path.resolve()
        if not is_supported_local_candidate(source):
            raise PBGenError(f"Local path is not a supported repository: {source}")
        shutil.copytree(source, paths.repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        resolved_repo_url = source.as_posix()
        resolved_commit = commit_sha or "local"
    elif repo_url and commit_sha:
        result = run_command(["git", "clone", repo_url, str(paths.repo)], timeout_seconds=300)
        if not result.ok:
            raise PBGenError(f"Git clone failed: {result.stderr.strip()}")
        checkout = run_command(["git", "checkout", commit_sha], cwd=paths.repo, timeout_seconds=120)
        if not checkout.ok:
            raise PBGenError(f"Git checkout failed: {checkout.stderr.strip()}")
        resolved_repo_url = repo_url
        resolved_commit = commit_sha
    else:
        raise PBGenError("Provide either --local-path or both --repo-url and --commit.")

    analysis = analyze_repository(paths.repo, primary_binary=primary_binary)
    build_candidates = [
        _build_candidate_schema(candidate.to_dict()) for candidate in analysis.build_candidates
    ]
    entrypoint_candidates = [
        _entrypoint_candidate_schema(candidate.to_dict(), primary_binary)
        for candidate in analysis.entrypoint_candidates
    ]
    entrypoint_candidates = sorted(
        entrypoint_candidates,
        key=lambda candidate: (-candidate.confidence, candidate.path),
    )
    dependency_manifests = analysis.dependency_manifest_paths
    metadata_warnings = list(analysis.metadata_warnings)
    docs_paths = list(analysis.docs_paths)
    language, build_system = analysis.primary_language, analysis.primary_build_system
    spec = TaskSpec(
        task_id=task_id,
        repo_url=resolved_repo_url,
        commit_sha=resolved_commit,
        language=language,
        build_system=build_system,
        binary_names=[candidate.path for candidate in entrypoint_candidates],
        docs_paths=docs_paths,
        asset_paths=list(analysis.asset_paths),
        license=_detect_license(paths.repo),
        build_candidates=build_candidates,
        entrypoint_candidates=entrypoint_candidates,
        dependency_manifests=dependency_manifests,
        metadata_warnings=metadata_warnings,
    )
    write_data(paths.task_spec, spec.model_dump(mode="json"))
    logger.append(
        task_id=task_id,
        stage="repo_discovery",
        event_type="repo_cloned",
        metrics={
            "language": language or "unknown",
            "build_system": build_system or "unknown",
            "build_candidates": len(build_candidates),
            "entrypoint_candidates": len(entrypoint_candidates),
            "dependency_manifests": len(dependency_manifests),
            "metadata_warnings": len(metadata_warnings),
        },
    )
    return spec


def _build_candidate_schema(data: dict[str, object]) -> BuildCandidate:
    return BuildCandidate.model_validate(data)


def _entrypoint_candidate_schema(
    data: dict[str, object],
    primary_binary: str | None,
) -> EntrypointCandidate:
    candidate = EntrypointCandidate.model_validate(data)
    if primary_binary and primary_binary in {candidate.name, candidate.path}:
        return candidate.model_copy(
            update={
                "confidence": 1.0,
                "reason": f"explicit primary binary override: {primary_binary}",
            }
        )
    return candidate


def _detect_license(repo_path: Path) -> str | None:
    for child in sorted(repo_path.iterdir()):
        if child.name.lower().startswith("license"):
            return child.name
    return None
