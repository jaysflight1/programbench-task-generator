from pathlib import Path
from zipfile import ZipFile

from pbgen.submission_export import create_submission_archive


def test_submission_archive_uses_allowlist_and_excludes_clutter(tmp_path: Path) -> None:
    project_root = _write_project_fixture(tmp_path)
    archive_path = tmp_path / "submission.zip"

    result = create_submission_archive(project_root, archive_path)

    assert result.archive_path == archive_path.resolve()
    assert result.excluded_known_clutter_count > 0
    assert ".venv" in result.excluded_known_clutter
    assert "artifacts" in result.excluded_known_clutter
    assert "pbgen/__pycache__" in result.excluded_known_clutter

    with ZipFile(archive_path) as archive:
        names = archive.namelist()

    assert result.included_count == len(names)
    assert names == sorted(names)
    assert "README.md" in names
    assert "pyproject.toml" in names
    assert "AGENTS.md" in names
    assert "pbgen/__init__.py" in names
    assert "tests/unit/test_demo.py" in names
    assert "prompts/generate_tests_for_gap.md" in names
    assert "examples/demo_task/README.md" in names
    assert "docs/adapter_development.md" in names

    assert ".venv/lib/site.py" not in names
    assert "artifacts/run/output.json" not in names
    assert "programbench_generator.egg-info/PKG-INFO" not in names
    assert "pbgen/__pycache__/module.cpython-311.pyc" not in names
    assert "tests/unit/__pycache__/test_demo.cpython-311.pyc" not in names
    assert ".DS_Store" not in names


def test_submission_archive_uses_deterministic_zip_metadata(tmp_path: Path) -> None:
    project_root = _write_project_fixture(tmp_path)
    archive_path = tmp_path / "submission.zip"

    create_submission_archive(project_root, archive_path)

    with ZipFile(archive_path) as archive:
        infos = archive.infolist()

    assert [info.filename for info in infos] == sorted(info.filename for info in infos)
    assert {info.date_time for info in infos} == {(2020, 1, 1, 0, 0, 0)}
    assert {info.external_attr >> 16 for info in infos} == {0o644}


def test_submission_archive_excludes_symlinks(tmp_path: Path) -> None:
    project_root = _write_project_fixture(tmp_path)
    secret_file = tmp_path / "outside-secret.txt"
    secret_file.write_text("do not ship\n", encoding="utf-8")
    (project_root / "examples" / "demo_task" / "secret_link.txt").symlink_to(secret_file)

    archive_path = tmp_path / "submission.zip"
    result = create_submission_archive(project_root, archive_path)

    with ZipFile(archive_path) as archive:
        names = archive.namelist()

    assert "examples/demo_task/secret_link.txt" not in names
    assert "examples/demo_task/secret_link.txt" in result.excluded_known_clutter


def _write_project_fixture(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    (project_root / "pbgen").mkdir(parents=True)
    (project_root / "tests" / "unit").mkdir(parents=True)
    (project_root / "prompts").mkdir(parents=True)
    (project_root / "examples" / "demo_task").mkdir(parents=True)
    (project_root / "docs").mkdir(parents=True)

    (project_root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (project_root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (project_root / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    (project_root / "pbgen" / "__init__.py").write_text("", encoding="utf-8")
    (project_root / "tests" / "unit" / "test_demo.py").write_text(
        "def test_demo():\n    assert True\n",
        encoding="utf-8",
    )
    (project_root / "prompts" / "generate_tests_for_gap.md").write_text(
        "Generate tests.\n",
        encoding="utf-8",
    )
    (project_root / "examples" / "demo_task" / "README.md").write_text(
        "# Example\n",
        encoding="utf-8",
    )
    (project_root / "docs" / "adapter_development.md").write_text(
        "# Adapter Guide\n",
        encoding="utf-8",
    )

    (project_root / ".venv" / "lib").mkdir(parents=True)
    (project_root / ".venv" / "lib" / "site.py").write_text("", encoding="utf-8")
    (project_root / ".pytest_cache").mkdir()
    (project_root / ".mypy_cache").mkdir()
    (project_root / ".ruff_cache").mkdir()
    (project_root / "programbench_generator.egg-info").mkdir()
    (project_root / "programbench_generator.egg-info" / "PKG-INFO").write_text(
        "metadata\n",
        encoding="utf-8",
    )
    (project_root / "artifacts" / "run").mkdir(parents=True)
    (project_root / "artifacts" / "run" / "output.json").write_text("{}", encoding="utf-8")
    (project_root / "logs").mkdir()
    (project_root / "logs" / "run.log").write_text("local log\n", encoding="utf-8")
    (project_root / ".DS_Store").write_text("", encoding="utf-8")
    (project_root / "pbgen" / "__pycache__").mkdir()
    (project_root / "pbgen" / "__pycache__" / "module.cpython-311.pyc").write_bytes(b"pyc")
    (project_root / "tests" / "unit" / "__pycache__").mkdir()
    (project_root / "tests" / "unit" / "__pycache__" / "test_demo.cpython-311.pyc").write_bytes(
        b"pyc"
    )

    return project_root
