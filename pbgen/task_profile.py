"""Passive loading helpers for ProgramBench task profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pbgen.config import PBGenConfig
from pbgen.schemas import TaskProfile
from pbgen.serialization import read_data


PROFILE_FILENAMES = ("pbgen_task.yaml", "pbgen_task.yml", "pbgen_task.json")

_NETWORK_ENABLED_POLICIES = {
    "allow-network",
    "allow_network",
    "network",
    "online",
}


def load_task_profile(path: Path) -> TaskProfile:
    """Load one JSON/YAML task profile from disk."""

    return TaskProfile.model_validate(read_data(path))


def discover_profile(repo_path: Path) -> TaskProfile | None:
    """Find and load a standard task profile at a repository root."""

    for filename in PROFILE_FILENAMES:
        profile_path = repo_path / filename
        if profile_path.is_file():
            return load_task_profile(profile_path)
    return None


def apply_profile_to_config(profile: TaskProfile, config: PBGenConfig) -> PBGenConfig:
    """Return a config copy with supported task-profile overrides applied."""

    updates: dict[str, object] = {}
    fields = config.__class__.model_fields

    if profile.coverage_backend is not None and "coverage_backend" in fields:
        updates["coverage_backend"] = profile.coverage_backend
    if profile.coverage_target is not None and "coverage_target" in fields:
        updates["coverage_target"] = profile.coverage_target
    if profile.min_coverage_delta is not None:
        if "min_coverage_delta_per_iteration" in fields:
            updates["min_coverage_delta_per_iteration"] = profile.min_coverage_delta
        elif "min_coverage_delta" in fields:
            updates["min_coverage_delta"] = profile.min_coverage_delta
    if "allow_network_dependency_fetch" in fields:
        updates["allow_network_dependency_fetch"] = _allows_network_dependency_fetch(profile)
    if "allow_custom_build_command" in fields:
        updates["allow_custom_build_command"] = profile.trusted_local
    if "trusted_local_execution" in fields:
        updates["trusted_local_execution"] = profile.trusted_local
    if "execution_policy" in fields:
        updates["execution_policy"] = _execution_policy(profile)
    if "safe_command_allow_patterns" in fields:
        updates["safe_command_allow_patterns"] = profile.safe_command_allow_patterns
    if "safe_command_deny_patterns" in fields:
        updates["safe_command_deny_patterns"] = profile.safe_command_deny_patterns
    if "dependency_policy" in fields:
        updates["dependency_policy"] = profile.dependency_policy
    if profile.generation_backend is not None and "generation_backend" in fields:
        updates["generation_backend"] = profile.generation_backend
    if profile.docker_image is not None and "docker_image" in fields:
        updates["docker_image"] = profile.docker_image
    if profile.model_provider is not None and "model_provider" in fields:
        updates["model_provider"] = profile.model_provider
    if profile.model_command is not None and "model_command" in fields:
        updates["model_command"] = profile.model_command
    if profile.model_name is not None and "model_name" in fields:
        updates["model_name"] = profile.model_name
    if profile.model_temperature is not None and "model_temperature" in fields:
        updates["model_temperature"] = profile.model_temperature

    return config.model_copy(update=updates)


def resolve_profile_paths(profile: TaskProfile, base_dir: Path) -> TaskProfile:
    """Resolve relative filesystem paths in a profile against its source directory."""

    updates: dict[str, object] = {}
    if profile.local_path is not None and not profile.local_path.is_absolute():
        updates["local_path"] = (base_dir / profile.local_path).resolve()
    return profile.model_copy(update=updates) if updates else profile


def profile_primary_binary(profile: TaskProfile) -> str | None:
    """Return the preferred executable name/path for a profile, if one is known."""

    if profile.primary_binary:
        return profile.primary_binary
    for command in profile.benchmark_commands:
        if command:
            return command[0]
    return None


def load_batch_manifest(path: Path) -> tuple[str, list[TaskProfile]]:
    """Load a batch manifest with an optional batch id and a task profile list."""

    data = read_data(path)
    batch_id = _batch_id(data.get("batch_id"), path)
    tasks_data = data.get("tasks")
    if not isinstance(tasks_data, list):
        raise ValueError(f"{path} must contain a 'tasks' list.")

    tasks: list[TaskProfile] = []
    for index, task_data in enumerate(tasks_data):
        if not isinstance(task_data, dict):
            raise ValueError(f"{path} tasks[{index}] must contain a mapping.")
        tasks.append(TaskProfile.model_validate(task_data))
    return batch_id, tasks


def _allows_network_dependency_fetch(profile: TaskProfile) -> bool:
    policy = profile.dependency_policy.strip().lower()
    return policy in _NETWORK_ENABLED_POLICIES


def _execution_policy(profile: TaskProfile) -> str:
    if profile.execution_policy:
        return profile.execution_policy
    return "trusted-local" if profile.trusted_local else "sandboxed-local"


def _batch_id(value: Any, path: Path) -> str:
    if value is None:
        return path.stem
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} batch_id must be a non-empty string.")
    return value
