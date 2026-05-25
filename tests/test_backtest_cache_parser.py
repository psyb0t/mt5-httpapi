from __future__ import annotations

import struct
from datetime import datetime

from mt5api.backtest import cache_parser


def _build_record(symbol: str, result: float, profit: float, trades: int) -> bytes:
    stride = 320
    symbol_offset = 280
    record = bytearray(stride)
    struct.pack_into("<d", record, symbol_offset - 272, result)
    struct.pack_into("<d", record, symbol_offset - 256, profit)
    struct.pack_into("<d", record, symbol_offset - 112, 12.5)
    struct.pack_into("<d", record, symbol_offset - 104, profit / trades)
    struct.pack_into("<d", record, symbol_offset - 96, 1.7)
    struct.pack_into("<d", record, symbol_offset - 88, 2.4)
    struct.pack_into("<d", record, symbol_offset - 80, 0.8)
    struct.pack_into("<I", record, symbol_offset - 52, trades)
    encoded_symbol = f"{symbol}\x00".encode("utf-16le")
    record[symbol_offset:symbol_offset + len(encoded_symbol)] = encoded_symbol
    return bytes(record)


def test_build_cache_pattern_uses_all_symbols_for_mode3():
    pattern = cache_parser.build_cache_pattern(
        {
            "Expert": r"Uploaded\MyEA",
            "Period": "M15",
            "FromDate": "2021.05.25",
            "ToDate": "2026.05.25",
            "Optimization": "3",
            "Symbol": "GBPCAD",
        }
    )
    assert pattern == "MyEA.all_symbols.M15.20210525.20260525.*.opt"


def test_parse_cache_filename_handles_mode3_name():
    parsed = cache_parser.parse_cache_filename(
        cache_parser.Path(
            "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.22.788ECDD113BA3097A58EF888EBEFF9CA.opt"
        )
    )
    assert parsed == {
        "expert": "EA Studio GBPCAD M15 1615044595",
        "symbol_component": "all_symbols",
        "period": "M15",
        "from_date": "20210525",
        "to_date": "20260525",
        "build": "22",
        "cache_hash": "788ECDD113BA3097A58EF888EBEFF9CA",
    }


def test_detect_opt_cache_row_layout_finds_stride_from_symbols():
    data = _build_record("EURUSD", 42.0, 84.0, 2) + _build_record("GBPUSD", 55.0, 110.0, 2)
    layout = cache_parser.detect_opt_cache_row_layout(data)
    assert layout is not None
    assert layout["stride"] == 320
    assert layout["row_count"] == 2
    assert layout["symbol_offset"] == 280


def test_parse_cache_results_reads_and_sorts_opt_rows(tmp_path):
    terminal_dir = tmp_path / "terminal"
    cache_dir = terminal_dir / "Tester" / "cache"
    cache_dir.mkdir(parents=True)
    (terminal_dir / "Tester" / "Agent-127.0.0.1-3001" / "logs").mkdir(parents=True)

    ini_path = tmp_path / "normalized.ini"
    ini_path.write_text(
        "[Tester]\n"
        "Expert=Uploaded\\MyEA\n"
        "Symbol=GBPCAD\n"
        "Period=M15\n"
        "FromDate=2021.05.25\n"
        "ToDate=2026.05.25\n"
        "Optimization=3\n",
        encoding="utf-8",
    )

    cache_path = cache_dir / "MyEA.all_symbols.M15.20210525.20260525.22.ABCDEF.opt"
    cache_path.write_bytes(
        _build_record("EURUSD", 42.0, 84.0, 2) + _build_record("GBPUSD", 55.0, 110.0, 2)
    )

    rows = cache_parser.parse_cache_results(str(ini_path), str(terminal_dir), top_n=1)

    assert rows == [
        {
            "Symbol": "GBPUSD",
            "Pass": 1,
            "Result": 55,
            "Profit": 110,
            "Expected Payoff": 55,
            "Profit Factor": 1.7,
            "Recovery Factor": 2.4,
            "Sharpe Ratio": 0.8,
            "Custom": "",
            "Equity DD %": 12.5,
            "Trades": 2,
        }
    ]


def test_parse_cache_details_includes_cache_metadata(tmp_path):
    terminal_dir = tmp_path / "terminal"
    cache_dir = terminal_dir / "Tester" / "cache"
    cache_dir.mkdir(parents=True)

    ini_path = tmp_path / "normalized.ini"
    ini_path.write_text(
        "[Tester]\n"
        "Expert=Uploaded\\MyEA\n"
        "Symbol=GBPCAD\n"
        "Period=M15\n"
        "FromDate=2021.05.25\n"
        "ToDate=2026.05.25\n"
        "Optimization=3\n",
        encoding="utf-8",
    )

    cache_path = cache_dir / "MyEA.all_symbols.M15.20210525.20260525.22.ABCDEF.opt"
    cache_path.write_bytes(
        _build_record("EURUSD", 42.0, 84.0, 2) + _build_record("GBPUSD", 55.0, 110.0, 2)
    )

    details = cache_parser.parse_cache_details(str(ini_path), str(terminal_dir), top_n=10)

    assert details["rows"][0]["Symbol"] == "GBPUSD"
    assert details["cache"] == {
        "path": str(cache_path),
        "name": cache_path.name,
        "sizeBytes": cache_path.stat().st_size,
        "modifiedAt": details["cache"]["modifiedAt"],
        "pattern": "MyEA.all_symbols.M15.20210525.20260525.*.opt",
        "rowCount": 2,
        "expert": "MyEA",
        "symbolComponent": "all_symbols",
        "period": "M15",
        "fromDate": "20210525",
        "toDate": "20260525",
        "build": "22",
        "cacheHash": "ABCDEF",
    }


def test_parse_all_symbols_from_agent_logs_prefers_history_variant(tmp_path):
    tester_dir = tmp_path / "Tester"
    logs_dir = tester_dir / "Agent-127.0.0.1-3001" / "logs"
    logs_dir.mkdir(parents=True)
    cache_path = tmp_path / "MyEA.all_symbols.M15.20210525.20260525.22.ABCDEF.opt"
    cache_path.write_bytes(b"test")

    cache_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
    time_prefix = cache_time.strftime("%H:%M:%S") + ".000"
    log_path = logs_dir / cache_time.strftime("%Y%m%d.log")
    log_path.write_bytes(
        (
            f"CS\t0\t{time_prefix}\tTester\toptimization pass 7 started\r\n"
            f"CS\t0\t{time_prefix}\tSymbols\tUSDJPY: symbol to be synchronized\r\n"
            f"CS\t0\t{time_prefix}\tHistory\tGBPJPY,M15: history synchronized\r\n"
        ).encode("utf-16-le")
    )

    rows = cache_parser.parse_all_symbols_from_agent_logs(cache_path, tester_dir)

    assert rows == [{"pass": 7, "symbol": "GBPJPY", "source": "history"}]


def test_parse_cache_results_returns_empty_when_cache_missing(tmp_path):
    terminal_dir = tmp_path / "terminal"
    terminal_dir.mkdir()
    ini_path = tmp_path / "normalized.ini"
    ini_path.write_text("[Tester]\nExpert=Uploaded\\MyEA\n", encoding="utf-8")

    assert cache_parser.parse_cache_results(str(ini_path), str(terminal_dir), top_n=50) == []