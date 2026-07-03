from __future__ import annotations

from pathlib import Path

import pytest

from pbgen.errors import PBGenError
from pbgen.security.command_executor import DockerNoNetworkCommandRunner
from pbgen.subprocess_utils import CommandResult


def test_docker_no_network_args_mount_workspace_and_disable_network(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    build_script = source / "build.py"
    build_script.write_text("print('build')\n", encoding="utf-8")
    runner = DockerNoNetworkCommandRunner(
        tmp_path,
        image="python:3.12-slim",
        docker_executable="docker",
    )

    args = runner.docker_args(
        ["python3", str(build_script)],
        cwd=source,
        env={"PBGEN_SAMPLE": "case-env"},
        stdin="hello\n",
    )

    assert args[:3] == ["docker", "run", "--rm"]
    assert _option_value(args, "--network") == "none"
    assert _option_value(args, "--cpus") == "2"
    assert _option_value(args, "--memory") == "2g"
    assert _option_value(args, "-v") == f"{tmp_path.resolve()}:/workspace"
    assert _option_value(args, "-w") == "/workspace/source"
    assert "-i" in args
    assert "PBGEN_SAMPLE=case-env" in args
    assert "python:3.12-slim" in args
    assert "/workspace/source/build.py" in args
    assert str(build_script) not in args


def test_docker_no_network_args_reject_paths_outside_mount(tmp_path: Path) -> None:
    mount_root = tmp_path / "mount"
    mount_root.mkdir()
    outside_path = tmp_path / "outside.py"
    outside_path.write_text("print('outside')\n", encoding="utf-8")
    runner = DockerNoNetworkCommandRunner(mount_root)

    with pytest.raises(PBGenError, match="escapes Docker mount root"):
        runner.docker_args([str(outside_path)], cwd=mount_root)


def test_docker_no_network_args_reject_invalid_env_name(tmp_path: Path) -> None:
    runner = DockerNoNetworkCommandRunner(tmp_path)

    with pytest.raises(PBGenError, match="Invalid environment variable"):
        runner.docker_args(["python3"], env={"bad-name": "value"})


def test_docker_preflight_reports_missing_local_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pbgen.security.command_executor.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        "pbgen.security.command_executor.run_command",
        lambda *args, **kwargs: CommandResult(
            args=["docker", "image", "inspect", "missing:image"],
            returncode=1,
            stdout="",
            stderr="No such image",
            cwd=tmp_path,
        ),
    )
    runner = DockerNoNetworkCommandRunner(tmp_path, image="missing:image")

    with pytest.raises(PBGenError, match="not available locally"):
        runner.preflight()


def _option_value(args: list[str], option: str) -> str:
    index = args.index(option)
    return args[index + 1]
