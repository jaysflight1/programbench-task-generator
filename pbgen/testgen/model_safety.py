"""Safety validation for model-generated pytest source."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
import re
import shlex
import sys
from typing import Literal


Severity = Literal["error"]

DEFAULT_ALLOWED_IMPORT_ROOTS = frozenset({"pytest", "typing_extensions"})

NETWORK_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "boto3",
        "botocore",
        "ftplib",
        "http",
        "httpx",
        "imaplib",
        "paramiko",
        "poplib",
        "requests",
        "smtplib",
        "socket",
        "telnetlib",
        "urllib",
        "webbrowser",
        "websockets",
    }
)

SHELLISH_COMMAND_TOKENS = frozenset(
    {
        "apt",
        "apt-get",
        "brew",
        "conda",
        "curl",
        "del",
        "delete",
        "dnf",
        "erase",
        "ftp",
        "install",
        "mamba",
        "nc",
        "ncat",
        "npm",
        "pip",
        "pip3",
        "pnpm",
        "poetry",
        "remove",
        "rm",
        "rmdir",
        "scp",
        "sftp",
        "ssh",
        "uninstall",
        "uv",
        "wget",
        "yarn",
        "yum",
    }
)

SUBPROCESS_CALLS = frozenset({"run", "call", "check_call", "check_output", "Popen"})
OS_PROCESS_CALL_PREFIXES = ("exec", "fork", "popen", "spawn", "system")
OS_FILESYSTEM_MUTATION_CALLS = frozenset(
    {
        "chmod",
        "chown",
        "link",
        "makedirs",
        "mkdir",
        "remove",
        "removedirs",
        "rename",
        "replace",
        "rmdir",
        "symlink",
        "unlink",
    }
)
SHUTIL_FILESYSTEM_MUTATION_CALLS = frozenset(
    {
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "move",
        "rmtree",
    }
)
PATH_FILESYSTEM_MUTATION_METHODS = frozenset(
    {
        "chmod",
        "hardlink_to",
        "mkdir",
        "rename",
        "replace",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
WRITE_MODE_MARKERS = frozenset({"w", "a", "x", "+"})
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")
URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
TOKEN_STRIP_CHARS = "\"'`,;:()[]{}"


@dataclass(frozen=True, slots=True)
class ModelSafetyPolicy:
    """Policy knobs for validating one generated pytest source string."""

    allowed_import_roots: frozenset[str] = DEFAULT_ALLOWED_IMPORT_ROOTS
    target_import_roots: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ModelSafetyDiagnostic:
    """One safety validation finding."""

    rule_id: str
    message: str
    line: int
    column: int
    severity: Severity = "error"


@dataclass(frozen=True, slots=True)
class ModelSafetyReport:
    """Structured result for model-generated pytest safety validation."""

    diagnostics: tuple[ModelSafetyDiagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    def has_rule(self, rule_id: str) -> bool:
        return any(diagnostic.rule_id == rule_id for diagnostic in self.diagnostics)


def validate_model_generated_pytest(
    source: str,
    policy: ModelSafetyPolicy | None = None,
) -> ModelSafetyReport:
    """Validate generated pytest source without raising for ordinary failures."""

    active_policy = policy or ModelSafetyPolicy()
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ModelSafetyReport(
            (
                ModelSafetyDiagnostic(
                    rule_id="syntax_error",
                    message=exc.msg,
                    line=exc.lineno or 0,
                    column=max((exc.offset or 1) - 1, 0),
                ),
            )
        )

    env_names = _collect_trusted_env_names(tree)
    command_names = _collect_trusted_command_names(tree, env_names)
    validator = _ModelSafetyVisitor(active_policy, env_names, command_names)
    validator.visit(tree)
    return ModelSafetyReport(tuple(validator.diagnostics))


class _ModelSafetyVisitor(ast.NodeVisitor):
    def __init__(
        self,
        policy: ModelSafetyPolicy,
        env_names: frozenset[str],
        command_names: frozenset[str],
    ) -> None:
        self.policy = policy
        self.allowed_import_roots = frozenset(sys.stdlib_module_names) | policy.allowed_import_roots
        self.target_import_roots = policy.target_import_roots
        self.env_names = env_names
        self.command_names = command_names
        self.diagnostics: list[ModelSafetyDiagnostic] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._validate_import(alias.name.split(".", 1)[0], node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            return
        if node.level:
            self._add("relative_import", "relative imports are not allowed", node)
        if node.module:
            self._validate_import(node.module.split(".", 1)[0], node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._validate_shell_keyword(node)
        self._validate_process_call(node)
        self._validate_subprocess_call(node)
        self._validate_filesystem_mutation_call(node)
        self._validate_open_write_call(node)
        self._validate_dynamic_import(node)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._validate_string_literal(node.value, node)
        self.generic_visit(node)

    def _validate_import(self, root: str, node: ast.AST) -> None:
        if root in NETWORK_IMPORT_ROOTS:
            self._add("network_import", f"network import '{root}' is not allowed", node)
            return
        if root in self.target_import_roots:
            self._add("target_import", f"target import '{root}' is not allowed", node)
            return
        if root not in self.allowed_import_roots:
            self._add(
                "target_import",
                f"non-stdlib import '{root}' is treated as a likely target import",
                node,
            )

    def _validate_shell_keyword(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg == "shell" and _constant_bool(keyword.value) is True:
                self._add("shell_true", "shell=True is not allowed", keyword.value)

    def _validate_process_call(self, node: ast.Call) -> None:
        path = _attribute_path(node.func)
        if not path or path[0] != "os":
            return
        attr = path[-1]
        if attr.startswith(OS_PROCESS_CALL_PREFIXES):
            self._add("os_process_call", f"os.{attr} is not allowed", node)

    def _validate_subprocess_call(self, node: ast.Call) -> None:
        path = _attribute_path(node.func)
        if not path or path[0] != "subprocess" or path[-1] not in SUBPROCESS_CALLS:
            return
        if not node.args:
            self._add("subprocess_command_missing", "subprocess call must pass a command", node)
            return
        if not _is_safe_subprocess_command(node.args[0], self.env_names, self.command_names):
            self._add(
                "arbitrary_subprocess_command",
                "subprocess command must execute PBGEN_EXECUTABLE as its root",
                node.args[0],
            )

    def _validate_filesystem_mutation_call(self, node: ast.Call) -> None:
        path = _attribute_path(node.func)
        attr = path[-1] if path else _method_name(node.func)
        root = path[0] if path else None
        if root == "os" and attr in OS_FILESYSTEM_MUTATION_CALLS:
            self._add("filesystem_mutation", f"os.{attr} mutates the filesystem", node)
        if root == "shutil" and attr in SHUTIL_FILESYSTEM_MUTATION_CALLS:
            self._add("filesystem_mutation", f"shutil.{attr} mutates the filesystem", node)
        if attr in PATH_FILESYSTEM_MUTATION_METHODS:
            self._add("filesystem_mutation", f"{attr} mutates the filesystem", node)

    def _validate_open_write_call(self, node: ast.Call) -> None:
        path = _attribute_path(node.func)
        attr = path[-1] if path else _method_name(node.func)
        is_builtin_open = path == ("open",)
        is_path_open = attr == "open" and not is_builtin_open
        if not is_builtin_open and not is_path_open:
            return
        mode = _open_mode(node, is_path_open=is_path_open)
        if mode is not None and any(marker in mode for marker in WRITE_MODE_MARKERS):
            self._add("filesystem_mutation", "opening files in write mode is not allowed", node)

    def _validate_dynamic_import(self, node: ast.Call) -> None:
        path = _attribute_path(node.func)
        if path == ("__import__",) or path == ("importlib", "import_module"):
            self._add("dynamic_import", "dynamic imports are not allowed", node)

    def _validate_string_literal(self, value: str, node: ast.AST) -> None:
        if _contains_absolute_local_path(value):
            self._add("absolute_local_path", "absolute local paths are not allowed", node)
        if _contains_path_traversal(value):
            self._add("path_traversal", "path traversal components are not allowed", node)
        if _contains_url(value):
            self._add("network_url", "network URLs are not allowed", node)
        token = _first_shellish_command_token(value)
        if token is not None:
            self._add(
                "shellish_command_token",
                f"command token '{token}' is not allowed in generated tests",
                node,
            )

    def _add(self, rule_id: str, message: str, node: ast.AST) -> None:
        self.diagnostics.append(
            ModelSafetyDiagnostic(
                rule_id=rule_id,
                message=message,
                line=_line(node),
                column=_column(node),
            )
        )


def _collect_trusted_env_names(tree: ast.AST) -> frozenset[str]:
    assignments = _collect_assignments(tree)
    return frozenset(
        name
        for name, values in assignments.items()
        if values and all(_is_pbgen_env_lookup(value, frozenset()) for value in values)
    )


def _collect_trusted_command_names(tree: ast.AST, env_names: frozenset[str]) -> frozenset[str]:
    assignments = _collect_assignments(tree)
    trusted: set[str] = set()
    for name, values in assignments.items():
        if values and all(_is_safe_subprocess_command(value, env_names, frozenset()) for value in values):
            trusted.add(name)
    return frozenset(trusted)


def _collect_assignments(tree: ast.AST) -> dict[str, list[ast.AST]]:
    assignments: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _target_names(target):
                    assignments.setdefault(name, []).append(node.value)
        elif isinstance(node, ast.AnnAssign):
            for name in _target_names(node.target):
                assignments.setdefault(name, []).append(node.value or ast.Constant(value=None))
        elif isinstance(node, ast.AugAssign):
            for name in _target_names(node.target):
                assignments.setdefault(name, []).append(node.value)
    return assignments


def _target_names(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_target_names(element))
        return tuple(names)
    return ()


def _is_safe_subprocess_command(
    node: ast.AST,
    env_names: frozenset[str],
    command_names: frozenset[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in command_names
    if isinstance(node, (ast.List, ast.Tuple)):
        return bool(node.elts) and _is_pbgen_executable_expr(node.elts[0], env_names)
    return False


def _is_pbgen_executable_expr(node: ast.AST, env_names: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in env_names
    return _is_pbgen_env_lookup(node, env_names)


def _is_pbgen_env_lookup(node: ast.AST, env_names: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in env_names
    if isinstance(node, ast.Subscript):
        path = _attribute_path(node.value)
        return path == ("os", "environ") and _string_constant(node.slice) == "PBGEN_EXECUTABLE"
    if isinstance(node, ast.Call):
        path = _attribute_path(node.func)
        if path == ("os", "getenv"):
            return bool(node.args) and _string_constant(node.args[0]) == "PBGEN_EXECUTABLE"
        if path == ("os", "environ", "get"):
            return bool(node.args) and _string_constant(node.args[0]) == "PBGEN_EXECUTABLE"
    return False


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        parent = _attribute_path(node.value)
        if parent is None:
            return None
        return (*parent, node.attr)
    return None


def _method_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _open_mode(node: ast.Call, *, is_path_open: bool) -> str | None:
    positional_index = 0 if is_path_open else 1
    if len(node.args) > positional_index:
        return _string_constant(node.args[positional_index])
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return _string_constant(keyword.value)
    return None


def _string_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _constant_bool(node: ast.AST) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _contains_absolute_local_path(value: str) -> bool:
    return any(_is_absolute_local_path_token(token) for token in _tokens_for_path_checks(value))


def _is_absolute_local_path_token(token: str) -> bool:
    stripped = token.strip(TOKEN_STRIP_CHARS)
    if not stripped or URL_RE.match(stripped):
        return False
    return stripped.startswith("/") or WINDOWS_ABSOLUTE_PATH_RE.match(stripped) is not None


def _contains_path_traversal(value: str) -> bool:
    for token in _tokens_for_path_checks(value):
        parts = [part for part in re.split(r"[\\/]+", token.strip(TOKEN_STRIP_CHARS)) if part]
        if ".." in parts:
            return True
    return False


def _contains_url(value: str) -> bool:
    return any(URL_RE.match(token.strip(TOKEN_STRIP_CHARS)) is not None for token in value.split())


def _first_shellish_command_token(value: str) -> str | None:
    for token in _shell_tokens(value):
        normalized = token.strip(TOKEN_STRIP_CHARS).lower()
        if normalized in SHELLISH_COMMAND_TOKENS:
            return normalized
    return None


def _tokens_for_path_checks(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    tokens = _shell_tokens(value)
    return tokens or (value,)


def _shell_tokens(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    try:
        return tuple(shlex.split(value))
    except ValueError:
        return tuple(value.split())


def _line(node: ast.AST) -> int:
    value = getattr(node, "lineno", 0)
    return value if isinstance(value, int) else 0


def _column(node: ast.AST) -> int:
    value = getattr(node, "col_offset", 0)
    return value if isinstance(value, int) else 0
