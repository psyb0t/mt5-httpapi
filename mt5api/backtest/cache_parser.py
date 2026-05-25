from __future__ import annotations

import re
import struct
from datetime import datetime
from pathlib import Path

from mt5api.logger import log

OPT_CACHE_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,10}$")
_PASS_PATTERNS = (
    re.compile(r"optimization\s+pass\s+#?(\d+)\s+started", re.IGNORECASE),
    re.compile(r"pass\s+#?(\d+)\s+started", re.IGNORECASE),
    re.compile(r"started\s+optimization\s+pass\s+#?(\d+)", re.IGNORECASE),
)
_SYMBOL_PATTERNS = (
    re.compile(r"Symbols\s+([^:]+):\s+symbol to be synchronized", re.IGNORECASE),
    re.compile(r"symbol\s+([A-Z0-9]{3,10})\s+to be synchronized", re.IGNORECASE),
)
_HISTORY_PATTERNS = (
    re.compile(r"History\s+([^,]+),([^:]+):\s+history cache allocated", re.IGNORECASE),
    re.compile(r"History\s+([^,]+),([^:]+):\s+history synchronized", re.IGNORECASE),
    re.compile(r"History\s+([^,]+),([^:]+):", re.IGNORECASE),
)

OPT_CACHE_PROFILE_CANDIDATES = [
    {
        "name": "symbol-relative-v1",
        "metric_relative_offsets": {
            "Result": -272,
            "Profit": -256,
            "Expected Payoff": -104,
            "Profit Factor": -96,
            "Recovery Factor": -88,
            "Sharpe Ratio": -80,
            "Equity DD %": -112,
        },
        "trades_relative_offset": -52,
    }
]


def extract_tester_settings(ini_text: str) -> dict[str, str]:
    settings: dict[str, str] = {}
    in_tester = False

    for raw_line in ini_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            if in_tester:
                break
            in_tester = line.lower() == "[tester]"
            continue
        if in_tester and "=" in raw_line:
            key, value = raw_line.split("=", 1)
            settings[key.strip()] = value.strip()

    return settings


def compact_date(date_text: str) -> str:
    return date_text.replace(".", "")


def build_cache_pattern(tester_settings: dict[str, str]) -> str | None:
    expert_value = tester_settings.get("Expert")
    period_value = tester_settings.get("Period")
    from_date = tester_settings.get("FromDate")
    to_date = tester_settings.get("ToDate")
    if not all((expert_value, period_value, from_date, to_date)):
        return None

    expert_name = Path(expert_value.replace("\\", "/")).stem
    symbol_value = tester_settings.get("Symbol", "*")
    optimization_mode = tester_settings.get("Optimization")
    symbol_component = "all_symbols" if optimization_mode == "3" else symbol_value
    return (
        f"{expert_name}.{symbol_component}.{period_value}."
        f"{compact_date(from_date)}.{compact_date(to_date)}.*.opt"
    )


def parse_cache_filename(cache_path: Path) -> dict[str, str] | None:
    parts = cache_path.stem.split(".")
    if len(parts) < 7:
        return None

    return {
        "expert": ".".join(parts[:-6]),
        "symbol_component": parts[-6],
        "period": parts[-5],
        "from_date": parts[-4],
        "to_date": parts[-3],
        "build": parts[-2],
        "cache_hash": parts[-1],
    }


def iter_utf16_strings_with_offsets(data: bytes) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for match in re.finditer(rb"(?:[\x20-\x7E]\x00){3,}", data):
        raw_value = match.group(0)
        try:
            decoded = raw_value.decode("utf-16le")
        except UnicodeDecodeError:
            continue
        matches.append((match.start(), decoded))
    return matches


