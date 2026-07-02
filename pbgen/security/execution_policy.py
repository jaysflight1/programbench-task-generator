"""Conservative execution-policy checks for repository commands and probes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from collections.abc import Sequence

from pbgen.errors import PBGenError


class ExecutionPolicy(StrEnum):
    """Supported command execution policies."""

    TRUSTED_LOCAL = "trusted-local"
    SANDBOXED_LOCAL = "sandboxed-local"
    DOCKER_NO_NETWORK = "docker-no-network"


DENIED_COMMAND_ROOTS = {
    "brew",
    "curl",
    "docker",
    "git",
    "npm",
    "pip",
    "pip3",
    "python -m pip",
    "python3 -m pip",
    "rm",
    "rsync",
    "scp",
    "ssh",
    "wget",
}
DENIED_TOKENS = {
    "commit",
    "delete",
    "deploy",
    "destroy",
    "drop",
    "format",
    "install",
    "push",
    "remove",
    "reset",
    "send",
    "truncate",
    "uninstall",
    "upload",
}
SHELL_MARKERS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "2>", "2>>"}
SAFE_BUILD_ROOTS = {"cc", "clang", "clang++", "cmake", "c++", "g++", "gcc", "make", "python", "python3"}


@dataclass(frozen=True)
class CommandPolicyDecision:
    """Decision record for one command safety check."""

    allowed: bool
    reason: str
    policy: ExecutionPolicy
    network_disabled: bool


def is_command_allowed(
    args: Sequence[str],
    *,
    policy: ExecutionPolicy | str = ExecutionPolicy.SANDBOXED_LOCAL,
    allow_patterns: Sequence[str] | None = None,
    deny_patterns: Sequence[str] | None = None,
    trusted: bool = False,
    command_kind: str = "probe",
) -> CommandPolicyDecision:
    """Return whether a command is allowed under the current policy."""

    resolved_policy = _policy(policy)
    network_disabled = resolved_policy in {
        ExecutionPolicy.SANDBOXED_LOCAL,
        ExecutionPolicy.DOCKER_NO_NETWORK,
    }
    if not args:
        return CommandPolicyDecision(False, "empty command", resolved_policy, network_disabled)
    command_text = " ".join(args)
    deny_match = _first_matching_pattern(command_text, deny_patterns or [])
    if deny_match:
        return CommandPolicyDecision(
            False,
            f"blocked by deny pattern: {deny_match}",
            resolved_policy,
            network_disabled,
        )
    allow_patterns = allow_patterns or []
    if allow_patterns and _first_matching_pattern(command_text, allow_patterns) is None:
        return CommandPolicyDecision(
            False,
            "command does not match any allow pattern",
            resolved_policy,
            network_disabled,
        )
    if trusted or resolved_policy == ExecutionPolicy.TRUSTED_LOCAL:
        return CommandPolicyDecision(True, "trusted local execution", resolved_policy, False)
    generic_block = _generic_block_reason(args, command_kind=command_kind)
    if generic_block is not None:
        return CommandPolicyDecision(False, generic_block, resolved_policy, network_disabled)
    return CommandPolicyDecision(True, "allowed by sandboxed command policy", resolved_policy, network_disabled)


def enforce_command_allowed(
    args: Sequence[str],
    *,
    policy: ExecutionPolicy | str = ExecutionPolicy.SANDBOXED_LOCAL,
    allow_patterns: Sequence[str] | None = None,
    deny_patterns: Sequence[str] | None = None,
    trusted: bool = False,
    command_kind: str = "probe",
) -> CommandPolicyDecision:
    """Raise a framework error if a command is not allowed."""

    decision = is_command_allowed(
        args,
        policy=policy,
        allow_patterns=allow_patterns,
        deny_patterns=deny_patterns,
        trusted=trusted,
        command_kind=command_kind,
    )
    if not decision.allowed:
        raise PBGenError(f"Command blocked by execution policy: {decision.reason}")
    return decision


def command_policy_metadata(
    *,
    policy: ExecutionPolicy | str,
    timeout_seconds: int | None,
    trusted: bool = False,
) -> dict[str, object]:
    """Return structured metadata for logs/reports."""

    resolved_policy = _policy(policy)
    return {
        "execution_policy": resolved_policy.value,
        "network_disabled": resolved_policy
        in {ExecutionPolicy.SANDBOXED_LOCAL, ExecutionPolicy.DOCKER_NO_NETWORK}
        and not trusted,
        "trusted": trusted or resolved_policy == ExecutionPolicy.TRUSTED_LOCAL,
        "timeout_seconds": timeout_seconds,
        "resource_limits": _resource_limits(resolved_policy),
    }


def _policy(value: ExecutionPolicy | str) -> ExecutionPolicy:
    if isinstance(value, ExecutionPolicy):
        return value
    try:
        return ExecutionPolicy(value)
    except ValueError as exc:
        raise PBGenError(f"Unknown execution policy: {value}") from exc


def _first_matching_pattern(command_text: str, patterns: Sequence[str]) -> str | None:
    for pattern in patterns:
        if re.search(pattern, command_text):
            return pattern
    return None


def _generic_block_reason(args: Sequence[str], *, command_kind: str) -> str | None:
    if any(arg in SHELL_MARKERS for arg in args):
        return "shell operators are not allowed"
    if any(any(marker in arg for marker in ("`", "$(", "${")) for arg in args):
        return "shell interpolation markers are not allowed"
    root = args[0]
    joined_root = " ".join(args[:3])
    if root in DENIED_COMMAND_ROOTS or joined_root in DENIED_COMMAND_ROOTS:
        return f"command root is denied: {root}"
    if command_kind == "build" and root not in SAFE_BUILD_ROOTS:
        return f"custom build command root is not allowlisted: {root}"
    if command_kind != "build" and root in SAFE_BUILD_ROOTS - {"python", "python3"}:
        return f"compiler/build command is not allowed during {command_kind}"
    lowered_tokens = {arg.lower().strip("-") for arg in args}
    destructive = lowered_tokens & DENIED_TOKENS
    if destructive:
        return f"destructive token is denied: {sorted(destructive)[0]}"
    if any(arg.startswith(("http://", "https://", "ssh://", "git@")) for arg in args):
        return "network locations are not allowed"
    return None


def _resource_limits(policy: ExecutionPolicy) -> dict[str, object]:
    if policy == ExecutionPolicy.TRUSTED_LOCAL:
        return {"mode": "trusted-host-defaults"}
    if policy == ExecutionPolicy.DOCKER_NO_NETWORK:
        return {"mode": "docker", "network": "none", "cpus": 2, "memory": "2g"}
    return {"mode": "local-timeout", "network": "best-effort-disabled"}
