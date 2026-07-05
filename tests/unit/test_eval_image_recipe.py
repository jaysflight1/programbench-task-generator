from __future__ import annotations

from pathlib import Path


def test_eval_image_recipe_documents_expected_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = root / "docker" / "pbgen-eval" / "Dockerfile"
    helper = root / "scripts" / "build_eval_image.sh"

    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    helper_text = helper.read_text(encoding="utf-8")

    assert "debian:bookworm-slim" in dockerfile_text
    assert "python3" in dockerfile_text
    assert "python3-yaml" in dockerfile_text
    assert "gcc" in dockerfile_text
    assert "g++" in dockerfile_text
    assert "make" in dockerfile_text
    assert "cmake" in dockerfile_text
    assert "pbgen-eval:py-c" in helper_text
    assert "docker build" in helper_text
