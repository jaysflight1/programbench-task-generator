"""Candidate-evaluation product workflow."""

from __future__ import annotations

from pathlib import Path

from pbgen.config import ArtifactPaths, PBGenConfig
from pbgen.errors import PBGenError
from pbgen.eval.submission_runner import run_generated_suite
from pbgen.schemas import CandidateEvaluationReport, CandidateSubmission
from pbgen.serialization import write_data


def evaluate_executable_candidate(
    task_id: str,
    config: PBGenConfig,
    executable_path: Path,
) -> CandidateEvaluationReport:
    """Evaluate a legacy executable candidate against generated hidden tests."""

    paths = ArtifactPaths(config, task_id)
    result = run_generated_suite(task_id, paths.generated_tests, executable_path)
    report = CandidateEvaluationReport(
        task_id=task_id,
        resolved=result.total_tests > 0 and result.failed_tests == 0,
        tests_passed=result.passed_tests,
        total_tests=result.total_tests,
        pass_rate=result.pass_rate,
        build_success=True,
        runtime_policy=config.execution_policy,
        executable_path=executable_path,
        outcomes=result.outcomes,
    )
    write_data(paths.reports / "candidate_evaluation_report.json", report.model_dump(mode="json"))
    return report


def evaluate_source_submission(
    submission: CandidateSubmission,
    config: PBGenConfig,
) -> CandidateEvaluationReport:
    """Evaluate a source submission package.

    Source-tree build execution is intentionally implemented in the next phase. This
    product entrypoint exists now so the public workflow can stabilize before the
    untrusted build runner is introduced.
    """

    del config
    package_text = f" for package {submission.package_path}" if submission.package_path else ""
    raise PBGenError(
        "Source submission evaluation is not implemented yet"
        f"{package_text}; use benchmark-solution for executable compatibility until the "
        "source-submission evaluator phase lands."
    )
