"""Strict JSON parsing for core and execution responses."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_text(text: str) -> str:
    raw = str(text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    start_positions = [index for index in (raw.find("{"), raw.find("[")) if index >= 0]
    if not start_positions:
        return raw
    start = min(start_positions)
    opening = raw[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if escaped:
            escaped = False
            continue
        if in_string and char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == opening:
            depth += 1
            continue
        if char == closing:
            depth -= 1
            if depth == 0:
                return raw[start : index + 1].strip()
    return raw


def parse_json_model(text: str, model_class: type[Any]) -> Any:
    payload = json.loads(extract_json_text(text))
    return model_class.model_validate(payload)