def detect_opt_cache_row_layout(data: bytes) -> dict[str, int | list[int]] | None:
    offsets = [
        offset
        for offset, value in iter_utf16_strings_with_offsets(data)
        if OPT_CACHE_SYMBOL_RE.fullmatch(value)
    ]
    if len(offsets) < 2:
        return None

    sorted_offsets = sorted(offsets)
    deltas = [
        current - previous
        for previous, current in zip(sorted_offsets, sorted_offsets[1:])
        if current > previous
    ]
    if not deltas:
        return None

    stride = max(set(deltas), key=deltas.count)
    best_run: list[int] = []
    current_run = [sorted_offsets[0]]
    for offset in sorted_offsets[1:]:
        if offset - current_run[-1] == stride:
            current_run.append(offset)
            continue
        if len(current_run) > len(best_run):
            best_run = current_run[:]
        current_run = [offset]
    if len(current_run) > len(best_run):
        best_run = current_run[:]

    if len(best_run) < 2:
        return None

    row_count = len(best_run)
    row_start = len(data) - (row_count * stride)
    symbol_offset = best_run[0] - row_start
    if row_start < 0 or symbol_offset < 0 or symbol_offset >= stride:
        return None

    return {
        "stride": stride,
        "row_count": row_count,
        "row_start": row_start,
        "symbol_offset": symbol_offset,
        "symbol_offsets": best_run,
    }


def score_opt_cache_profile(records: list[bytes], metric_offsets: dict[str, int], trades_offset: int) -> float:
    penalties = 0.0
    valid_rows = 0
    payoff_error_total = 0.0

    for record in records:
        if trades_offset < 0 or trades_offset + 4 > len(record):
            return float("inf")
        trades = struct.unpack_from("<I", record, trades_offset)[0]
        if trades <= 0:
            penalties += 1000.0
            continue

        values: dict[str, float] = {}
        for column, offset in metric_offsets.items():
            if offset < 0 or offset + 8 > len(record):
                return float("inf")
            values[column] = struct.unpack_from("<d", record, offset)[0]

        profit = values["Profit"]
        expected_payoff = values["Expected Payoff"]
        payoff_error_total += abs(profit - (expected_payoff * trades))

        equity_dd = values["Equity DD %"]
        if not (0.0 <= equity_dd <= 1000.0):
            penalties += 50.0
        if values["Profit Factor"] <= 0:
            penalties += 10.0
        valid_rows += 1

    if valid_rows == 0:
        return float("inf")

    return penalties + (payoff_error_total / valid_rows)


def detect_opt_cache_metric_layout(layout: dict[str, int | list[int]], data: bytes) -> dict[str, object] | None:
    row_start = int(layout["row_start"])
    row_count = int(layout["row_count"])
    stride = int(layout["stride"])
    symbol_offset = int(layout["symbol_offset"])
    records = [
        data[row_start + (index * stride):row_start + ((index + 1) * stride)]
        for index in range(row_count)
    ]

    best_profile: dict[str, object] | None = None
    best_score = float("inf")
    for candidate in OPT_CACHE_PROFILE_CANDIDATES:
        metric_offsets = {
            column: symbol_offset + relative_offset
            for column, relative_offset in candidate["metric_relative_offsets"].items()
        }
        trades_offset = symbol_offset + int(candidate["trades_relative_offset"])
        score = score_opt_cache_profile(records, metric_offsets, trades_offset)
        if score >= best_score:
            continue
        best_score = score
        best_profile = {
            "name": candidate["name"],
            "metric_offsets": metric_offsets,
            "trades_offset": trades_offset,
            "score": score,
        }

    return best_profile


def decode_utf16_c_string(data: bytes) -> str:
    text = data.decode("utf-16le", errors="ignore")
    return text.split("\x00", 1)[0]


def format_cache_metric(value: float) -> float | int:
    rounded = round(value, 2)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded


