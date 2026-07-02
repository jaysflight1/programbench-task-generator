"""Behavior-surface extraction from docs and executable help output."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.errors import PBGenError
from pbgen.logging.event_log import EventLogger
from pbgen.security import enforce_command_allowed
from pbgen.schemas import BehaviorCommand, BehaviorSurface, CommandExample, TaskSpec
from pbgen.serialization import read_data, write_data
from pbgen.testgen.example_extractor import extract_behavior_hints
from pbgen.subprocess_utils import run_command

FLAG_RE = re.compile(r"(?<!\w)(--[A-Za-z0-9][\w-]*|-[A-Za-z])(?!\w)")
SUBCOMMAND_RE = re.compile(r"^\s{0,4}([a-z][a-z0-9_-]+)\s+(.+)$")


def discover_behavior_surface(task_id: str, config: PBGenConfig) -> BehaviorSurface:
    """Extract a structured behavior surface from docs and help probes."""

    paths = ArtifactPaths(config, task_id)
    spec = TaskSpec.model_validate(read_data(paths.task_spec))
    help_outputs = _probe_help(paths.executable, config)
    docs_chunks = _read_docs(paths.repo, spec.docs_paths, max_bytes=config.max_doc_file_bytes)
    docs_text = "\n".join(text for _path, text in docs_chunks)
    combined = "\n".join(help_outputs + [docs_text])
    program_names = {paths.executable.name, "program"}
    program_names.update(Path(binary).name for binary in spec.binary_names)
    hints = extract_behavior_hints(combined, program_names)

    flags = sorted(set(FLAG_RE.findall(combined)) | set(hints.flags))
    commands = [
        BehaviorCommand(command="--help", category="help", observables=["stdout", "exit_code"]),
    ]
    if "--version" in combined or "version" in combined.lower():
        commands.append(
            BehaviorCommand(command="--version", category="version", observables=["stdout", "exit_code"])
        )

    for line in combined.splitlines():
        match = SUBCOMMAND_RE.match(line)
        if match and match.group(1) not in {"usage", "options", "commands"}:
            commands.append(
                BehaviorCommand(
                    command=match.group(1),
                    category="subcommand",
                    observables=["stdout", "stderr", "exit_code"],
                    notes=match.group(2).strip(),
                )
            )
    for usage in hints.command_usages:
        if usage.name not in {"usage", "options", "commands"}:
            commands.append(
                BehaviorCommand(
                    command=usage.name,
                    category="subcommand",
                    observables=["stdout", "stderr", "exit_code"],
                    notes=" ".join([*usage.signature, usage.description]).strip() or None,
                )
            )

    seen: set[str] = set()
    unique_commands: list[BehaviorCommand] = []
    for command in commands:
        if command.command not in seen:
            unique_commands.append(command)
            seen.add(command.command)

    examples: list[CommandExample] = []
    for rel_path, text in docs_chunks:
        doc_hints = extract_behavior_hints(text, program_names)
        examples.extend(
            CommandExample(
                args=list(example.argv),
                source=example.source,
                source_path=rel_path,
                category="example",
            )
            for example in doc_hints.examples
        )
        examples.extend(
            CommandExample(
                args=[usage.name, *_example_args_for_signature(usage.signature)],
                source="help-derived",
                source_path=rel_path,
                category="subcommand",
            )
            for usage in doc_hints.command_usages
            if _example_args_for_signature(usage.signature)
        )
    examples.extend(
        CommandExample(
            args=[usage.name, *_example_args_for_signature(usage.signature)],
            source="help-derived",
            category="subcommand",
        )
        for usage in hints.command_usages
        if _example_args_for_signature(usage.signature)
    )
    examples.extend(_help_derived_examples([command.command for command in unique_commands] + flags))
    subcommand_flags: dict[str, set[str]] = {}
    for usage in hints.command_usages:
        if usage.flags:
            subcommand_flags.setdefault(usage.name, set()).update(usage.flags)

    surface = BehaviorSurface(
        task_id=task_id,
        commands=unique_commands,
        global_flags=flags,
        subcommand_flags={name: sorted(values) for name, values in subcommand_flags.items()},
        stdin_supported="stdin" in combined.lower(),
        file_inputs=["file"] if "file" in combined.lower() else [],
        config_files=[],
        env_vars=["PBGEN_EXECUTABLE"],
        side_effects=[],
        error_cases=["invalid arguments"] if "error" in combined.lower() else [],
        command_examples=examples,
    )
    write_data(paths.behavior_surface, surface.model_dump(mode="json"))
    EventLogger(paths.event_log).append(
        task_id=task_id,
        stage="behavior_discovery",
        event_type="behavior_surface_extracted",
        metrics={
            "commands": len(surface.commands),
            "flags": len(surface.global_flags),
            "command_examples": len(surface.command_examples),
        },
    )
    return surface


def _probe_help(executable: Path, config: PBGenConfig) -> list[str]:
    outputs: list[str] = []
    for args in (["--help"], ["-h"], ["--version"], []):
        try:
            enforce_command_allowed(
                [str(executable), *args],
                policy=config.execution_policy,
                allow_patterns=config.safe_command_allow_patterns,
                deny_patterns=config.safe_command_deny_patterns,
                trusted=config.trusted_local_execution,
                command_kind="probe",
            )
            result = run_command([str(executable), *args], timeout_seconds=15)
        except (subprocess.TimeoutExpired, PBGenError):
            continue
        outputs.append(result.stdout + "\n" + result.stderr)
    return outputs


def _read_docs(repo_path: Path, docs_paths: list[str], *, max_bytes: int) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for rel in docs_paths:
        path = repo_path / rel
        if path.is_file():
            chunks.append((rel, path.read_text(encoding="utf-8", errors="replace")[:max_bytes]))
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in {"", ".md", ".txt", ".rst"}:
                    rel_child = child.relative_to(repo_path).as_posix()
                    chunks.append((rel_child, child.read_text(encoding="utf-8", errors="replace")[:max_bytes]))
    return chunks


def _help_derived_examples(commands: list[str]) -> list[CommandExample]:
    command_set = set(commands)
    examples: list[CommandExample] = []
    if "--help" in command_set:
        examples.append(CommandExample(args=["--help"], source="help-derived", category="help"))
    if "-h" in command_set:
        examples.append(CommandExample(args=["-h"], source="help-derived", category="help"))
    if "--version" in command_set:
        examples.append(CommandExample(args=["--version"], source="help-derived", category="version"))
    return examples


def _example_args_for_signature(signature: tuple[str, ...]) -> list[str]:
    args: list[str] = []
    for token in signature:
        cleaned = token.strip("[]<>").rstrip(",")
        if not cleaned:
            continue
        if cleaned.startswith("-"):
            args.append(cleaned)
            continue
        if any(word in cleaned.upper() for word in {"FILE", "PATH", "DIR"}):
            return []
        if any(word in cleaned.upper() for word in {"NUM", "INT", "FLOAT", "DECIMAL"}):
            args.append("1")
            if cleaned.endswith("..."):
                args.append("2")
        elif cleaned.rstrip(".").isupper():
            args.append("sample")
    return args
