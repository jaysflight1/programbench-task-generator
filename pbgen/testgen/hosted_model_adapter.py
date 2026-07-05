"""Hosted-model command adapter for pbgen's external-command backend."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
import re
import sys
from typing import Any, cast
import urllib.error
import urllib.request

from pbgen.testgen.diversity import REQUIRED_BEHAVIOR_CATEGORIES


DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_ROUNDS = 2


def main(argv: list[str] | None = None) -> int:
    """Read a pbgen prompt from stdin and write structured test-case JSON to stdout."""

    del argv
    prompt_text = sys.stdin.read()
    try:
        result, metadata = generate_structured_cases(prompt_text, os.environ)
    except HostedModelAdapterError as exc:
        print(f"hosted model adapter error: {exc}", file=sys.stderr)
        return 2
    _write_metadata(metadata)
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


class HostedModelAdapterError(Exception):
    """Expected adapter failure shown to pbgen as a model-command error."""


def generate_structured_cases(
    prompt_text: str,
    env: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one or more hosted-model rounds and merge structured proposals."""

    endpoint = env.get("PBGEN_HOSTED_MODEL_ENDPOINT", "").strip()
    if not endpoint:
        raise HostedModelAdapterError("PBGEN_HOSTED_MODEL_ENDPOINT is required")
    model = (
        env.get("PBGEN_HOSTED_MODEL_NAME")
        or env.get("PBGEN_MODEL_NAME")
        or env.get("PBGEN_MODEL")
        or ""
    ).strip()
    if not model:
        raise HostedModelAdapterError("PBGEN_HOSTED_MODEL_NAME or PBGEN_MODEL_NAME is required")
    temperature = _float_env(env, "PBGEN_MODEL_TEMPERATURE", 0.2)
    timeout = _int_env(env, "PBGEN_MODEL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    rounds = max(1, _int_env(env, "PBGEN_HOSTED_MODEL_ROUNDS", DEFAULT_ROUNDS))
    api_key = env.get("PBGEN_HOSTED_MODEL_API_KEY", "").strip()

    merged_cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    round_metadata: list[dict[str, Any]] = []
    for round_index, categories in enumerate(_category_rounds(rounds)):
        round_prompt = _round_prompt(prompt_text, categories, merged_cases)
        response = _post_chat_completion(
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            prompt=round_prompt,
            temperature=temperature,
            timeout_seconds=timeout,
        )
        content = _extract_response_text(response)
        parsed = _parse_structured_json(content)
        accepted_this_round = 0
        for case in parsed["test_cases"]:
            key = _case_key(case)
            if key in seen:
                continue
            seen.add(key)
            merged_cases.append(case)
            accepted_this_round += 1
        round_metadata.append(
            {
                "round": round_index,
                "focus_categories": categories,
                "accepted_cases": accepted_this_round,
                "raw_cases": len(parsed["test_cases"]),
                "usage": response.get("usage", {}),
            }
        )

    metadata = {
        "adapter": "pbgen-hosted-model-adapter-v1",
        "model": model,
        "temperature": temperature,
        "rounds": rounds,
        "prompt_chars": len(prompt_text),
        "accepted_cases": len(merged_cases),
        "rounds_detail": round_metadata,
        "usage": _sum_usage(round_metadata),
        "estimated_cost_usd": _estimated_cost(round_metadata, env),
    }
    return {"test_cases": merged_cases}, metadata


def _post_chat_completion(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    prompt: str,
    temperature: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return only valid JSON with a top-level test_cases array. "
                    "Do not include markdown fences or prose."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HostedModelAdapterError(
            f"hosted model request failed: HTTP {exc.code}: {_clip(body, 2_000)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise HostedModelAdapterError(f"hosted model request failed: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HostedModelAdapterError("hosted model response was not JSON") from exc
    if not isinstance(data, dict):
        raise HostedModelAdapterError("hosted model response must be a JSON object")
    return cast(dict[str, Any], data)


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return cast(str, message["content"])
            if isinstance(first.get("text"), str):
                return cast(str, first["text"])
    output = response.get("output")
    if isinstance(output, list):
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    text_parts.append(cast(str, block["text"]))
        if text_parts:
            return "\n".join(text_parts)
    raise HostedModelAdapterError("could not find model text in hosted response")


def _parse_structured_json(text: str) -> dict[str, list[dict[str, Any]]]:
    candidate = _extract_json_text(text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise HostedModelAdapterError("model text was not valid structured JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("test_cases"), list):
        raise HostedModelAdapterError("model JSON must contain a top-level test_cases array")
    cases = []
    for index, item in enumerate(data["test_cases"]):
        if not isinstance(item, dict):
            raise HostedModelAdapterError(f"test_cases[{index}] must be an object")
        cases.append(cast(dict[str, Any], item))
    return {"test_cases": cases}


def _extract_json_text(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        return fenced.group(1)
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _category_rounds(rounds: int) -> list[list[str]]:
    categories = list(REQUIRED_BEHAVIOR_CATEGORIES)
    chunk_size = max(1, (len(categories) + rounds - 1) // rounds)
    chunks = [
        categories[index : index + chunk_size]
        for index in range(0, len(categories), chunk_size)
    ]
    while len(chunks) < rounds:
        chunks.append(categories)
    return chunks[:rounds]


def _round_prompt(
    prompt_text: str,
    categories: list[str],
    accepted_cases: list[dict[str, Any]],
) -> str:
    return (
        f"{prompt_text}\n\n"
        "Round focus categories:\n"
        f"{json.dumps(categories, indent=2)}\n\n"
        "Already proposed cases to avoid duplicating:\n"
        f"{json.dumps(accepted_cases[-50:], indent=2, sort_keys=True)}\n"
    )


def _case_key(case: dict[str, Any]) -> str:
    payload = {
        "args": case.get("args", []),
        "stdin": case.get("stdin", ""),
        "env": case.get("env", {}),
        "fixture_files": case.get("fixture_files", {}),
        "behavior_category": case.get("behavior_category"),
    }
    return json.dumps(payload, sort_keys=True)


def _sum_usage(round_metadata: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for item in round_metadata:
        usage = item.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    return totals


def _estimated_cost(
    round_metadata: list[dict[str, Any]],
    env: Mapping[str, str],
) -> float | None:
    input_rate = _optional_float_env(env, "PBGEN_HOSTED_MODEL_INPUT_COST_PER_1M")
    output_rate = _optional_float_env(env, "PBGEN_HOSTED_MODEL_OUTPUT_COST_PER_1M")
    if input_rate is None or output_rate is None:
        return None
    usage = _sum_usage(round_metadata)
    return (
        usage["prompt_tokens"] / 1_000_000 * input_rate
        + usage["completion_tokens"] / 1_000_000 * output_rate
    )


def _write_metadata(metadata: dict[str, Any]) -> None:
    path_value = os.environ.get("PBGEN_MODEL_METADATA_PATH")
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    value = env.get(key)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise HostedModelAdapterError(f"{key} must be an integer") from exc


def _float_env(env: Mapping[str, str], key: str, default: float) -> float:
    value = env.get(key)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise HostedModelAdapterError(f"{key} must be a float") from exc


def _optional_float_env(
    env: Mapping[str, str],
    key: str,
) -> float | None:
    value = env.get(key)
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise HostedModelAdapterError(f"{key} must be a float") from exc


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "...[clipped]"


if __name__ == "__main__":
    raise SystemExit(main())