def parse_mt5_log_time(raw_line: str, log_date: datetime) -> datetime | None:
    match = re.search(r"\b(\d{2}:\d{2}:\d{2})\.(\d{3})\b", raw_line)
    if not match:
        return None

    time_part = match.group(1)
    millis = int(match.group(2))
    parsed = datetime.strptime(f"{log_date.strftime('%Y-%m-%d')} {time_part}", "%Y-%m-%d %H:%M:%S")
    return parsed.replace(microsecond=millis * 1000)


def _extract_pass_number(line: str) -> int | None:
    for pattern in _PASS_PATTERNS:
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None


def _extract_symbol_candidate(line: str, period: str) -> tuple[str, str] | None:
    for pattern in _SYMBOL_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group(1).strip(), "symbols"

    for pattern in _HISTORY_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        symbol = match.group(1).strip()
        match_period = match.group(2).strip()
        if match_period.upper() != period.upper():
            continue
        return symbol, "history"

    return None


def parse_all_symbols_from_agent_logs(cache_path: Path, agent_logs_base_dir: Path) -> list[dict[str, str | int]]:
    parsed_name = parse_cache_filename(cache_path)
    if not parsed_name:
        return []

    cache_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
    log_date = cache_time.strftime("%Y%m%d")
    window_start = cache_time.timestamp() - 300
    window_end = cache_time.timestamp() + 10
    pass_to_symbol: dict[int, str] = {}
    pass_source: dict[int, str] = {}
    period = str(parsed_name["period"])

    for agent_logs_dir in sorted(agent_logs_base_dir.glob("Agent-127.0.0.1-*/logs")):
        log_path = agent_logs_dir / f"{log_date}.log"
        if not log_path.exists():
            continue

        try:
            lines = log_path.read_bytes().decode("utf-16-le", errors="replace").splitlines()
        except OSError:
            continue

        current_pass: int | None = None
        for line in lines:
            parsed_time = parse_mt5_log_time(line, cache_time)
            if not parsed_time:
                continue
            timestamp = parsed_time.timestamp()
            if timestamp < window_start or timestamp > window_end:
                continue

            pass_number = _extract_pass_number(line)
            if pass_number is not None:
                current_pass = pass_number
                continue

            if current_pass is None:
                continue

            symbol_candidate = _extract_symbol_candidate(line, period)
            if symbol_candidate is None:
                continue

            symbol, source = symbol_candidate
            if current_pass not in pass_to_symbol:
                pass_to_symbol[current_pass] = symbol
                pass_source[current_pass] = source
                continue

            # Prefer explicit history lines for the requested period over
            # generic symbol sync chatter when both appear for the same pass.
            if source == "history" and pass_source.get(current_pass) != "history":
                pass_to_symbol[current_pass] = symbol
                pass_source[current_pass] = source

    return [
        {
            "pass": pass_number,
            "symbol": pass_to_symbol[pass_number],
            "source": pass_source.get(pass_number, "unknown"),
        }
        for pass_number in sorted(pass_to_symbol)
    ]


def build_cache_metadata(cache_path: Path, tester_settings: dict[str, str], rows: list[dict[str, object]]) -> dict[str, object]:
    parsed_name = parse_cache_filename(cache_path) or {}
    pattern = build_cache_pattern(tester_settings)
    stats = cache_path.stat()
    return {
        "path": str(cache_path),
        "name": cache_path.name,
        "sizeBytes": stats.st_size,
        "modifiedAt": datetime.fromtimestamp(stats.st_mtime).isoformat(),
        "pattern": pattern,
        "rowCount": len(rows),
        "expert": parsed_name.get("expert"),
        "symbolComponent": parsed_name.get("symbol_component"),
        "period": parsed_name.get("period"),
        "fromDate": parsed_name.get("from_date"),
        "toDate": parsed_name.get("to_date"),
        "build": parsed_name.get("build"),
        "cacheHash": parsed_name.get("cache_hash"),
    }


