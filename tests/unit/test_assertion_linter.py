from pbgen.config import PBGenConfig
from pbgen.quality.assertion_linter import AssertionQualityLinter


def test_assertion_linter_flags_weak_tests(tmp_path) -> None:
    test_file = tmp_path / "test_weak.py"
    test_file.write_text(
        """
def test_no_assertion():
    value = 1

def test_short_substring(result=None):
    output = "abc"
    assert "ok" in output
""",
        encoding="utf-8",
    )
    report = AssertionQualityLinter(PBGenConfig(workspace_root=tmp_path)).lint_path("demo", tmp_path)
    assert report.high_count >= 2


def test_assertion_linter_accepts_strong_output_check(tmp_path) -> None:
    test_file = tmp_path / "test_strong.py"
    test_file.write_text(
        """
def test_strong():
    stdout = "invalid number: not-a-number"
    assert "invalid number: not-a-number" in stdout
""",
        encoding="utf-8",
    )
    report = AssertionQualityLinter(PBGenConfig(workspace_root=tmp_path)).lint_path("demo", tmp_path)
    assert report.high_count == 0
