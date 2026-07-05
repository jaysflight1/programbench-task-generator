"""Typed schemas exchanged by ProgramBench generator pipeline stages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PBModel(BaseModel):
    """Base model with path-friendly JSON serialization."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={Path: str},
        use_enum_values=True,
    )


class BuildCandidate(PBModel):
    build_system: str
    language: str | None = None
    confidence: float
    commands: list[list[str]] = Field(default_factory=list)
    output_hints: list[str] = Field(default_factory=list)
    dependency_manifests: list[str] = Field(default_factory=list)
    entrypoint_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EntrypointCandidate(PBModel):
    name: str
    path: str
    invocation_kind: str
    confidence: float
    reason: str
    help_probe_supported: bool = True
    language: str | None = None
    module: str | None = None
    callable_name: str | None = None


class CommandExample(PBModel):
    args: list[str]
    source: str
    source_path: str | None = None
    category: str | None = None
    expected_exit_code: int | None = None
    expected_stdout: str | None = None
    expected_stderr: str | None = None


class RecordedCommandBehavior(PBModel):
    args: list[str]
    exit_code: int
    stdout: str
    stderr: str
    source: str
    source_path: str | None = None


class ExpectedOutput(PBModel):
    """Language-independent output assertion contract for one stream."""

    exact: str | None = None
    contains: list[str] = Field(default_factory=list)
    regex: list[str] = Field(default_factory=list)


class ExecutionEnvironment(PBModel):
    """Runtime environment requested by one executable test case."""

    env: dict[str, str] = Field(default_factory=dict)
    cwd: Path | None = None
    timeout_seconds: int = 20


class ExecutableTestCase(PBModel):
    """Canonical executable-level behavioral test independent of renderer language."""

    test_id: str
    task_id: str
    args: list[str] = Field(default_factory=list)
    stdin: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    fixture_files: dict[str, str] = Field(default_factory=dict)
    expected_exit_code: int
    expected_stdout: ExpectedOutput = Field(default_factory=ExpectedOutput)
    expected_stderr: ExpectedOutput = Field(default_factory=ExpectedOutput)
    timeout_seconds: int = 20
    behavior_category: str | None = None
    source: str
    source_path: str | None = None
    provenance: dict[str, str] = Field(default_factory=dict)


class ExecutableTestSuite(PBModel):
    """Canonical generated test suite for one generation iteration."""

    task_id: str
    iteration: int
    cases: list[ExecutableTestCase] = Field(default_factory=list)
    generator: str | None = None
    renderer: str | None = None


class TestArtifactRecord(PBModel):
    """Record linking canonical test cases to rendered artifacts."""

    task_id: str
    iteration: int
    canonical_suite_path: Path
    rendered_paths: list[Path] = Field(default_factory=list)
    case_count: int
    renderer: str


class LanguageCapabilityReport(PBModel):
    """Structured capability report for a language/build adapter selection."""

    language: str | None = None
    build_system: str | None = None
    adapter_name: str
    supported: bool
    build_supported: bool
    coverage_supported: bool
    behavior_probe_supported: bool
    test_rendering_supported: bool
    package_runtime: str | None = None
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class TaskProfile(PBModel):
    """Repository-specific settings for real ProgramBench-scale runs."""

    task_id: str | None = None
    local_path: Path | None = None
    repo_url: str | None = None
    commit_sha: str | None = None
    build_command: list[str] | None = None
    primary_binary: str | None = None
    safe_command_allow_patterns: list[str] = Field(default_factory=list)
    safe_command_deny_patterns: list[str] = Field(default_factory=list)
    runtime_env: dict[str, str] = Field(default_factory=dict)
    fixture_files: dict[str, str] = Field(default_factory=dict)
    benchmark_commands: list[list[str]] = Field(default_factory=list)
    coverage_backend: str | None = None
    expected_language: str | None = None
    dependency_policy: str = "offline"
    trusted_local: bool = False
    execution_policy: str | None = None
    iterations: int = 3
    coverage_target: float | None = None
    min_coverage_delta: float | None = None
    generation_backend: str | None = None
    docker_image: str | None = None
    model_provider: str | None = None
    model_command: list[str] | None = None
    model_name: str | None = None
    model_temperature: float | None = None
    model_timeout_seconds: int | None = None
    model_max_output_chars: int | None = None
    model_require_structured_cases: bool | None = None
    notes: list[str] = Field(default_factory=list)


