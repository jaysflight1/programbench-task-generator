"""Small JSON/YAML-compatible serialization helpers.

The environment used for evaluation may not have PyYAML installed yet. JSON is
valid YAML, so the framework writes deterministic indented JSON to `.yaml`
paths and can read it without optional dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


def write_data(path: Path, data: dict[str, Any]) -> None:
    """Write deterministic JSON content, suitable for `.json` or `.yaml` paths."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_data(path: Path) -> dict[str, Any]:
    """Read deterministic JSON/YAML-compatible content."""

    text = path.read_text(encoding="utf-8")
    try:
        return cast(dict[str, Any], json.loads(text))
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-untyped]
        except ModuleNotFoundError as exc:
            raise ValueError(f"{path} is not JSON and PyYAML is unavailable.") from exc
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must contain a mapping.")
        return cast(dict[str, Any], loaded)
