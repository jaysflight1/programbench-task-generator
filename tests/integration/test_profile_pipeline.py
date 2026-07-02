from pathlib import Path

from pbgen.config import PBGenConfig
from pbgen.pipeline import run_task_profile
from pbgen.schemas import TaskProfile
from pbgen.serialization import read_data


def test_run_task_profile_writes_summary_without_real_repo_selection(tmp_path: Path) -> None:
    repo = Path(__file__).parents[2] / "examples" / "demo_task" / "source"
    profile = TaskProfile(
        task_id="profile_demo",
        local_path=repo,
        primary_binary="pbcalc",
        expected_language="python",
        iterations=1,
        coverage_target=0.5,
        benchmark_commands=[["--version"]],
        trusted_local=True,
    )

    summary = run_task_profile(profile, PBGenConfig(workspace_root=tmp_path))

    root = tmp_path / "artifacts" / "profile_demo"
    assert summary.task_id == "profile_demo"
    assert summary.generated_tests > 0
    assert summary.gold_pass_rate == 1.0
    assert (root / "RUN_SUMMARY.md").exists()
    assert (root / "reports" / "RUN_SUMMARY.json").exists()
    assert (root / "packages" / "solver").is_dir()
    assert (root / "packages" / "evaluator").is_dir()
    manifest = read_data(root / "packages" / "release_manifest.json")
    assert manifest["task_id"] == "profile_demo"
    assert manifest["accepted_test_count"] > 0
    assert manifest["runtime_policy"] == "trusted-local"
    assert manifest["package_hash"]
    assert manifest["leak_check_passed"] is True
    assert manifest["solver_includes_gold_executable"] is False
    assert manifest["solver_manifest_path"].endswith("SOLVER_MANIFEST.json")
