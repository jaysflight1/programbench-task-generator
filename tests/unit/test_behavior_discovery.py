from __future__ import annotations

from pathlib import Path

from pbgen.build.build_agent import build_gold
from pbgen.config import PBGenConfig
from pbgen.repo_discovery.checkout import init_task
from pbgen.serialization import read_data
from pbgen.testgen.behavioral_surface import discover_behavior_surface


FIXTURES = Path(__file__).parents[2] / "examples" / "robust_repos"


def test_behavior_discovery_harvests_docs_native_tests_and_safe_probes(tmp_path: Path) -> None:
    config = PBGenConfig(workspace_root=tmp_path)
    init_task(task_id="behavior", config=config, local_path=FIXTURES / "behavior_docs")
    build_gold("behavior", config)

    surface = discover_behavior_surface("behavior", config)
    reports = tmp_path / "artifacts" / "behavior" / "reports"
    planned = read_data(reports / "command_probes_planned.json")
    observed = read_data(reports / "command_probes_observed.json")

    assert surface.stdin_supported is True
    assert {"APP_MODE", "PBGEN_EXECUTABLE"}.issubset(set(surface.env_vars))
    assert {"settings.toml", ".toolrc", "config.yaml"}.issubset(set(surface.config_files))
    assert "sample.txt" in surface.file_inputs
    assert any("unknown command" in item for item in surface.error_cases)
    assert any(example.category == "native-test" for example in surface.command_examples)
    assert any(behavior.args == ["echo", "hello"] for behavior in surface.recorded_behaviors)

    planned_probes = planned["probes"]
    delete_probe = next(probe for probe in planned_probes if probe["args"] == ["delete", "all"])
    assert delete_probe["safe"] is False
    assert "destructive token" in delete_probe["reason"]
    assert all(probe["args"] != ["delete", "all"] for probe in observed["probes"])
    assert any(item["args"] == ["echo", "hello"] for item in observed["recorded_behaviors"])
