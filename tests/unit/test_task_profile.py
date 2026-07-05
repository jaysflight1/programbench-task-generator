from __future__ import annotations

import json
from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.schemas import TaskProfile
from pbgen.task_profile import (
    apply_profile_to_config,
    discover_profile,
    load_batch_manifest,
    load_task_profile,
    profile_primary_binary,
)


def test_load_task_profile_from_json(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "task_id": "json-task",
                "local_path": "repos/json-task",
                "primary_binary": "bin/json-task",
                "coverage_backend": "python",
            }
        ),
        encoding="utf-8",
    )

    profile = load_task_profile(profile_path)

    assert profile.task_id == "json-task"
    assert profile.local_path == Path("repos/json-task")
    assert profile.coverage_backend == "python"
    assert profile_primary_binary(profile) == "bin/json-task"


def test_load_task_profile_from_yaml_and_discover_profile(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "pbgen_task.yaml").write_text(
        """
task_id: yaml-task
benchmark_commands:
  - [tool, --version]
coverage_target: 0.92
trusted_local: true
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_task_profile(repo_path / "pbgen_task.yaml")
    discovered = discover_profile(repo_path)

    assert loaded.task_id == "yaml-task"
    assert loaded.coverage_target == 0.92
    assert discovered is not None
    assert discovered.task_id == "yaml-task"
    assert profile_primary_binary(discovered) == "tool"


def test_load_batch_manifest_uses_filename_when_batch_id_is_omitted(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "batch_manifest.yaml"
    manifest_path.write_text(
        """
tasks:
  - task_id: first
    repo_url: https://example.invalid/first.git
    commit_sha: abc123
  - task_id: second
    local_path: repos/second
    primary_binary: runner
""".lstrip(),
        encoding="utf-8",
    )

    batch_id, tasks = load_batch_manifest(manifest_path)

    assert batch_id == "batch_manifest"
    assert [task.task_id for task in tasks] == ["first", "second"]
    assert tasks[1].local_path == Path("repos/second")
    assert profile_primary_binary(tasks[1]) == "runner"


def test_apply_profile_to_config_returns_copy_with_overrides(tmp_path: Path) -> None:
    config = PBGenConfig(
        workspace_root=tmp_path,
        coverage_backend="python",
        coverage_target=0.5,
        min_coverage_delta_per_iteration=0.2,
        allow_network_dependency_fetch=True,
        allow_custom_build_command=True,
    )
    profile = TaskProfile(
        coverage_backend="lcov",
        coverage_target=0.9,
        min_coverage_delta=0.03,
        dependency_policy="offline",
        trusted_local=False,
        execution_policy="docker-no-network",
        safe_command_allow_patterns=[r"^program --help$"],
        safe_command_deny_patterns=[r"delete"],
        generation_backend="model",
        model_provider="external-command",
        model_command=["model-cli", "--json"],
        model_name="fast-model",
        model_temperature=0.1,
        model_timeout_seconds=900,
        model_max_output_chars=2_000_000,
        model_require_structured_cases=True,
    )

    updated = apply_profile_to_config(profile, config)

    assert updated is not config
    assert updated.coverage_backend == "lcov"
    assert updated.coverage_target == 0.9
    assert updated.min_coverage_delta_per_iteration == 0.03
    assert updated.allow_network_dependency_fetch is False
    assert updated.allow_custom_build_command is False
    assert updated.trusted_local_execution is False
    assert updated.execution_policy == "docker-no-network"
    assert updated.safe_command_allow_patterns == [r"^program --help$"]
    assert updated.safe_command_deny_patterns == [r"delete"]
    assert updated.dependency_policy == "offline"
    assert updated.generation_backend == "model"
    assert updated.model_provider == "external-command"
    assert updated.model_command == ["model-cli", "--json"]
    assert updated.model_name == "fast-model"
    assert updated.model_temperature == 0.1
    assert updated.model_timeout_seconds == 900
    assert updated.model_max_output_chars == 2_000_000
    assert updated.model_require_structured_cases is True
    assert config.coverage_backend == "python"
    assert config.coverage_target == 0.5
    assert config.min_coverage_delta_per_iteration == 0.2
    assert config.allow_network_dependency_fetch is True
    assert config.allow_custom_build_command is True


def test_resolve_profile_paths_uses_profile_directory(tmp_path: Path) -> None:
    from pbgen.task_profile import resolve_profile_paths

    base = tmp_path / "profiles"
    base.mkdir()
    profile = TaskProfile(local_path=Path("../repos/tool"))

    resolved = resolve_profile_paths(profile, base)

    assert resolved.local_path == (tmp_path / "repos" / "tool").resolve()
    assert profile.local_path == Path("../repos/tool")


def test_shipped_profile_examples_load_and_resolve() -> None:
    from pbgen.task_profile import resolve_profile_paths

    profiles_dir = Path(__file__).parents[2] / "examples" / "profiles"

    python_profile = resolve_profile_paths(
        load_task_profile(profiles_dir / "python_package_cli.pbgen_task.yaml"),
        profiles_dir,
    )
    c_profile = resolve_profile_paths(
        load_task_profile(profiles_dir / "c_make_cli.pbgen_task.yaml"),
        profiles_dir,
    )

    assert python_profile.task_id == "python_pkgcalc"
    assert python_profile.local_path is not None
    assert python_profile.local_path.exists()
    assert python_profile.expected_language == "python"
    assert profile_primary_binary(python_profile) == "pkgcalc"

    assert c_profile.task_id == "c_make_ccalc"
    assert c_profile.local_path is not None
    assert c_profile.local_path.exists()
    assert c_profile.expected_language == "c/c++"
    assert profile_primary_binary(c_profile) == "ccalc"
