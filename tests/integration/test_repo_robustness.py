from pathlib import Path

import pytest

from pbgen.build.build_agent import build_gold
from pbgen.config import PBGenConfig
from pbgen.errors import BuildError
from pbgen.repo_discovery.checkout import init_task


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_python_package_cli_builds_wrapper(tmp_path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="pkg", config=config, local_path=FIXTURES / "python_package_cli")
    artifact = build_gold("pkg", config)
    assert artifact.executable_path.exists()
    assert "pkgcalc" in artifact.executable_paths


def test_make_c_cli_builds_and_records_attempt(tmp_path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="ccalc", config=config, local_path=FIXTURES / "make_c_cli")
    artifact = build_gold("ccalc", config)
    assert artifact.executable_path.exists()
    assert any(attempt["build_system"] == "make" for attempt in artifact.build_attempts)


def test_multiple_binaries_are_ranked_deterministically_with_override(tmp_path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    spec = init_task(
        task_id="multi",
        config=config,
        local_path=FIXTURES / "multi_binary_scripts",
        primary_binary="beta",
    )
    assert spec.entrypoint_candidates[0].name == "beta"


def test_failing_build_has_clear_log(tmp_path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="broken", config=config, local_path=FIXTURES / "failing_build")
    with pytest.raises(BuildError, match="build log"):
        build_gold("broken", config)
    assert (tmp_path / "artifacts" / "broken" / "gold" / "build.log").exists()


def test_weird_docs_excludes_vendor_docs(tmp_path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    spec = init_task(task_id="docs", config=config, local_path=FIXTURES / "weird_docs")
    assert any("docs/tutorial/USAGE.rst" in path for path in spec.docs_paths)
    assert not any("vendor" in path for path in spec.docs_paths)
