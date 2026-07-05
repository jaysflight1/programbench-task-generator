from pbgen.schemas import (
    CandidateEvaluationReport,
    ExecutableTestCase,
    ExecutableTestSuite,
    ExpectedOutput,
    ProgramBenchEvaluationMetrics,
    ProgramBenchModelPerformanceReport,
    ReleasedTaskPackageManifest,
    RewardShapeReport,
    SuiteQualityReport,
    TaskSpec,
)


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


def test_executable_test_suite_serializes_canonical_cases() -> None:
    suite = ExecutableTestSuite(
        task_id="demo",
        iteration=2,
        generator="local_heuristic_v1",
        renderer="pytest",
        cases=[
            ExecutableTestCase(
                test_id="test_demo_help",
                task_id="demo",
                args=["--help"],
                stdin="",
                env={"LC_ALL": "C"},
                fixture_files={"input.txt": "hello\n"},
                expected_exit_code=0,
                expected_stdout=ExpectedOutput(
                    exact=None,
                    contains=["Usage"],
                    regex=[r"Commands?:"],
                ),
                expected_stderr=ExpectedOutput(exact=""),
                timeout_seconds=10,
                behavior_category="help",
                source="docs",
                source_path="README.md",
                provenance={"line": "12"},
            )
        ],
    )

    loaded = ExecutableTestSuite.model_validate_json(suite.model_dump_json())

    assert loaded.task_id == "demo"
    assert loaded.iteration == 2
    assert loaded.cases[0].args == ["--help"]
    assert loaded.cases[0].expected_stdout.contains == ["Usage"]
    assert loaded.cases[0].fixture_files == {"input.txt": "hello\n"}


def test_release_and_candidate_reports_serialize_product_boundaries() -> None:
    manifest = ReleasedTaskPackageManifest(
        task_id="demo",
        language="python",
        build_system="script",
        solver_package="/tmp/demo/solver",
        evaluator_package="/tmp/demo/evaluator",
        hidden_tests_path="/tmp/demo/evaluator/hidden_tests",
        runtime_policy="sandboxed-local",
        accepted_test_count=3,
        package_hash="abc123",
    )
    report = CandidateEvaluationReport(
        task_id="demo",
        resolved=True,
        tests_passed=3,
        total_tests=3,
        pass_rate=1.0,
        build_success=True,
        runtime_policy="sandboxed-local",
        executable_path="/tmp/submission/program",
    )

    loaded_manifest = ReleasedTaskPackageManifest.model_validate_json(manifest.model_dump_json())
    loaded_report = CandidateEvaluationReport.model_validate_json(report.model_dump_json())

    assert loaded_manifest.accepted_test_count == 3
    assert loaded_report.resolved is True
    assert loaded_report.pass_rate == 1.0


def test_programbench_performance_metrics_serialize() -> None:
    metrics = ProgramBenchEvaluationMetrics(
        task_id="demo",
        model_name="model-a",
        attempt_id="attempt-1",
        resolved=True,
        almost_resolved=True,
        test_pass_rate=1.0,
        raw_test_pass_rate=1.0,
        tests_passed=3,
        total_tests=3,
        build_success=True,
        api_calls=12,
        cost_usd=0.5,
    )
    report = ProgramBenchModelPerformanceReport(
        model_name="model-a",
        task_count=1,
        resolved_count=1,
        almost_resolved_count=1,
        percent_resolved=1.0,
        percent_almost_resolved=1.0,
        macro_average_test_pass_rate=1.0,
        micro_average_test_pass_rate=1.0,
        build_success_rate=1.0,
        disqualified_count=0,
        disqualification_rate=0.0,
        task_metrics=[metrics],
    )

    loaded = ProgramBenchModelPerformanceReport.model_validate_json(report.model_dump_json())

    assert loaded.task_metrics[0].task_id == "demo"
    assert loaded.task_metrics[0].resolved is True
