from __future__ import annotations

import pytest

from pbgen.errors import PBGenError
from pbgen.security import command_policy_metadata, enforce_command_allowed, is_command_allowed


def test_safe_probe_command_is_accepted() -> None:
    decision = is_command_allowed(["program", "--help"], policy="sandboxed-local")

    assert decision.allowed is True
    assert decision.network_disabled is True


def test_destructive_command_is_rejected() -> None:
    decision = is_command_allowed(["program", "delete", "everything"], policy="sandboxed-local")

    assert decision.allowed is False
    assert "destructive token" in decision.reason


def test_deny_pattern_blocks_command() -> None:
    with pytest.raises(PBGenError, match="blocked by deny pattern"):
        enforce_command_allowed(
            ["program", "--danger"],
            policy="sandboxed-local",
            deny_patterns=[r"--danger"],
        )


def test_allow_pattern_must_match_when_configured() -> None:
    decision = is_command_allowed(
        ["program", "--version"],
        policy="sandboxed-local",
        allow_patterns=[r"--help"],
    )

    assert decision.allowed is False
    assert decision.reason == "command does not match any allow pattern"


def test_trusted_local_override_allows_custom_build_root() -> None:
    decision = is_command_allowed(
        ["./configure"],
        policy="trusted-local",
        trusted=True,
        command_kind="build",
    )

    assert decision.allowed is True
    assert decision.network_disabled is False


def test_docker_no_network_metadata_records_resource_limits() -> None:
    metadata = command_policy_metadata(policy="docker-no-network", timeout_seconds=12)

    assert metadata["execution_policy"] == "docker-no-network"
    assert metadata["network_disabled"] is True
    assert metadata["timeout_seconds"] == 12
    assert metadata["resource_limits"] == {"mode": "docker", "network": "none", "cpus": 2, "memory": "2g"}
