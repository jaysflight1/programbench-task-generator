from __future__ import annotations

from pathlib import Path
import json
import shutil

import pytest

from pbgen.build.build_agent import build_gold
from pbgen.config import PBGenConfig
from pbgen.errors import BuildError
from pbgen.languages import CLanguageAdapter, PythonLanguageAdapter, UnsupportedLanguageAdapter
from pbgen.languages.adapters import LanguageAdapterRegistry
from pbgen.repo_discovery.checkout import init_task
from pbgen.schemas import TaskSpec
from pbgen.serialization import read_data, write_data


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_registry_selects_python_adapter_for_python_package() -> None:
    registry = LanguageAdapterRegistry()

    adapter = registry.select(language="python", build_system="python-package")
    report = registry.capability_report(language="python", build_system="python-package")

    assert isinstance(adapter, PythonLanguageAdapter)
    assert report.supported is True
    assert report.build_supported is True
    assert report.coverage_supported is True
    assert report.package_runtime == "python3"


def test_registry_selects_c_adapter_for_make() -> None:
    registry = LanguageAdapterRegistry()

    adapter = registry.select(language="c", build_system="make")
    report = registry.capability_report(language="c", build_system="make")

    assert isinstance(adapter, CLanguageAdapter)
    assert report.supported is True
    assert report.build_supported is True
    assert report.coverage_supported is False
    assert report.package_runtime == "native executable"


def test_registry_reports_unsupported_language() -> None:
    registry = LanguageAdapterRegistry()

    adapter = registry.select(language="ruby", build_system="bundle")
    report = registry.capability_report(language="ruby", build_system="bundle")

    assert isinstance(adapter, UnsupportedLanguageAdapter)
    assert report.supported is False
    assert report.build_supported is False
    assert report.reason


def test_explicit_unsupported_build_system_fails_with_diagnostic(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="pkg", config=config, local_path=FIXTURES / "python_package")

    with pytest.raises(BuildError, match="Build system is not supported yet: bundle"):
        build_gold("pkg", config, build_system="bundle")

    report = json.loads(
        (tmp_path / "artifacts" / "pkg" / "reports" / "language_capabilities.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["adapter_name"] == "python"
    assert report["supported"] is False
    assert report["build_supported"] is False
    assert report["build_system"] == "bundle"


def test_python_adapter_preserves_existing_package_build(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="pkg", config=config, local_path=FIXTURES / "python_package")

    artifact = build_gold("pkg", config)

    assert "packcalc" in artifact.executable_paths
    report = read_data(tmp_path / "artifacts" / "pkg" / "reports" / "language_capabilities.json")
    assert report["adapter_name"] == "python"
    assert report["build_supported"] is True


def test_c_adapter_preserves_existing_make_build(tmp_path: Path) -> None:
    if shutil.which("make") is None or shutil.which("cc") is None:
        pytest.skip("make and cc are required for this fixture")
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="make-c", config=config, local_path=FIXTURES / "make_c")

    artifact = build_gold("make-c", config)

    assert "calc" in artifact.executable_paths
    report = read_data(
        tmp_path / "artifacts" / "make-c" / "reports" / "language_capabilities.json"
    )
    assert report["adapter_name"] == "c-cpp"
    assert report["build_supported"] is True


def test_unsupported_discovered_language_can_fall_back_to_existing_candidates(
    tmp_path: Path,
) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="pkg", config=config, local_path=FIXTURES / "python_package")
    spec_path = tmp_path / "artifacts" / "pkg" / "task_spec.yaml"
    spec = TaskSpec.model_validate(read_data(spec_path)).model_copy(
        update={"language": "unknown-new-language", "build_system": None}
    )
    write_data(spec_path, spec.model_dump(mode="json"))

    artifact = build_gold("pkg", config)

    assert "packcalc" in artifact.executable_paths
    report = read_data(tmp_path / "artifacts" / "pkg" / "reports" / "language_capabilities.json")
    assert report["adapter_name"] == "unsupported"
    assert report["build_supported"] is False
