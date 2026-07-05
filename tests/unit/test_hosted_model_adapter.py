from __future__ import annotations

import json
from typing import Any

import pytest

from pbgen.testgen import hosted_model_adapter


def test_hosted_adapter_merges_rounds_and_reports_cost(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def fake_post_chat_completion(**kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs["prompt"]))
        if len(calls) == 1:
            cases = [
                {"args": ["--help"], "behavior_category": "flag", "source_evidence": "docs"},
                {"args": ["--help"], "behavior_category": "flag", "source_evidence": "dupe"},
            ]
        else:
            cases = [
                {"args": ["--version"], "behavior_category": "normal-path", "source_evidence": "docs"}
            ]
        return {
            "choices": [{"message": {"content": json.dumps({"test_cases": cases})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }

    monkeypatch.setattr(
        hosted_model_adapter,
        "_post_chat_completion",
        fake_post_chat_completion,
    )

    result, metadata = hosted_model_adapter.generate_structured_cases(
        "base prompt",
        {
            "PBGEN_HOSTED_MODEL_ENDPOINT": "https://example.invalid/v1/chat/completions",
            "PBGEN_HOSTED_MODEL_NAME": "review-model",
            "PBGEN_HOSTED_MODEL_ROUNDS": "2",
            "PBGEN_HOSTED_MODEL_INPUT_COST_PER_1M": "1.0",
            "PBGEN_HOSTED_MODEL_OUTPUT_COST_PER_1M": "2.0",
        },
    )

    assert len(calls) == 2
    assert result["test_cases"] == [
        {"args": ["--help"], "behavior_category": "flag", "source_evidence": "docs"},
        {"args": ["--version"], "behavior_category": "normal-path", "source_evidence": "docs"},
    ]
    assert metadata["accepted_cases"] == 2
    assert metadata["usage"] == {
        "prompt_tokens": 200,
        "completion_tokens": 40,
        "total_tokens": 240,
    }
    assert metadata["estimated_cost_usd"] == pytest.approx(0.00028)


def test_hosted_adapter_requires_endpoint() -> None:
    try:
        hosted_model_adapter.generate_structured_cases(
            "prompt",
            {"PBGEN_HOSTED_MODEL_NAME": "review-model"},
        )
    except hosted_model_adapter.HostedModelAdapterError as exc:
        assert "PBGEN_HOSTED_MODEL_ENDPOINT" in str(exc)
    else:
        raise AssertionError("expected hosted adapter error")
