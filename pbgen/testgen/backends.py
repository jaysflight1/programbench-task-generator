"""Factory helpers for test-generation backends."""

from __future__ import annotations

from pbgen.config import PBGenConfig
from pbgen.errors import TestGenerationError
from pbgen.testgen.model_backend import ModelClient, ModelTestGenerationBackend
from pbgen.testgen.test_writer import LocalHeuristicTestGenerationBackend, TestGenerationBackend


def create_test_generation_backend(
    config: PBGenConfig,
    *,
    model_client: ModelClient | None = None,
) -> TestGenerationBackend:
    """Create the configured test-generation backend."""

    backend = config.generation_backend.strip().lower()
    if backend in {"local", "heuristic", "local-heuristic"}:
        return LocalHeuristicTestGenerationBackend()
    if backend == "model":
        return ModelTestGenerationBackend(config, client=model_client)
    raise TestGenerationError(
        f"Unsupported generation backend {config.generation_backend!r}. "
        "Supported backends: local, model."
    )