class CommandProbe(PBModel):
    """Planned or observed executable command probe."""

    args: list[str]
    category: str
    source: str
    safe: bool = True
    reason: str | None = None


class TaskSpec(PBModel):
    task_id: str
    repo_url: str
    commit_sha: str
    language: str | None = None
    build_system: str | None = None
    binary_names: list[str] = Field(default_factory=list)
    docs_paths: list[str] = Field(default_factory=list)
    asset_paths: list[str] = Field(default_factory=list)
    license: str | None = None
    build_candidates: list[BuildCandidate] = Field(default_factory=list)
    entrypoint_candidates: list[EntrypointCandidate] = Field(default_factory=list)
    dependency_manifests: list[str] = Field(default_factory=list)
    metadata_warnings: list[str] = Field(default_factory=list)


class BuildArtifact(PBModel):
    task_id: str
    build_success: bool
    build_script_path: Path
    executable_path: Path
    executable_hash: str
    docker_image: str | None = None
    build_log_path: Path
    runtime_dependencies: list[str] = Field(default_factory=list)
    executable_paths: dict[str, Path] = Field(default_factory=dict)
    build_attempts: list[dict[str, Any]] = Field(default_factory=list)


class BehaviorCommand(PBModel):
    command: str
    category: str
    observables: list[str] = Field(default_factory=list)
    notes: str | None = None


class BehaviorSurface(PBModel):
    task_id: str
    commands: list[BehaviorCommand] = Field(default_factory=list)
    global_flags: list[str] = Field(default_factory=list)
    subcommand_flags: dict[str, list[str]] = Field(default_factory=dict)
    stdin_supported: bool = False
    file_inputs: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    error_cases: list[str] = Field(default_factory=list)
    command_examples: list[CommandExample] = Field(default_factory=list)
    recorded_behaviors: list[RecordedCommandBehavior] = Field(default_factory=list)


class CoverageGap(PBModel):
    file_path: str
    function_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    reason: str
    priority: float


class CoverageReport(PBModel):
    task_id: str
    iteration: int
    coverage_backend: str | None = None
    coverage_available: bool = True
    coverage_unavailable_reason: str | None = None
    line_coverage: float | None = None
    branch_coverage: float | None = None
    function_coverage: float | None = None
    uncovered_files: list[str] = Field(default_factory=list)
    uncovered_functions: list[str] = Field(default_factory=list)
    uncovered_line_ranges: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[CoverageGap] = Field(default_factory=list)


class GeneratedTest(PBModel):
    test_id: str
    task_id: str
    file_path: Path
    source: str
    prompt_version: str | None = None
    command_signature: str | None = None
    behavior_category: str | None = None
    coverage_delta: float | None = None
    assertion_lint_flags: list[str] = Field(default_factory=list)
    gold_passes: bool = False
    dummy_passes: bool = False
    deterministic: bool = False
    redundancy_cluster_id: str | None = None
    qc_flags: list[str] = Field(default_factory=list)


class SuiteQualityReport(PBModel):
    task_id: str
    num_tests: int
    gold_pass_rate: float
    dummy_pass_rate: float
    deterministic_pass_rate: float
    line_coverage: float | None = None
    assertion_strength_score: float | None = None
    high_lint_count: int = 0
    medium_lint_count: int = 0
    redundancy_score: float | None = None
    qc_queue_size: int = 0


class RewardShapeReport(PBModel):
    task_id: str
    correctness_gate_passed: bool
    correctness_score: float
    assertion_strength_score: float
    coverage_score: float | None = None
    redundancy_penalty: float | None = None
    determinism_score: float
    dummy_rejection_score: float
    efficiency_multiplier: float | None = None
    final_score: float
    notes: list[str] = Field(default_factory=list)


