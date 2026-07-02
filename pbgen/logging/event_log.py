"""JSONL event log for benchmark creation provenance."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pbgen.schemas import GenerationEvent


EVENT_TYPES = {
    "repo_selected",
    "repo_cloned",
    "gold_build_started",
    "language_adapter_selected",
    "gold_build_succeeded",
    "gold_build_failed",
    "executable_hashed",
    "behavior_surface_extracted",
    "tests_harvested",
    "coverage_measured",
    "coverage_gap_identified",
    "test_generated",
    "test_linted",
    "test_repaired",
    "test_discarded",
    "dummy_check_run",
    "determinism_check_run",
    "redundancy_cluster_assigned",
    "qc_flag_created",
    "human_review_added",
    "cleanroom_packaged",
    "leak_check_run",
    "suite_finalized",
}


class EventLogger:
    """Append-only JSONL event logger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        task_id: str,
        stage: str,
        event_type: str,
        iteration: int | None = None,
        actor: str = "system",
        model: str | None = None,
        prompt_version: str | None = None,
        input_hashes: dict[str, str] | None = None,
        output_hashes: dict[str, str] | None = None,
        metrics: dict[str, object] | None = None,
        qc_flags: list[str] | None = None,
    ) -> GenerationEvent:
        """Append one benchmark-creation event and return it."""

        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")
        event = GenerationEvent(
            event_id=str(uuid4()),
            task_id=task_id,
            stage=stage,
            event_type=event_type,
            iteration=iteration,
            actor=actor,
            model=model,
            prompt_version=prompt_version,
            input_hashes=input_hashes or {},
            output_hashes=output_hashes or {},
            metrics=metrics or {},
            qc_flags=qc_flags or [],
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
        return event

    def read_events(self) -> list[GenerationEvent]:
        """Read all events from the JSONL log."""

        if not self.path.exists():
            return []
        events: list[GenerationEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(GenerationEvent.model_validate(json.loads(line)))
        return events
