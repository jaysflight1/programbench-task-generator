from __future__ import annotations

from pathlib import Path

from pbgen.repo_discovery.metadata import analyze_repository, detect_binaries


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_python_package_analysis_ranks_project_script() -> None:
    analysis = analyze_repository(FIXTURES / "python_package")

    assert analysis.primary_language == "python"
    assert analysis.primary_build_system == "python-package"
    assert analysis.dependency_manifest_paths == ["pyproject.toml"]
    assert list(analysis.docs_paths) == ["README.md"]
    assert analysis.build_candidates[0].build_system == "python-package"
    assert analysis.entrypoint_candidates[0].name == "packcalc"
    assert analysis.entrypoint_candidates[0].path == "packcalc/cli.py"
    assert analysis.entrypoint_candidates[0].invocation_kind == "python-entrypoint"
    assert detect_binaries(FIXTURES / "python_package")[0] == "packcalc/cli.py"


def test_make_analysis_reports_manifest_and_output_hint() -> None:
    analysis = analyze_repository(FIXTURES / "make_c")

    assert analysis.primary_language == "c"
    assert analysis.primary_build_system == "make"
    assert analysis.dependency_manifest_paths == ["Makefile"]
    assert analysis.build_candidates[0].commands == (("make",),)
    assert analysis.build_candidates[0].output_hints == ("calc",)
    assert "no executable entrypoint detected" in analysis.metadata_warnings

