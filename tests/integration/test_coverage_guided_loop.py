import json
from pathlib import Path

from pbgen.build.build_agent import build_gold
from pbgen.config import PBGenConfig
from pbgen.repo_discovery.checkout import init_task
from pbgen.serialization import read_data
from pbgen.testgen.behavioral_surface import discover_behavior_surface
from pbgen.testgen.controller import CoverageGuidedTestController


def test_python_coverage_guided_loop_improves_coverage(tmp_path) -> None:
    repo = Path(__file__).parents[2] / "examples" / "demo_task" / "source"
    config = PBGenConfig(
        workspace_root=tmp_path,
        coverage_target=0.99,
        min_coverage_delta_per_iteration=0.0,
    )
    init_task(task_id="mini_cov", config=config, local_path=repo)
    build_gold("mini_cov", config)
    discover_behavior_surface("mini_cov", config)
    CoverageGuidedTestController(config).run("mini_cov", iterations=2)

    reports = tmp_path / "artifacts" / "mini_cov" / "reports"
    first = read_data(reports / "coverage_report_iteration_0.json")
    second = read_data(reports / "coverage_report_iteration_1.json")
    assert first["line_coverage"] is not None
    assert second["line_coverage"] is not None
    assert second["line_coverage"] >= first["line_coverage"]
    assert first["gaps"]
    generated = tmp_path / "artifacts" / "mini_cov" / "generated_tests"
    assert list(generated.glob("test_behavior_iter_0*.py"))
    assert list(generated.glob("test_behavior_iter_1*.py"))

    qc_item_count = 0
    qc_dir = tmp_path / "artifacts" / "mini_cov" / "qc"
    for iteration in [0, 1]:
        lint_report = read_data(reports / f"lint_report_iteration_{iteration}.json")
        redundancy_report = read_data(reports / f"redundancy_report_iteration_{iteration}.json")
        qc_report = read_data(qc_dir / f"qc_queue_iteration_{iteration}.json")
        assert lint_report["task_id"] == "mini_cov"
        assert "flags" in lint_report
        assert redundancy_report["task_id"] == "mini_cov"
        assert "redundancy_score" in redundancy_report
        assert qc_report["task_id"] == "mini_cov"
        assert "items" in qc_report
        qc_item_count += len(qc_report["items"])

    events_path = tmp_path / "artifacts" / "mini_cov" / "logs" / "generation_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    lint_iterations = {
        event["iteration"]
        for event in events
        if event["event_type"] == "test_linted" and event["iteration"] is not None
    }
    assert {0, 1}.issubset(lint_iterations)

    qc_events = [event for event in events if event["event_type"] == "qc_flag_created"]
    assert qc_item_count > 0
    assert len(qc_events) == qc_item_count
    assert {event["iteration"] for event in qc_events}.issubset({0, 1})