class EfficiencyResult(PBModel):
    task_id: str
    eligible: bool
    reason: str | None = None
    benchmark_command_count: int = 0
    benchmark_command_sources: list[str] = Field(default_factory=list)
    reference_median_runtime_ms: float | None = None
    candidate_median_runtime_ms: float | None = None
    runtime_ratio: float | None = None
    efficiency_multiplier: float | None = None


class PerTestOutcome(PBModel):
    """Structured outcome for one pytest node."""

    test_id: str
    nodeid: str
    file_path: Path | None = None
    outcome: str
    duration_ms: float | None = None
    stdout: str = ""
    stderr: str = ""
    failure_message: str | None = None
    executable_path: Path | None = None


class TestRunResult(PBModel):
    task_id: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    exit_status: int
    stdout: str
    stderr: str
    outcomes: list[PerTestOutcome] = Field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed_tests / self.total_tests if self.total_tests else 0.0


class GoldDeterminismReport(PBModel):
    """Per-test determinism result for repeated gold runs."""

    task_id: str
    deterministic_pass_rate: float
    runs: int
    pass_rates: list[float] = Field(default_factory=list)
    per_test_deterministic: dict[str, bool] = Field(default_factory=dict)


class DummyRunReport(PBModel):
    """Per-test dummy-binary rejection result."""

    task_id: str
    dummy_pass_rate: float
    dummy_pass_rates: dict[str, float] = Field(default_factory=dict)
    per_test_dummy_passes: dict[str, bool] = Field(default_factory=dict)


class MutationLiteReport(PBModel):
    """Mutation-lite rejection result for synthetic wrong executables."""

    task_id: str
    mutation_count: int
    mutation_survival_rate: float
    mutation_pass_rates: dict[str, float] = Field(default_factory=dict)
    per_test_mutation_survived: dict[str, bool] = Field(default_factory=dict)


class HardGateRejectedTest(PBModel):
    """One test rejected by hard quality gates."""

    test_id: str
    reasons: list[str] = Field(default_factory=list)


class HardGateReport(PBModel):
    """Hard-filter report for accepted and rejected generated tests."""

    task_id: str
    iteration: int | None = None
    suite_passed: bool
    accepted_test_count: int
    rejected_test_count: int
    rejected_tests: list[HardGateRejectedTest] = Field(default_factory=list)
    high_lint_rejected: bool = True
    gold_deterministic_required: bool = True
    dummy_rejection_required: bool = True
    canonical_filter_applied: bool = False


class TestCaseRecord(PBModel):
    """Generated executable-level behavioral test metadata."""

    test_id: str
    file_path: Path
    args: list[str]
    expected_exit_code: int
    expected_stdout: str
    expected_stderr: str
    source: str
    iteration: int
    behavior_category: str | None = None


class LintSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"


class AssertionLintFlag(PBModel):
    rule_id: str
    severity: LintSeverity
    message: str
    file_path: Path
    line: int | None = None
    test_name: str | None = None


class AssertionLintReport(PBModel):
    task_id: str
    flags: list[AssertionLintFlag] = Field(default_factory=list)

    @property
    def high_count(self) -> int:
        return sum(1 for flag in self.flags if flag.severity == LintSeverity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for flag in self.flags if flag.severity == LintSeverity.MEDIUM)


class RedundancyItem(PBModel):
    test_id: str
    cluster_id: str
    cluster_size: int
    coverage_delta: float | None = None
    redundancy_penalty: float
    recommended_action: str


class RedundancyReport(PBModel):
    task_id: str
    items: list[RedundancyItem] = Field(default_factory=list)
    redundancy_score: float


class QCItem(PBModel):
    test_id: str
    queue: str
    reason: str
    severity: str
    file_path: Path | None = None
    recommendation: str | None = None
    iteration: int | None = None


class QCQueueReport(PBModel):
    task_id: str
    items: list[QCItem] = Field(default_factory=list)
    summary: dict[str, object] = Field(default_factory=dict)


