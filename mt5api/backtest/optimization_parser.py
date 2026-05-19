"""Best-effort parser for MT5 optimization XML reports."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from mt5api.logger import log

_SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
_NS = {"ss": _SS_NS}
_INDEX_ATTR = f"{{{_SS_NS}}}Index"


def _coerce_value(value: str):
    value = value.strip()
    if not value:
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _row_values(row) -> list:
    values = []
    next_index = 1
    for cell in row.findall("ss:Cell", _NS):
        raw_index = cell.get(_INDEX_ATTR)
        if raw_index:
            try:
                cell_index = int(raw_index)
            except ValueError:
                cell_index = next_index
            while next_index < cell_index:
                values.append("")
                next_index += 1
        data = cell.find("ss:Data", _NS)
        values.append(_coerce_value(data.text or "") if data is not None else "")
        next_index += 1
    return values


def parse_optimization_report(xml_path: str, top_n: int = 50) -> list[dict]:
    """Parse an MT5 optimization XML report into a top-N list of pass dicts."""
    try:
        tree = ET.parse(xml_path)
        rows = tree.findall(".//ss:Worksheet/ss:Table/ss:Row", _NS)
    except (ET.ParseError, OSError) as exc:
        log.warning("optimization xml parse failed path=%s error=%s", xml_path, exc)
        return []

    if not rows:
        return []

    headers = [str(value) for value in _row_values(rows[0]) if str(value).strip()]
    if not headers:
        return []

    parsed_rows = []
    for row in rows[1:]:
        values = _row_values(row)
        if not any(value not in ("", None) for value in values):
            continue
        record = {
            header: values[index] if index < len(values) else ""
            for index, header in enumerate(headers)
        }
        parsed_rows.append(record)

    if not parsed_rows:
        return []

    if "Result" in headers:
        parsed_rows.sort(
            key=lambda row: row["Result"] if isinstance(row.get("Result"), (int, float)) else float("-inf"),
            reverse=True,
        )

    return parsed_rows[:top_n]