def parse_opt_cache_rows(cache_path: Path, tester_settings: dict[str, str], agent_logs_base_dir: Path) -> list[dict[str, object]]:
    data = cache_path.read_bytes()
    layout = detect_opt_cache_row_layout(data)
    if layout is None:
        raise ValueError("could not detect MT5 optimization row layout in cache")

    row_start = int(layout["row_start"])
    row_count = int(layout["row_count"])
    stride = int(layout["stride"])
    symbol_offset = int(layout["symbol_offset"])
    metric_layout = detect_opt_cache_metric_layout(layout, data)
    if metric_layout is None:
        raise ValueError("could not detect MT5 optimization metric layout in cache")

    metric_offsets = dict(metric_layout["metric_offsets"])
    trades_offset = int(metric_layout["trades_offset"])

    symbol_to_pass = {
        str(entry["symbol"]): int(entry["pass"])
        for entry in parse_all_symbols_from_agent_logs(cache_path, agent_logs_base_dir)
    }

    rows: list[dict[str, object]] = []
    for index in range(row_count):
        record_offset = row_start + (index * stride)
        record = data[record_offset:record_offset + stride]
        if len(record) < stride:
            continue

        symbol = decode_utf16_c_string(record[symbol_offset:symbol_offset + (stride - symbol_offset)])
        if not symbol or not OPT_CACHE_SYMBOL_RE.fullmatch(symbol):
            continue

        row: dict[str, object] = {
            "Symbol": symbol,
            "Pass": symbol_to_pass.get(symbol, index),
            "Custom": "",
        }
        for column, offset in metric_offsets.items():
            value = struct.unpack_from("<d", record, offset)[0]
            row[column] = format_cache_metric(value)
        trades = struct.unpack_from("<I", record, trades_offset)[0]
        row["Trades"] = int(trades)
        rows.append(row)

    return rows


def _sort_rows_by_result(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    def sort_key(row: dict[str, object]) -> tuple[int, float, str]:
        value = row.get("Result")
        if isinstance(value, (int, float)):
            return (0, -float(value), str(row.get("Symbol", "")))
        try:
            return (0, -float(value), str(row.get("Symbol", "")))
        except (TypeError, ValueError):
            return (1, float("inf"), str(row.get("Symbol", "")))

    return sorted(rows, key=sort_key)


def find_cache_for_job(debug_ini_path: str, terminal_dir: str) -> Path | None:
    try:
        ini_text = Path(debug_ini_path).read_text(encoding="utf-8")
    except OSError:
        return None

    tester_settings = extract_tester_settings(ini_text)
    cache_pattern = build_cache_pattern(tester_settings)
    if not cache_pattern:
        return None

    cache_dir = Path(terminal_dir) / "Tester" / "cache"
    if not cache_dir.exists():
        return None

    candidates = list(cache_dir.glob(cache_pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def parse_cache_details(debug_ini_path: str, terminal_dir: str, top_n: int = 50) -> dict[str, object]:
    try:
        ini_text = Path(debug_ini_path).read_text(encoding="utf-8")
        tester_settings = extract_tester_settings(ini_text)
        cache_path = find_cache_for_job(debug_ini_path, terminal_dir)
        if cache_path is None:
            return {"rows": [], "cache": None}
        rows = parse_opt_cache_rows(cache_path, tester_settings, Path(terminal_dir) / "Tester")
        sorted_rows = _sort_rows_by_result(rows)
        return {
            "rows": sorted_rows[:top_n],
            "cache": build_cache_metadata(cache_path, tester_settings, sorted_rows),
        }
    except (OSError, ValueError, struct.error) as exc:
        log.warning("optimization cache parse failed ini=%s error=%s", debug_ini_path, exc)
        return {"rows": [], "cache": None}


def parse_cache_results(debug_ini_path: str, terminal_dir: str, top_n: int = 50) -> list[dict[str, object]]:
    return list(parse_cache_details(debug_ini_path, terminal_dir, top_n).get("rows") or [])