"""Configuration and artifact path helpers."""

from __future__ import annotations

from pathlib import Path
import re
import shlex

from pydantic import BaseModel, ConfigDict, Field

from pbgen.errors import PBGenError


TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class PBGenConfig(BaseModel):
    """Runtime configuration with conservative MVP defaults."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace_root: Path = Field(default_factory=lambda: Path.cwd())
    artifacts_dir: Path | None = None
    assertion_min_substring_length: int = 15
    dummy_max_pass_rate: float = 0.05
    determinism_runs: int = 3
    redundancy_similarity_threshold: float = 0.85
    correctness_gate_for_efficiency: float = 1.00
    efficiency_multiplier_min: float = 0.75
    efficiency_multiplier_max: float = 1.25
    coverage_enabled: bool = True
    coverage_backend: str = "python"
    coverage_target: float = 0.80
    min_coverage_delta_per_iteration: float = 0.01
    max_generation_iterations: int = 5
    agentic_candidate_budget: int = 256
    agentic_revision_rounds: int = 2
    allow_network_dependency_fetch: bool = False
    allow_custom_build_command: bool = False
    trusted_local_execution: bool = False
    execution_policy: str = "sandboxed-local"
    safe_command_allow_patterns: list[str] = Field(default_factory=list)
    safe_command_deny_patterns: list[str] = Field(default_factory=list)
    dependency_policy: str = "offline"
    build_timeout_seconds: int = 300
    probe_timeout_seconds: int = 15
    docker_image: str = "python:3.11-slim"
    max_doc_file_bytes: int = 200_000
    benchmark_trials: int = 5
    benchmark_warmups: int = 1
    generation_backend: str = "local"
    model_provider: str = "external-command"
    model_command: list[str] | None = None
    model_name: str | None = None
    model_temperature: float = 0.2
    model_timeout_seconds: int = 120
    model_max_output_chars: int = 200_000

    def model_post_init(self, __context: object) -> None:
        if self.artifacts_dir is None:
            self.artifacts_dir = self.workspace_root / "artifacts"
        if self.model_command is None:
            command = model_command_from_env()
            if command:
                self.model_command = command


class ArtifactPaths:
    """Stable filesystem layout for one generated task."""

    def __init__(self, config: PBGenConfig, task_id: str) -> None:
        validate_task_id(task_id)
        self.config = config
        self.task_id = task_id
        self.root = (config.artifacts_dir or config.workspace_root / "artifacts") / task_id
        self.repo = self.root / "repo"
        self.gold = self.root / "gold"
        self.logs = self.root / "logs"
        self.reports = self.root / "reports"
        self.generated_tests = self.root / "generated_tests"
        self.qc = self.root / "qc"
        self.packages = self.root / "packages"

    @property
    def task_spec(self) -> Path:
        return self.root / "task_spec.yaml"

    @property
    def build_artifact(self) -> Path:
        return self.root / "build_artifact.json"

    @property
    def behavior_surface(self) -> Path:
        return self.root / "behavior_surface.yaml"

    @property
    def event_log(self) -> Path:
        return self.logs / "generation_events.jsonl"

    @property
    def executable(self) -> Path:
        return self.gold / "executable" / "program"

    def ensure_base_dirs(self) -> None:
        for path in [
            self.root,
            self.gold,
            self.logs,
            self.reports,
            self.generated_tests,
            self.qc,
            self.packages,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def validate_task_id(task_id: str) -> None:
    """Reject task ids that could escape artifact directories."""

    if not TASK_ID_PATTERN.match(task_id):
        raise PBGenError(
            "Task IDs may contain only letters, numbers, '.', '_' and '-'."
        )


def model_command_from_env() -> list[str] | None:
    """Return an external model command configured through the environment."""

    import os

    raw = os.environ.get("PBGEN_MODEL_COMMAND")
    if raw is None or not raw.strip():
        return None
    return shlex.split(raw)
