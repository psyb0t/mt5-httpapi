"""Build MT5 .set files from structured JSON parameters.

The MT5 Strategy Tester accepts plain `name=value` lines for fixed inputs and
`name=value||start||step||stop||Y|N` lines for optimization ranges exported from
the UI. This module emits that MT5-native text form so callers do not need to
hand-author .set files.
"""
from __future__ import annotations


def _stringify_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError("parameter value must be a string, number, or boolean")


def _stringify_optimize(value):
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in ("Y", "N"):
            return normalized
    raise ValueError("optimize must be a boolean or 'Y'/'N'")


def _render_entry(entry):
    if not isinstance(entry, dict):
        raise ValueError("each parameter entry must be an object")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("parameter name is required")
    name = name.strip()

    if "value" not in entry:
        raise ValueError(f"parameter '{name}' is missing value")

    value = _stringify_scalar(entry["value"])

    has_range_fields = any(
        key in entry for key in ("start", "step", "stop", "optimize")
    )
    if not has_range_fields:
        return f"{name}={value}"

    missing = [key for key in ("start", "step", "stop", "optimize") if key not in entry]
    if missing:
        raise ValueError(
            f"parameter '{name}' is missing optimization fields: {', '.join(missing)}"
        )

    start = _stringify_scalar(entry["start"])
    step = _stringify_scalar(entry["step"])
    stop = _stringify_scalar(entry["stop"])
    optimize = _stringify_optimize(entry["optimize"])
    return f"{name}={value}||{start}||{step}||{stop}||{optimize}"


def build_set(params: dict) -> str:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")

    comments = params.get("comments", [])
    if comments is None:
        comments = []
    if not isinstance(comments, list) or any(not isinstance(line, str) for line in comments):
        raise ValueError("comments must be a list of strings")

    parameters = params.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        raise ValueError("parameters must be a non-empty array")

    lines = []
    for comment in comments:
        if comment.startswith(";"):
            lines.append(comment)
        else:
            lines.append(f"; {comment}")

    for entry in parameters:
        lines.append(_render_entry(entry))

    return "\n".join(lines) + "\n"