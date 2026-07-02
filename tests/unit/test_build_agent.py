from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from pbgen.build.build_agent import build_gold
from pbgen.config import PBGenConfig
from pbgen.errors import BuildError
from pbgen.repo_discovery.checkout import init_task
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
