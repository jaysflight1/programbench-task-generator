"""Extract command examples and usage hints from docs and help text."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re
import shlex

from pbgen.schemas import CommandExample


FLAG_RE = re.compile(r"(?<!\w)(--[A-Za-z0-9][\w-]*|-[A-Za-z])(?!\w)")
FENCED_BLOCK_RE = re.compile(r"```[^\n`]*\n(?P<body>.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
PROMPT_RE = re.compile(r"^\s*(?:\$|>)\s+(?P<command>.+?)\s*$")
USAGE_PROGRAM_RE = re.compile(r"^\s*Usage:\s+([^\s]+)", re.IGNORECASE | re.MULTILINE)
HELP_COMMAND_RE = re.compile(
    r"^\s*(?:[-*]\s+)?`?(?P<name>[a-z][a-z0-9_-]*)"
    r"(?P<signature>(?:\s+(?:\[?[A-Z][A-Z0-9_-]*(?:\.\.\.)?\]?|<[^>]+>|"
    r"--[A-Za-z0-9][\w-]*|-[A-Za-z]))*)`?"
    r"(?:\s{2,}|\s+-\s+|\s+prints\s+|:\s+)"
    r"(?P<description>.+?)\s*$"
)

COMMON_PROGRAM_NAMES = {"program", "prog", "cli", "tool", "app", "command"}
SHELL_OPERATORS = {
    "|",
    "||",
    "&",
    "&&",
    ";",
    ">",
    ">>",
    "<",
    "<<",
    "2>",
    "2>>",
}
SECTION_HEADINGS = {"commands", "options", "usage", "examples", "example"}


@dataclass(frozen=True)
class ExtractedExample:
    """A safe argv fragment parsed from examples.

    The executable name is stripped, so argv contains only arguments to pass to
    the gold program.
    """

    argv: tuple[str, ...]
    raw: str
    source: str


@dataclass(frozen=True)
class CommandUsage:
    """A subcommand signature parsed from help text or docs."""

    name: str
    signature: tuple[str, ...] = ()
    description: str = ""
    flags: tuple[str, ...] = ()
    source: str = ""


@dataclass(frozen=True)
class ExampleExtraction:
    """All behavior hints found in untrusted docs/help text."""

    examples: tuple[ExtractedExample, ...]
    command_usages: tuple[CommandUsage, ...]
    flags: tuple[str, ...]
    program_names: tuple[str, ...]


def extract_behavior_hints(text: str, program_names: Iterable[str] = ()) -> ExampleExtraction:
    """Parse docs/help text for examples, subcommand usages, and flags."""

    names = _normalise_program_names([*program_names, *extract_program_names(text)])
    return ExampleExtraction(
        examples=tuple(extract_examples(text, names)),
        command_usages=tuple(extract_command_usages(text, names)),
        flags=tuple(sorted(set(FLAG_RE.findall(text)))),
        program_names=tuple(sorted(names)),
    )


def extract_command_examples(
    text: str,
    *,
    program_names: Iterable[str] = (),
    source_path: str | None = None,
) -> list[CommandExample]:
    """Return schema command examples parsed from docs text."""

    names = _normalise_program_names([*program_names, *extract_program_names(text)])
    return [
        CommandExample(
            args=list(example.argv),
            source=example.source,
            source_path=source_path,
            category=_category_for_args(example.argv),
        )
        for example in extract_examples(text, names)
    ]


def help_derived_examples(commands: Iterable[str]) -> list[CommandExample]:
    """Return safe examples for universal help/version flags."""

    examples: list[CommandExample] = []
    command_set = set(commands)
    if "--help" in command_set:
        examples.append(CommandExample(args=["--help"], source="help-derived", category="help"))
    if "-h" in command_set:
        examples.append(CommandExample(args=["-h"], source="help-derived", category="help"))
    if "--version" in command_set:
        examples.append(CommandExample(args=["--version"], source="help-derived", category="version"))
    return examples


def extract_program_names(text: str) -> list[str]:
    """Return executable names seen in usage lines."""

    names: list[str] = []
    for match in USAGE_PROGRAM_RE.finditer(text):
        token = match.group(1).strip()
        if token and token not in {"COMMAND", "[COMMAND]"}:
            names.append(_basename(token))
    return _dedupe(names)


def extract_examples(text: str, program_names: Iterable[str] = ()) -> list[ExtractedExample]:
    """Extract shell-style, fenced, and inline examples without executing them."""

    names = _normalise_program_names(program_names)
    examples: list[ExtractedExample] = []
    for raw, source, require_program in _candidate_example_strings(text):
        parsed = _parse_example_argv(raw, names, require_program=require_program)
        if parsed is None:
            continue
        examples.append(ExtractedExample(argv=parsed, raw=raw.strip(), source=source))
    return _dedupe_examples(examples)


def extract_command_usages(text: str, program_names: Iterable[str] = ()) -> list[CommandUsage]:
    """Extract help-derived subcommand signatures from docs and help output."""

    names = _normalise_program_names(program_names)
    usages: list[CommandUsage] = []
    for line in text.splitlines():
        parsed = _parse_help_command_line(line)
        if parsed is not None:
            usages.append(parsed)
    for snippet in INLINE_CODE_RE.findall(text):
        parsed = _parse_usage_snippet(snippet, names, source="inline")
        if parsed is not None:
            usages.append(parsed)
    for block in FENCED_BLOCK_RE.finditer(text):
        for line in block.group("body").splitlines():
            parsed = _parse_usage_snippet(line, names, source="fenced")
            if parsed is not None:
                usages.append(parsed)
    return _dedupe_usages(usages)


def _candidate_example_strings(text: str) -> list[tuple[str, str, bool]]:
    candidates: list[tuple[str, str, bool]] = []
    for line in text.splitlines():
        prompt = PROMPT_RE.match(line)
        if prompt:
            candidates.append((prompt.group("command"), "shell", False))
    for block in FENCED_BLOCK_RE.finditer(text):
        for line in block.group("body").splitlines():
            prompt = PROMPT_RE.match(line)
            if prompt:
                candidates.append((prompt.group("command"), "fenced-shell", False))
            elif line.strip():
                candidates.append((line.strip(), "fenced", True))
    for snippet in INLINE_CODE_RE.findall(text):
        candidates.append((snippet.strip(), "inline", True))
    return candidates


def _parse_example_argv(
    raw: str,
    program_names: set[str],
    *,
    require_program: bool,
) -> tuple[str, ...] | None:
    if not raw or "\n" in raw or _contains_shell_syntax(raw):
        return None
    try:
        tokens = shlex.split(raw, comments=False, posix=True)
    except ValueError:
        return None
    if not tokens or any(_unsafe_token(token) for token in tokens):
        return None
    stripped, had_program = _strip_program_token(tokens, program_names)
    if require_program and not had_program and not _looks_like_inline_example(stripped):
        return None
    if any(_looks_like_metavar(token) for token in stripped):
        return None
    return tuple(stripped)


def _parse_help_command_line(line: str) -> CommandUsage | None:
    stripped = line.strip()
    if not stripped or stripped.rstrip(":").lower() in SECTION_HEADINGS:
        return None
    match = HELP_COMMAND_RE.match(line)
    if not match:
        return None
    name = match.group("name")
    if name in SECTION_HEADINGS:
        return None
    signature = tuple(match.group("signature").split())
    description = match.group("description").strip().rstrip(".")
    flags = tuple(sorted({flag for token in signature for flag in FLAG_RE.findall(token)}))
    return CommandUsage(
        name=name,
        signature=signature,
        description=description,
        flags=flags,
        source="help",
    )


def _parse_usage_snippet(
    snippet: str,
    program_names: set[str],
    *,
    source: str,
) -> CommandUsage | None:
    if not snippet or "\n" in snippet or _contains_shell_syntax(snippet):
        return None
    try:
        tokens = shlex.split(snippet, comments=False, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    stripped, _had_program = _strip_program_token(tokens, program_names)
    if len(stripped) < 2 or stripped[0].startswith("-"):
        return None
    name = stripped[0]
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or name in SECTION_HEADINGS:
        return None
    signature = tuple(stripped[1:])
    has_metavar = any(_looks_like_metavar(token) for token in signature)
    has_only_flags = all(token.startswith("-") for token in signature)
    if not has_metavar and not has_only_flags:
        return None
    flags = tuple(sorted({flag for token in signature for flag in FLAG_RE.findall(token)}))
    return CommandUsage(name=name, signature=signature, flags=flags, source=source)


def _strip_program_token(tokens: list[str], program_names: set[str]) -> tuple[list[str], bool]:
    if not tokens:
        return tokens, False
    first = _basename(tokens[0])
    if first in program_names:
        return tokens[1:], True
    return tokens, False


def _normalise_program_names(program_names: Iterable[str]) -> set[str]:
    names = {name for item in program_names if (name := _basename(str(item).strip()))}
    return names | COMMON_PROGRAM_NAMES


def _basename(token: str) -> str:
    return token.rstrip("/").split("/")[-1].removeprefix("./")


def _contains_shell_syntax(raw: str) -> bool:
    return any(marker in raw for marker in ("`", "$(", "${"))


def _unsafe_token(token: str) -> bool:
    if token in SHELL_OPERATORS:
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
        return True
    if token.startswith(("/", "~")) or ".." in token.split("/"):
        return True
    return False


def _looks_like_inline_example(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if tokens[0].startswith("-"):
        return True
    if len(tokens) < 2 or not re.fullmatch(r"[a-z][a-z0-9_-]*", tokens[0]):
        return False
    return any(_looks_like_value(token) or token.startswith("-") for token in tokens[1:])


def _looks_like_value(token: str) -> bool:
    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        return True
    return token.lower() in {"true", "false", "yes", "no"}


def _looks_like_metavar(token: str) -> bool:
    cleaned = token.strip("[]<>").rstrip(",")
    if cleaned.endswith("..."):
        cleaned = cleaned[:-3]
    return cleaned.isupper() and any(char.isalpha() for char in cleaned)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_examples(examples: Iterable[ExtractedExample]) -> list[ExtractedExample]:
    seen: set[tuple[str, ...]] = set()
    result: list[ExtractedExample] = []
    for example in examples:
        if example.argv in seen:
            continue
        seen.add(example.argv)
        result.append(example)
    return result


def _dedupe_usages(usages: Iterable[CommandUsage]) -> list[CommandUsage]:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    result: list[CommandUsage] = []
    for usage in usages:
        key = (usage.name, usage.signature)
        if key in seen:
            continue
        seen.add(key)
        result.append(usage)
    return result


def _category_for_args(argv: tuple[str, ...]) -> str:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        return "help"
    if argv[0] == "--version" or "version" in argv[0].lower():
        return "version"
    if argv[0].startswith("-"):
        return "flag"
    return "subcommand"
