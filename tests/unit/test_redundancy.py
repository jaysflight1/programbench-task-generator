from pbgen.quality.redundancy import RedundancyAnalyzer


def test_redundancy_clusters_duplicate_command_shapes(tmp_path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_behavior.py").write_text(
        """
def run_cmd(args):
    return None

def test_one():
    result = run_cmd(["add", "1", "2"])
    assert result.stdout == "3"

def test_two():
    result = run_cmd(["add", "1", "2"])
    assert result.stdout == "3"
""",
        encoding="utf-8",
    )
    report = RedundancyAnalyzer().analyze("demo", tests, tmp_path / "redundancy.json", tmp_path / "events.jsonl")
    assert report.redundancy_score > 0