class RunSummaryReport(PBModel):
    """CEO-readable summary inputs for one completed task run."""

    task_id: str
    repo_url: str
    commit_sha: str
    language: str | None = None
    build_system: str | None = None
    generated_tests: int
    gold_pass_rate: float
    dummy_pass_rate: float
    deterministic_pass_rate: float
    line_coverage: float | None = None
    redundancy_score: float | None = None
    final_score: float
    qc_queue_size: int
    solver_package: Path | None = None
    evaluator_package: Path | None = None
    limitations: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class BatchRunReport(PBModel):
    """Summary for a manifest-driven multi-repository run."""

    batch_id: str
    tasks: list[RunSummaryReport] = Field(default_factory=list)
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    notes: list[str] = Field(default_factory=list)


class ReleasedTaskPackageManifest(PBModel):
    """Manifest for a solver/evaluator package released from constructed artifacts."""

    task_id: str
    language: str | None = None
    build_system: str | None = None
    solver_package: Path
    evaluator_package: Path
    hidden_tests_path: Path
    runtime_policy: str
    accepted_test_count: int
    package_hash: str
    solver_manifest_path: Path | None = None
    evaluator_manifest_path: Path | None = None
    leak_check_passed: bool | None = None
    solver_includes_gold_executable: bool = False
    solver_file_count: int | None = None
    evaluator_file_count: int | None = None
    excluded_patterns: list[str] = Field(default_factory=list)


class CandidateSubmission(PBModel):
    """Candidate submission input accepted by the evaluation product."""

    package_path: Path | None = None
    submission_source: Path | None = None
    build_script: Path | None = None
    executable_path: Path | None = None
    output_dir: Path | None = None
    model_name: str | None = None
    attempt_id: str | None = None
    api_calls: int | None = None
    cost_usd: float | None = None
    cheating_flagged: bool = False
    disqualification_reason: str | None = None


class ProgramBenchEvaluationMetrics(PBModel):
    """ProgramBench-facing model performance metrics for one task instance."""

    task_id: str
    model_name: str | None = None
    attempt_id: str | None = None
    resolved: bool
    almost_resolved: bool
    test_pass_rate: float
    raw_test_pass_rate: float
    tests_passed: int
    total_tests: int
    build_success: bool
    cheating_flagged: bool = False
    disqualified: bool = False
    disqualification_reason: str | None = None
    api_calls: int | None = None
    cost_usd: float | None = None


class ProgramBenchModelPerformanceReport(PBModel):
    """Aggregate ProgramBench-style performance report across task instances."""

    model_name: str | None = None
    task_count: int
    resolved_count: int
    almost_resolved_count: int
    percent_resolved: float
    percent_almost_resolved: float
    macro_average_test_pass_rate: float
    micro_average_test_pass_rate: float
    build_success_rate: float
    disqualified_count: int
    disqualification_rate: float
    total_api_calls: int | None = None
    average_api_calls_per_task: float | None = None
    total_cost_usd: float | None = None
    average_cost_usd_per_task: float | None = None
    task_metrics: list[ProgramBenchEvaluationMetrics] = Field(default_factory=list)


class CandidateEvaluationReport(PBModel):
    """ProgramBench-facing result for one candidate evaluation."""

    task_id: str
    resolved: bool
    tests_passed: int
    total_tests: int
    pass_rate: float
    build_success: bool
    runtime_policy: str
    executable_path: Path | None = None
    build_log_path: Path | None = None
    outcomes: list[PerTestOutcome] = Field(default_factory=list)
    reason: str | None = None
    model_name: str | None = None
    attempt_id: str | None = None
    api_calls: int | None = None
    cost_usd: float | None = None
    cheating_flagged: bool = False
    disqualification_reason: str | None = None
    programbench_metrics: ProgramBenchEvaluationMetrics | None = None


class NoNetworkValidationReport(PBModel):
    """Outcome of evaluator validation under docker-no-network policy."""

    task_id: str
    status: str
    runtime_policy: str
    validated: bool
    network_disabled: bool = True
    tests_passed: int = 0
    total_tests: int = 0
    pass_rate: float = 0.0
    build_success: bool = False
    reason: str | None = None
    candidate_report_path: Path | None = None


class GenerationEvent(PBModel):
    event_id: str
    task_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: str
    event_type: str
    iteration: int | None = None
    actor: str = "system"
    model: str | None = None
    prompt_version: str | None = None
    input_hashes: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    qc_flags: list[str] = Field(default_factory=list)
