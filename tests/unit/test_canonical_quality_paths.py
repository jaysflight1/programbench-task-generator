from __future__ import annotations

from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.logging.event_log import EventLogger
from pbgen.quality.dummy_runner import DummyBinaryRunner
from pbgen.quality.gold_determinism import run_gold_determinism
from pbgen.schemas import ExecutableTestCase, ExecutableTestSuite, ExpectedOutput
from pbgen.serialization import write_data


def test_quality_paths_use_canonical_test_cases(tmp_path: Path) -> None:
    executable = _write_cli(tmp_path / "program")
    tests_dir = tmp_path / "generated_tests"
    tests_dir.mkdir()
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=0,
        cases=[
            ExecutableTestCase(
                test_id="test_add",
                task_id="demo",
                args=["add", "2", "3"],
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(exact="5\n"),
                expected_stderr=ExpectedOutput(exact=""),
                source="unit",
            )
        ],
    )
    write_data(tests_dir / "test_cases_iteration_0.json", suite.model_dump(mode="json"))

    deterministic = run_gold_determinism(
        "demo",
        tests_dir,
        executable,
        tmp_path / "events.jsonl",
        PBGenConfig(workspace_root=tmp_path, determinism_runs=2),
    )
    dummy_rate = DummyBinaryRunner().run(
        "demo",
        tests_dir,
        tmp_path / "dummies",
        tmp_path / "events.jsonl",
    )

    assert deterministic == 1.0
    assert dummy_rate == 0.0
    events = EventLogger(tmp_path / "events.jsonl").read_events()
    determinism_event = next(event for event in events if event.event_type == "determinism_check_run")
    dummy_event = next(event for event in events if event.event_type == "dummy_check_run")
    assert determinism_event.metrics["per_test_deterministic"] == {"test_add": True}
    assert dummy_event.metrics["per_test_dummy_passes"] == {"test_add": False}


def _write_cli(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import sys


if sys.argv[1:] == ["add", "2", "3"]:
    print("5")
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path
