"""Deterministic redundancy clustering for generated tests."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from pbgen.eval.executable_runner import load_canonical_suites
from pbgen.logging.event_log import EventLogger
from pbgen.schemas import ExecutableTestCase, ExpectedOutput, RedundancyItem, RedundancyReport
from pbgen.serialization import write_data


class RedundancyAnalyzer:
    """Cluster tests by command signature and output assertion shape."""

    def analyze(
        self,
        task_id: str,
        tests_path: Path,
        report_path: Path,
        event_log_path: Path,
        iteration: int | None = None,
    ) -> RedundancyReport:
        signatures = _canonical_signatures(tests_path) or _pytest_signatures(tests_path)

        clusters: dict[str, list[str]] = defaultdict(list)
        for test_name, signature in signatures.items():
            cluster_id = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:10]
            clusters[cluster_id].append(test_name)

        items: list[RedundancyItem] = []
        for cluster_id, tests in sorted(clusters.items()):
            cluster_size = len(tests)
            penalty = max(0.0, (cluster_size - 1) / cluster_size) if cluster_size else 0.0
            for test_name in sorted(tests):
                items.append(
                    RedundancyItem(
                        test_id=test_name,
                        cluster_id=cluster_id,
                        cluster_size=cluster_size,
                        redundancy_penalty=penalty,
                        recommended_action="downweight_or_qc_review" if cluster_size > 1 else "keep",
                    )
                )

        redundancy_score = sum(item.redundancy_penalty for item in items) / len(items) if items else 0.0
        report = RedundancyReport(task_id=task_id, items=items, redundancy_score=redundancy_score)
        write_data(report_path, report.model_dump(mode="json"))
        EventLogger(event_log_path).append(
            task_id=task_id,
            stage="quality",
            event_type="redundancy_cluster_assigned",
            iteration=iteration,
            metrics={"clusters": len(clusters), "redundancy_score": redundancy_score},
        )
        return report


def _canonical_signatures(tests_path: Path) -> dict[str, str]:
    signatures: dict[str, str] = {}
    for suite in load_canonical_suites(tests_path):
        for case in suite.cases:
            signatures[case.test_id] = _case_signature(case)
    return signatures


def _pytest_signatures(tests_path: Path) -> dict[str, str]:
    signatures: dict[str, str] = {}
    for path in sorted(tests_path.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                signatures[node.name] = _pytest_signature(node)
    return signatures


def _case_signature(case: ExecutableTestCase) -> str:
    payload = {
        "command": [_text_shape(arg) for arg in case.args],
        "stdin_shape": _text_shape(case.stdin),
        "env_keys": sorted(case.env),
        "fixture_paths": sorted(case.fixture_files),
        "expected_exit_code": case.expected_exit_code,
        "expected_stdout": _expected_output_signature(case.expected_stdout),
        "expected_stderr": _expected_output_signature(case.expected_stderr),
        "behavior_category": case.behavior_category or "unknown",
        "source_path": case.source_path or "",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _expected_output_signature(expected: ExpectedOutput) -> dict[str, object]:
    return {
        "exact": _text_shape(expected.exact or ""),
        "contains": [_text_shape(value) for value in expected.contains],
        "regex": sorted(expected.regex),
        "assertion_shape": _assertion_shape(expected),
    }


def _assertion_shape(expected: ExpectedOutput) -> list[str]:
    shape: list[str] = []
    if expected.exact is not None:
        shape.append("exact")
    if expected.contains:
        shape.append("contains")
    if expected.regex:
        shape.append("regex")
    return shape


def _text_shape(text: str) -> str:
    normalized = re.sub(r"\d+", "<num>", text.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _pytest_signature(function: ast.FunctionDef) -> str:
    command_tokens: list[str] = []
    assertion_shapes: list[str] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_cmd":
            if node.args and isinstance(node.args[0], ast.List):
                for item in node.args[0].elts:
                    if isinstance(item, ast.Constant):
                        command_tokens.append(str(item.value))
        if isinstance(node, ast.Assert):
            text = ast.unparse(node.test)
            for literal in [part for part in text.split("'") if len(part) > 3]:
                text = text.replace(literal, "<literal>")
            assertion_shapes.append(text)
    return "|".join(command_tokens[:2]) + "::" + "::".join(assertion_shapes)
