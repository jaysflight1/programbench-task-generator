"""Prompt payload models for future model-backed test generation."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from pbgen.schemas import BehaviorSurface, CoverageGap


class TestGenerationPrompt(BaseModel):
    """Structured prompt payload passed to a generation backend."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    behavior_surface: BehaviorSurface
    coverage_gaps: list[CoverageGap] = []
    existing_test_names: list[str] = []
    iteration: int = 0
    executable_path: Path | None = None
