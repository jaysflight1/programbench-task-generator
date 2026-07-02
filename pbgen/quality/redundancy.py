"""Deterministic redundancy clustering for generated pytest tests."""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from pathlib import Path

from pbgen.logging.event_log import EventLogger
from pbgen.schemas import RedundancyItem, RedundancyReport
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
        signatures: dict[str, str] = {}
        for path in sorted(tests_path.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    signatures[node.name] = _signature(node)

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


def _signature(function: ast.FunctionDef) -> str:
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
