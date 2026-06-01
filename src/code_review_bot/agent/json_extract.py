"""Utilities for extracting and repairing JSON from LLM text output, with retry logic."""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import json_repair
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(?P<json>\{.*?\})\s*```", re.DOTALL)
TextRunner = Callable[[str], Awaitable[str]]

_DIAGNOSTIC_TRUNCATE = 4000
_CTRL_ESCAPE = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


async def complete_json_with_retries(
    prompt: str,
    schema: type[SchemaT],
    runner: TextRunner,
    max_retries: int = 1,
) -> SchemaT:
    last_error: ValueError | ValidationError | json.JSONDecodeError | None = None
    current_prompt = prompt
    for attempt in range(max_retries + 1):
        text = await runner(current_prompt)
        try:
            return schema.model_validate(_parse_json(extract_json(text), attempt=attempt))
        except json.JSONDecodeError as error:
            _log_parse_failure("invalid JSON syntax", error, text, attempt)
            last_error = error
        except ValueError as error:
            _log_parse_failure("no JSON object found", error, text, attempt)
            last_error = error
        except ValidationError as error:
            _log_parse_failure("schema validation failed", error, text, attempt)
            last_error = error
        current_prompt = f"{prompt}\n\nReturn only valid JSON."
    raise ValueError("coding agent did not return valid structured JSON") from last_error


def _parse_json(text: str, *, attempt: int = 0) -> Any:
    """Parse JSON via stdlib, falling back to json-repair on syntax errors.

    Agents occasionally emit JSON with unescaped quotes inside string values.
    json-repair recovers from these without forcing a full re-run; a warning is
    logged so persistent quality issues surface in operations.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        logger.warning(
            "stdlib JSON parse failed attempt=%d error=%s; falling back to json-repair",
            attempt,
            error,
        )
        repaired = json_repair.repair_json(text)
        return json.loads(repaired)


def extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return _escape_control_chars(stripped)
    block = JSON_BLOCK_RE.search(stripped)
    if block:
        return _escape_control_chars(block.group("json"))
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return _escape_control_chars(stripped[start : end + 1])
    raise ValueError("no JSON object found")


def _escape_control_chars(s: str) -> str:
    """Escape literal control characters inside JSON string values."""
    result: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            result.append(c)
            i += 1
            while i < n:
                c = s[i]
                if c == "\\":
                    result.append(c)
                    i += 1
                    if i < n:
                        result.append(s[i])
                        i += 1
                elif c == '"':
                    result.append(c)
                    i += 1
                    break
                elif ord(c) < 0x20:
                    result.append(_CTRL_ESCAPE.get(c, f"\\u{ord(c):04x}"))
                    i += 1
                else:
                    result.append(c)
                    i += 1
        else:
            result.append(c)
            i += 1
    return "".join(result)


def _log_parse_failure(
    kind: str,
    error: Exception,
    text: str,
    attempt: int,
) -> None:
    snippet = text[:_DIAGNOSTIC_TRUNCATE]
    logger.warning(
        "JSON parse failed kind=%r attempt=%d output_len=%d error=%s snippet=%r",
        kind,
        attempt,
        len(text),
        error,
        snippet,
    )
