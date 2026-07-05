"""Prompt payload models for future model-backed test generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from pbgen.schemas import BehaviorSurface, CoverageGap


class TestGenerationPrompt(BaseModel):
    """Structured prompt payload passed to a generation backend."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    task_spec: dict[str, Any] | None = None
    behavior_surface: BehaviorSurface
    coverage_gaps: list[CoverageGap] = []
    existing_test_names: list[str] = []
    previous_generation_diagnostics: list[dict[str, Any]] = []
    previous_behavior_category_counts: dict[str, int] = {}
    iteration: int = 0
    executable_path: Path | None = None
    execution_policy: str = "sandboxed-local"
    safe_command_allow_patterns: list[str] = []
    safe_command_deny_patterns: list[str] = []
    trusted_local_execution: bool = False
