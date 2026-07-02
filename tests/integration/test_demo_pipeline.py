from pathlib import Path

from pbgen.build.build_agent import build_gold
from pbgen.cleanroom.task_packager import package_cleanroom
from pbgen.cli import evaluate_suite
from pbgen.config import PBGenConfig
from pbgen.repo_discovery.checkout import init_task
from pbgen.testgen.behavioral_surface import discover_behavior_surface
from pbgen.testgen.controller import CoverageGuidedTestController


def test_demo_pipeline_runs_end_to_end(tmp_path) -> None:
    repo = Path(__file__).parents[2] / "examples" / "demo_task" / "source"
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="mini", config=config, local_path=repo)
    build_gold("mini", config)
    discover_behavior_surface("mini", config)
    CoverageGuidedTestController(config).run("mini", iterations=1)
    suite, reward, _qc = evaluate_suite("mini", config)
    package_cleanroom("mini", config)
    assert suite.gold_pass_rate == 1.0
    assert reward.correctness_gate_passed
    assert (tmp_path / "artifacts" / "mini" / "packages" / "solver" / "TASK.md").exists()
