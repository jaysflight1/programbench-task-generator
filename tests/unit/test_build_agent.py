from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from pbgen.build.build_agent import LocalBuildBackend, build_gold
from pbgen.config import PBGenConfig
from pbgen.errors import BuildError
from pbgen.repo_discovery.checkout import init_task
from pbgen.schemas import BuildCandidate, TaskSpec
from pbgen.serialization import read_data, write_data
from pbgen.subprocess_utils import run_command


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_python_package_wrapper_executes_console_script(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="pkg", config=config, local_path=FIXTURES / "python_package")

    artifact = build_gold("pkg", config)
    result = run_command([str(artifact.executable_path), "double", "7"], timeout_seconds=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "14"
    assert artifact.runtime_dependencies == ["python3"]
    assert "packcalc" in artifact.executable_paths


def test_make_c_build_discovers_executable(tmp_path: Path) -> None:
    if shutil.which("make") is None or shutil.which("cc") is None:
        pytest.skip("make and cc are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-c", config=config, local_path=FIXTURES / "make_c")

    artifact = build_gold("make-c", config)
    result = run_command([str(artifact.executable_path), "add", "2", "5"], timeout_seconds=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "7"
    assert artifact.build_attempts[0]["build_system"] == "make"
    assert "calc" in artifact.executable_paths


def test_make_multiple_outputs_selects_deterministic_primary(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-multi", config=config, local_path=FIXTURES / "make_multi")

    artifact = build_gold("make-multi", config)
    result = run_command([str(artifact.executable_path)], timeout_seconds=10)

    assert sorted(artifact.executable_paths) == ["alpha", "beta"]
    assert result.returncode == 0
    assert result.stdout.strip() == "alpha"


def test_cmake_c_build_discovers_executable(tmp_path: Path) -> None:
    if shutil.which("cmake") is None or shutil.which("cc") is None:
        pytest.skip("cmake and cc are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="cmake-c", config=config, local_path=FIXTURES / "cmake_c")

    artifact = build_gold("cmake-c", config)
    result = run_command([str(artifact.executable_path), "add", "4", "6"], timeout_seconds=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "10"
    assert artifact.build_attempts[0]["build_system"] == "cmake"
    assert "cmake_calc" in artifact.executable_paths


def test_single_c_source_builds_with_compiler_fallback(tmp_path: Path) -> None:
    if not any(shutil.which(compiler) for compiler in ("cc", "clang", "gcc")):
        pytest.skip("a C compiler is required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="c-single", config=config, local_path=FIXTURES / "c_single")

    artifact = build_gold("c-single", config)
    result = run_command([str(artifact.executable_path), "mul", "3", "5"], timeout_seconds=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "15"
    assert artifact.build_attempts[0]["build_system"] == "c-single"
    assert "tool" in artifact.executable_paths


def test_custom_build_command_requires_trust(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="custom", config=config, local_path=FIXTURES / "make_c")
    _write_custom_build_spec(tmp_path, "custom")

    with pytest.raises(BuildError, match="Custom build command is disabled"):
        build_gold("custom", config)


def test_trusted_custom_build_records_policy_metadata(tmp_path: Path) -> None:
    if shutil.which("make") is None or shutil.which("cc") is None:
        pytest.skip("make and cc are required for this fixture")
    config = PBGenConfig(
        workspace_root=tmp_path,
        allow_custom_build_command=True,
        trusted_local_execution=True,
        execution_policy="trusted-local",
    )
    init_task(task_id="custom", config=config, local_path=FIXTURES / "make_c")
    _write_custom_build_spec(tmp_path, "custom")

    artifact = build_gold("custom", config)

    attempt = artifact.build_attempts[0]
    assert attempt["build_system"] == "custom-command"
    assert attempt["execution_policy"] == "trusted-local"
    assert attempt["trusted"] is True
    assert attempt["timeout_seconds"] == config.build_timeout_seconds
    assert "calc" in artifact.executable_paths


@pytest.mark.parametrize(
    ("build_system", "language", "tool", "message"),
    [
        ("go", "go", "go", "go toolchain is not available"),
        ("cargo", "rust", "cargo", "cargo toolchain is not available"),
        ("maven", "java", "mvn", "mvn is not available"),
        ("gradle", "java", "gradle", "gradle is not available"),
    ],
)
def test_managed_language_builds_report_missing_toolchain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_system: str,
    language: str,
    tool: str,
    message: str,
) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "gold"
    repo.mkdir()
    _write_manifest_for_build_system(repo, build_system)
    original_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == tool else original_which(name))
    spec = TaskSpec(
        task_id=build_system,
        repo_url="local",
        commit_sha="local",
        language=language,
        build_system=build_system,
        build_candidates=[
            BuildCandidate(
                build_system=build_system,
                language=language,
                confidence=1.0,
            )
        ],
    )

    with pytest.raises(BuildError, match=message):
        LocalBuildBackend().build(spec, repo, output)

    build_log = output / "build.log"
    assert build_log.exists()
    assert "tool_unavailable" in build_log.read_text(encoding="utf-8")


def test_failing_make_writes_build_log(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-fail", config=config, local_path=FIXTURES / "make_fail")

    with pytest.raises(BuildError):
        build_gold("make-fail", config)

    build_log = tmp_path / "artifacts" / "make-fail" / "gold" / "build.log"
    assert build_log.exists()
    assert "intentional failure from robust fixture" in build_log.read_text(encoding="utf-8")


def _write_manifest_for_build_system(repo: Path, build_system: str) -> None:
    if build_system == "go":
        (repo / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    elif build_system == "cargo":
        (repo / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    elif build_system == "maven":
        (repo / "pom.xml").write_text("<project></project>\n", encoding="utf-8")
    elif build_system == "gradle":
        (repo / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")


def _write_custom_build_spec(tmp_path: Path, task_id: str) -> None:
    spec_path = tmp_path / "artifacts" / task_id / "task_spec.yaml"
    spec = TaskSpec.model_validate(read_data(spec_path)).model_copy(
        update={
            "build_system": "custom-command",
            "build_candidates": [
                BuildCandidate(
                    build_system="custom-command",
                    language="c",
                    confidence=1.0,
                    commands=[["make"]],
                    output_hints=["calc"],
                )
            ],
        }
    )
    write_data(spec_path, spec.model_dump(mode="json"))
