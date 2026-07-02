"""Behavior-surface extraction from docs and executable help output."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.errors import PBGenError
from pbgen.logging.event_log import EventLogger
from pbgen.security import enforce_command_allowed, is_command_allowed
from pbgen.schemas import (
    BehaviorCommand,
    BehaviorSurface,
    CommandExample,
    CommandProbe,
    RecordedCommandBehavior,
    TaskSpec,
)
from pbgen.serialization import read_data, write_data
from pbgen.testgen.example_extractor import extract_behavior_hints
from pbgen.subprocess_utils import run_command

FLAG_RE = re.compile(r"(?<!\w)(--[A-Za-z0-9][\w-]*|-[A-Za-z])(?!\w)")
SUBCOMMAND_RE = re.compile(r"^\s{0,4}([a-z][a-z0-9_-]+)\s+(.+)$")
ENV_VAR_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
CONFIG_RE = re.compile(
    r"(?<![\w/.-])(?:\.?[A-Za-z0-9_-]*(?:config|conf|settings|rc)[A-Za-z0-9_.-]*|"
    r"[A-Za-z0-9_.-]+\.(?:ya?ml|toml|ini|json|conf|cfg|rc))(?![\w/.-])",
    re.IGNORECASE,
)
FILE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.(?:txt|csv|json|yaml|yml|toml|ini|dat|in|out)\b")
ERROR_LINE_RE = re.compile(r"\b(error|invalid|unknown|missing|required|failed|failure)\b", re.IGNORECASE)
NATIVE_TEST_DIRS = {"test", "tests", "spec", "specs"}
NATIVE_TEST_SUFFIXES = {".py", ".sh", ".bats", ".txt", ".md"}
ENV_VAR_STOPWORDS = {
    "ARGS",
    "ARG",
    "COMMAND",
    "CONFIG",
    "FILE",
    "INPUT",
    "JSON",
    "NUM",
    "PATH",
    "STDIN",
    "TEXT",
    "TRUE",
    "YAML",
}
MAX_NATIVE_TEST_BYTES = 80_000
MAX_PROBES = 24


def discover_behavior_surface(task_id: str, config: PBGenConfig) -> BehaviorSurface:
    """Extract a structured behavior surface from docs and help probes."""

    paths = ArtifactPaths(config, task_id)
    spec = TaskSpec.model_validate(read_data(paths.task_spec))
    help_outputs = _probe_help(paths.executable, config)
    docs_chunks = _read_docs(paths.repo, spec.docs_paths, max_bytes=config.max_doc_file_bytes)
    native_test_chunks = _read_native_tests(paths.repo, max_bytes=config.max_doc_file_bytes)
    evidence_chunks = [*docs_chunks, *native_test_chunks]
    evidence_text = "\n".join(text for _path, text in evidence_chunks)
    combined = "\n".join(help_outputs + [evidence_text])
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
    for rel_path, text in evidence_chunks:
        doc_hints = extract_behavior_hints(text, program_names)
        examples.extend(
            CommandExample(
                args=list(example.argv),
                source=example.source,
                source_path=rel_path,
                category=_source_category(rel_path),
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

    planned_probes = _plan_command_probes(examples, config)
    recorded_behaviors = _execute_safe_probes(paths.executable, planned_probes, config)
    _write_probe_artifacts(paths, planned_probes, recorded_behaviors)

    surface = BehaviorSurface(
        task_id=task_id,
        commands=unique_commands,
        global_flags=flags,
        subcommand_flags={name: sorted(values) for name, values in subcommand_flags.items()},
        stdin_supported=_detect_stdin_support(combined),
        file_inputs=_detect_file_inputs(combined),
        config_files=_detect_config_files(combined),
        env_vars=_detect_env_vars(combined),
        side_effects=[],
        error_cases=_detect_error_cases(combined),
        command_examples=examples,
        recorded_behaviors=recorded_behaviors,
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
            "planned_probes": len(planned_probes),
            "safe_probes": sum(1 for probe in planned_probes if probe.safe),
            "recorded_behaviors": len(recorded_behaviors),
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


def _read_native_tests(repo_path: Path, *, max_bytes: int) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for child in sorted(repo_path.rglob("*")):
        if not child.is_file() or child.suffix.lower() not in NATIVE_TEST_SUFFIXES:
            continue
        rel = child.relative_to(repo_path)
        if not any(part.lower() in NATIVE_TEST_DIRS for part in rel.parts[:-1]):
            continue
        if child.stat().st_size > MAX_NATIVE_TEST_BYTES:
            continue
        chunks.append((rel.as_posix(), child.read_text(encoding="utf-8", errors="replace")[:max_bytes]))
    return chunks


def _plan_command_probes(
    examples: list[CommandExample],
    config: PBGenConfig,
) -> list[CommandProbe]:
    probes: list[CommandProbe] = []
    seen: set[tuple[str, ...]] = set()
    for example in examples:
        key = tuple(example.args)
        if key in seen:
            continue
        seen.add(key)
        decision = is_command_allowed(
            ["program", *example.args],
            policy=config.execution_policy,
            allow_patterns=config.safe_command_allow_patterns,
            deny_patterns=config.safe_command_deny_patterns,
            trusted=config.trusted_local_execution,
            command_kind="probe",
        )
        probes.append(
            CommandProbe(
                args=example.args,
                category=example.category or "example",
                source=example.source_path or example.source,
                safe=decision.allowed,
                reason=decision.reason,
            )
        )
        if len(probes) >= MAX_PROBES:
            break
    return probes


def _execute_safe_probes(
    executable: Path,
    probes: list[CommandProbe],
    config: PBGenConfig,
) -> list[RecordedCommandBehavior]:
    behaviors: list[RecordedCommandBehavior] = []
    for probe in probes:
        if not probe.safe:
            continue
        try:
            enforce_command_allowed(
                [str(executable), *probe.args],
                policy=config.execution_policy,
                allow_patterns=config.safe_command_allow_patterns,
                deny_patterns=config.safe_command_deny_patterns,
                trusted=config.trusted_local_execution,
                command_kind="probe",
            )
            result = run_command(
                [str(executable), *probe.args],
                timeout_seconds=config.probe_timeout_seconds,
            )
        except (subprocess.TimeoutExpired, PBGenError, OSError):
            continue
        behaviors.append(
            RecordedCommandBehavior(
                args=probe.args,
                exit_code=result.returncode,
                stdout=result.stdout[:20_000],
                stderr=result.stderr[:20_000],
                source=probe.source,
            )
        )
    return behaviors


def _write_probe_artifacts(
    paths: ArtifactPaths,
    planned_probes: list[CommandProbe],
    recorded_behaviors: list[RecordedCommandBehavior],
) -> None:
    write_data(
        paths.reports / "command_probes_planned.json",
        {
            "task_id": paths.task_id,
            "probes": [probe.model_dump(mode="json") for probe in planned_probes],
        },
    )
    write_data(
        paths.reports / "command_probes_observed.json",
        {
            "task_id": paths.task_id,
            "probes": [
                probe.model_dump(mode="json") for probe in planned_probes if probe.safe
            ],
            "recorded_behaviors": [
                behavior.model_dump(mode="json") for behavior in recorded_behaviors
            ],
        },
    )


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


def _source_category(source_path: str) -> str:
    parts = {part.lower() for part in Path(source_path).parts}
    return "native-test" if parts & NATIVE_TEST_DIRS else "example"


def _detect_stdin_support(text: str) -> bool:
    lowered = text.lower()
    return "stdin" in lowered or "standard input" in lowered or "| program" in lowered or "| tool" in lowered


def _detect_file_inputs(text: str) -> list[str]:
    values = set(FILE_TOKEN_RE.findall(text))
    for flag, _metavar in re.findall(r"(--[A-Za-z0-9][\w-]*)\s+(FILE|PATH|INPUT)", text):
        values.add(f"{flag} FILE")
    return sorted(values)


def _detect_config_files(text: str) -> list[str]:
    values: set[str] = set()
    for match in CONFIG_RE.finditer(text):
        value = match.group(0).rstrip(".,;:)")
        if value.startswith("--") or value.lower() in {"config", "configuration"}:
            continue
        values.add(value)
    return sorted(values)


def _detect_env_vars(text: str) -> list[str]:
    values = {"PBGEN_EXECUTABLE"}
    for match in ENV_VAR_RE.finditer(text):
        value = match.group(0)
        if value not in ENV_VAR_STOPWORDS and not value.startswith("PBGEN_"):
            values.add(value)
    return sorted(values)


def _detect_error_cases(text: str) -> list[str]:
    cases: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not ERROR_LINE_RE.search(stripped):
            continue
        normalized = " ".join(stripped.split())
        if normalized in seen:
            continue
        seen.add(normalized)
        cases.append(normalized[:200])
        if len(cases) >= 8:
            break
    return cases


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
