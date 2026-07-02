"""AST-based assertion quality linter for generated pytest files."""

from __future__ import annotations

import ast
from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.logging.event_log import EventLogger
from pbgen.schemas import AssertionLintFlag, AssertionLintReport, LintSeverity


class AssertionQualityLinter:
    """Detect weak tests that execute code without strong behavioral assertions."""

    def __init__(self, config: PBGenConfig) -> None:
        self.config = config

    def lint_path(self, task_id: str, tests_path: Path) -> AssertionLintReport:
        flags: list[AssertionLintFlag] = []
        for path in sorted(tests_path.rglob("test_*.py")):
            flags.extend(self._lint_file(path))
        return AssertionLintReport(task_id=task_id, flags=flags)

    def _lint_file(self, path: Path) -> list[AssertionLintFlag]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        flags: list[AssertionLintFlag] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                flags.extend(self._lint_function(path, node))
        return flags

    def _lint_function(self, path: Path, function: ast.FunctionDef) -> list[AssertionLintFlag]:
        asserts = [node for node in ast.walk(function) if isinstance(node, ast.Assert)]
        flags: list[AssertionLintFlag] = []
        if not asserts:
            flags.append(self._flag("no_assertions", LintSeverity.HIGH, path, function.lineno, function.name))
            return flags
        if asserts and all(_is_returncode_assert(assert_node.test) for assert_node in asserts):
            flags.append(
                self._flag(
                    "only_checks_return_code",
                    LintSeverity.HIGH,
                    path,
                    asserts[0].lineno,
                    function.name,
                )
            )
        for assert_node in asserts:
            test = assert_node.test
            if isinstance(test, ast.Constant) and test.value is True:
                flags.append(self._flag("assert_true", LintSeverity.HIGH, path, assert_node.lineno, function.name))
            if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
                flags.append(self._flag("disjunctive_assertion", LintSeverity.HIGH, path, assert_node.lineno, function.name))
            if _is_short_substring(test, self.config.assertion_min_substring_length):
                flags.append(self._flag("short_substring_assertion", LintSeverity.HIGH, path, assert_node.lineno, function.name))
            if _is_len_output_positive(test):
                flags.append(self._flag("len_output_positive", LintSeverity.MEDIUM, path, assert_node.lineno, function.name))
            if _is_error_only_substring(test):
                flags.append(self._flag("broad_error_substring", LintSeverity.MEDIUM, path, assert_node.lineno, function.name))
        for try_node in [node for node in ast.walk(function) if isinstance(node, ast.Try)]:
            for handler in try_node.handlers:
                if any(isinstance(item, ast.Pass) for item in handler.body):
                    flags.append(self._flag("try_except_swallows_failure", LintSeverity.HIGH, path, try_node.lineno, function.name))
        return flags

    def _flag(
        self,
        rule_id: str,
        severity: LintSeverity,
        path: Path,
        line: int,
        test_name: str,
    ) -> AssertionLintFlag:
        return AssertionLintFlag(
            rule_id=rule_id,
            severity=severity,
            message=rule_id.replace("_", " "),
            file_path=path,
            line=line,
            test_name=test_name,
        )


def lint_and_log(task_id: str, tests_path: Path, event_log_path: Path, config: PBGenConfig) -> AssertionLintReport:
    """Lint generated tests and emit an event."""

    report = AssertionQualityLinter(config).lint_path(task_id, tests_path)
    EventLogger(event_log_path).append(
        task_id=task_id,
        stage="quality",
        event_type="test_linted",
        metrics={"high": report.high_count, "medium": report.medium_count},
    )
    return report


def _is_returncode_assert(node: ast.AST) -> bool:
    text = ast.unparse(node)
    return "returncode" in text and all(token not in text for token in ("stdout", "stderr", "read_text"))


def _is_short_substring(node: ast.AST, minimum: int) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if not any(isinstance(op, ast.In) for op in node.ops):
        return False
    left = node.left
    return isinstance(left, ast.Constant) and isinstance(left.value, str) and 0 < len(left.value) < minimum


def _is_len_output_positive(node: ast.AST) -> bool:
    return ast.unparse(node).replace(" ", "") in {"len(result.stdout)>0", "len(output)>0"}


def _is_error_only_substring(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if not any(isinstance(op, ast.In) for op in node.ops):
        return False
    return isinstance(node.left, ast.Constant) and str(node.left.value).lower() == "error"
