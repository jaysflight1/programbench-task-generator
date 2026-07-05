from __future__ import annotations

import json
from pathlib import Path

import pytest

from pbgen.errors import PBGenError
from pbgen.schemas import TaskSpec
from pbgen.serialization import read_data, write_data
from pbgen.solver.openai_solver import (
    ModelResponse,
    OpenAISolverConfig,
    collect_solver_visible_context,
    parse_solver_proposal,
    run_public_smoke,
    solve_with_openai,
)


class FakeOpenAIClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def create_response(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        reasoning_effort: str,
        max_output_tokens: int | None,
    ) -> ModelResponse:
        self.prompts.append(system_prompt + "\n" + user_prompt)
        assert model_name == "fake-model"
        assert reasoning_effort == "xhigh"
        raw = self.responses.pop(0)
        return ModelResponse(
            raw={
                "output_text": json.dumps(raw),
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
            text=json.dumps(raw),
            usage={"input_tokens": 10, "output_tokens": 20},
        )


def test_collect_solver_visible_context_excludes_evaluator_and_hidden_files(tmp_path: Path) -> None:
    solver = _write_solver_package(tmp_path)
    (solver / "docs" / "README.md").write_text("public docs\n", encoding="utf-8")
    (solver / "hidden_tests").mkdir()
    (solver / "hidden_tests" / "test_cases_iteration_0.json").write_text(
        '{"secret": true}\n',
        encoding="utf-8",
    )
    (solver / "reports").mkdir()
    (solver / "reports" / "candidate_evaluation_report.json").write_text(
        '{"hidden": true}\n',
        encoding="utf-8",
    )

    context = collect_solver_visible_context(solver)

    assert set(context.files) == {"TASK.md", "SUBMISSION.md", "task.yaml", "docs/README.md"}
    rendered_prompt = "\n".join(context.files.values())
    assert "secret" not in rendered_prompt
    assert "hidden" not in rendered_prompt
    assert [item.path for item in context.manifest] == [
        "TASK.md",
        "SUBMISSION.md",
        "task.yaml",
        "docs/README.md",
    ]


def test_collect_solver_visible_context_prioritizes_required_files_before_large_assets(
    tmp_path: Path,
) -> None:
    solver = _write_solver_package(tmp_path)
    assets = solver / "assets"
    assets.mkdir()
    for index in range(20):
        (assets / f"large_{index}.json").write_text(
            "x" * 190_000,
            encoding="utf-8",
        )

    context = collect_solver_visible_context(solver)

    assert "TASK.md" in context.files
    assert "SUBMISSION.md" in context.files
    assert "task.yaml" in context.files


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/program.py",
        "../program.py",
        ".git/config",
        "hidden_tests/test.py",
        "evaluator/report.json",
        "gold/program",
    ],
)
def test_parse_solver_proposal_rejects_restricted_paths(path: str) -> None:
    payload = {
        "files": [{"path": path, "content": "print('bad')\n"}],
        "build_script": {"path": "build.py", "content": "print('build')\n"},
        "notes": "bad",
    }

    with pytest.raises(PBGenError):
        parse_solver_proposal(json.dumps(payload))


def test_solve_with_openai_writes_candidate_and_solver_metadata(tmp_path: Path) -> None:
    solver = _write_solver_package(tmp_path)
    response = {
        "files": [
            {
                "path": "src/program.py",
                "content": (
                    "import sys\n"
                    "if '--version' in sys.argv:\n"
                    "    print('candidate 1.0')\n"
                    "else:\n"
                    "    print('Usage: candidate')\n"
                ),
            }
        ],
        "build_script": {
            "path": "build.py",
            "content": (
                "from pathlib import Path\n"
                "out = Path('out')\n"
                "out.mkdir(exist_ok=True)\n"
                "program = out / 'program'\n"
                "program.write_text("
                "'#!/usr/bin/env python3\\n'"
                "+ Path('src/program.py').read_text(encoding='utf-8'),"
                "encoding='utf-8')\n"
                "program.chmod(0o755)\n"
            ),
        },
        "notes": "simple compatible program",
    }
    client = FakeOpenAIClient([response])

    report = solve_with_openai(
        OpenAISolverConfig(
            solver_package=solver,
            output_dir=tmp_path / "run",
            model_name="fake-model",
            max_rounds=1,
            input_cost_per_1m=1.0,
            output_cost_per_1m=2.0,
        ),
        client=client,  # type: ignore[arg-type]
    )

    assert report.status == "completed"
    assert report.api_calls == 1
    assert report.token_usage == {"input_tokens": 10, "output_tokens": 20}
    assert report.estimated_cost_usd == pytest.approx(0.00005)
    assert report.build_script == tmp_path / "run" / "candidate" / "build.py"
    assert (tmp_path / "run" / "candidate" / "out" / "program").is_file()
    persisted = read_data(tmp_path / "run" / "openai_solver_run.json")
    assert persisted["status"] == "completed"
    assert persisted["api_calls"] == 1
    assert "hidden_tests" not in client.prompts[0]


def test_solve_with_openai_repairs_failed_initial_build(tmp_path: Path) -> None:
    solver = _write_solver_package(tmp_path)
    bad = {
        "files": [],
        "build_script": {"path": "build.py", "content": "raise SystemExit(3)\n"},
        "notes": "broken",
    }
    fixed = {
        "files": [],
        "build_script": {
            "path": "build.py",
            "content": (
                "from pathlib import Path\n"
                "out = Path('out')\n"
                "out.mkdir(exist_ok=True)\n"
                "program = out / 'program'\n"
                "program.write_text("
                "'#!/usr/bin/env python3\\nimport sys\\nprint(\"Usage: candidate\")\\n',"
                "encoding='utf-8')\n"
                "program.chmod(0o755)\n"
            ),
        },
        "notes": "fixed",
    }
    client = FakeOpenAIClient([bad, fixed])

    report = solve_with_openai(
        OpenAISolverConfig(
            solver_package=solver,
            output_dir=tmp_path / "run",
            model_name="fake-model",
            max_rounds=2,
        ),
        client=client,  # type: ignore[arg-type]
    )

    assert report.status == "completed"
    assert report.api_calls == 2
    assert len(report.rounds) == 2
    assert report.rounds[0].accepted is False
    assert "build failed" in client.prompts[1]


def test_public_smoke_does_not_expose_openai_api_key_to_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    build_script = candidate / "build.py"
    build_script.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path('observed.txt').write_text(str(os.getenv('OPENAI_API_KEY')), encoding='utf-8')\n"
        "out = Path('out')\n"
        "out.mkdir(exist_ok=True)\n"
        "program = out / 'program'\n"
        "program.write_text('#!/usr/bin/env python3\\nprint(\"Usage: candidate\")\\n', encoding='utf-8')\n"
        "program.chmod(0o755)\n",
        encoding="utf-8",
    )

    build_result, _smoke_results = run_public_smoke(candidate, build_script)

    assert build_result.ok
    assert (candidate / "observed.txt").read_text(encoding="utf-8") == "None"


def _write_solver_package(tmp_path: Path) -> Path:
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "TASK.md").write_text(
        "Implement the public command-line behavior.\n",
        encoding="utf-8",
    )
    (solver / "SUBMISSION.md").write_text(
        "Build script must create out/program.\n",
        encoding="utf-8",
    )
    (solver / "docs").mkdir()
    spec = TaskSpec(
        task_id="demo",
        repo_url="https://example.test/repo",
        commit_sha="abc123",
        language="python",
        build_system="script",
        docs_paths=["docs"],
    )
    write_data(solver / "task.yaml", spec.model_dump(mode="json"))
    return solver
