from pbgen.schemas import RewardShapeReport, SuiteQualityReport, TaskSpec


def test_task_spec_serializes_to_json() -> None:
    spec = TaskSpec(
        task_id="demo",
        repo_url="/tmp/demo",
        commit_sha="local",
        language="python",
        build_system="script",
        binary_names=["pbcalc"],
    )
    assert TaskSpec.model_validate_json(spec.model_dump_json()).task_id == "demo"


def test_quality_and_reward_include_assertion_strength_without_economic_importance() -> None:
    suite = SuiteQualityReport(
        task_id="demo",
        num_tests=1,
        gold_pass_rate=1.0,
        dummy_pass_rate=0.0,
        deterministic_pass_rate=1.0,
        assertion_strength_score=1.0,
    )
    RewardShapeReport(
        task_id="demo",
        correctness_gate_passed=True,
        correctness_score=1.0,
        assertion_strength_score=1.0,
        determinism_score=1.0,
        dummy_rejection_score=1.0,
        final_score=1.0,
    )
    assert suite.assertion_strength_score == 1.0
    assert "economic_importance_score" not in RewardShapeReport.model_fields
