"""Build an MT5 Strategy Tester INI from validated JSON parameters.

Pure-Python, no MT5 SDK dependency. The runner (handler.py) is responsible
for injecting [Common] credentials from config.yaml; the builder leaves
[Common] empty so this module stays callable in isolation and from tests.

The INI text returned here is the canonical UTF-8 form. The runner
re-encodes it as UTF-16-LE with BOM before MT5 reads it, because MT5's
Strategy Tester silently rejects [Tester] Login under UTF-8.
"""
from __future__ import annotations

import io
from configparser import ConfigParser
from datetime import date, datetime, timedelta, timezone

# MT5 Strategy Tester modelling modes.
#   0 = Every tick
#   1 = 1 minute OHLC
#   2 = Open prices only
#   4 = Every tick based on real ticks
MODELLING_MAP = {
    "every-tick": 0,
    "1m-ohlc": 1,
    "open-prices": 2,
    "real-ticks": 4,
}

VALID_TIMEFRAMES = (
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20",
    "M30", "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
)

# MT5 reads tester dates as "YYYY.MM.DD".
_DATE_FMT_OUT = "%Y.%m.%d"
_DATE_FMT_IN = "%Y-%m-%d"


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _parse_iso_date(value, field):
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a YYYY-MM-DD string")
    try:
        return datetime.strptime(value, _DATE_FMT_IN).date()
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD ({value!r})") from exc


def _resolve_dates(params):
    has_explicit = "fromDate" in params or "toDate" in params
    has_last_years = "lastYears" in params
    has_last_days = "lastDays" in params

    set_count = sum([has_explicit, has_last_years, has_last_days])
    if set_count == 0:
        raise ValueError(
            "Date window required: provide fromDate+toDate, lastYears, or lastDays"
        )
    if set_count > 1:
        raise ValueError(
            "Date window over-specified: pick one of fromDate+toDate, lastYears, lastDays"
        )

    if has_explicit:
        if "fromDate" not in params or "toDate" not in params:
            raise ValueError("fromDate and toDate must be provided together")
        from_d = _parse_iso_date(params["fromDate"], "fromDate")
        to_d = _parse_iso_date(params["toDate"], "toDate")
    else:
        to_d = _today_utc()
        if has_last_years:
            n = params["lastYears"]
            if not isinstance(n, int) or n <= 0:
                raise ValueError("lastYears must be a positive integer")
            try:
                from_d = to_d.replace(year=to_d.year - n)
            except ValueError:
                # e.g. Feb 29 minus N years where target year is non-leap.
                from_d = to_d.replace(year=to_d.year - n, day=28)
        else:
            n = params["lastDays"]
            if not isinstance(n, int) or n <= 0:
                raise ValueError("lastDays must be a positive integer")
            from_d = to_d - timedelta(days=n)

    if from_d > to_d:
        raise ValueError(f"fromDate ({from_d}) must be on or before toDate ({to_d})")
    return from_d, to_d


def _resolve_modelling(params):
    raw = params.get("modelling", "open-prices")
    if raw in MODELLING_MAP:
        return MODELLING_MAP[raw]
    if isinstance(raw, int) and raw in MODELLING_MAP.values():
        return raw
    raise ValueError(
        f"Invalid modelling: {raw!r}. Use one of {sorted(MODELLING_MAP)}"
    )


def _resolve_optimization(params):
    raw = params.get("optimization", 0)
    if isinstance(raw, bool):
        raw = int(raw)
    if not isinstance(raw, int):
        raise ValueError("optimization must be an integer 0..3")
    if raw not in (0, 1, 2, 3):
        raise ValueError("optimization must be 0..3")
    return raw


def _resolve_optimization_criterion(params):
    raw = params.get("optimizationCriterion", 0)
    if isinstance(raw, bool):
        raw = int(raw)
    if not isinstance(raw, int):
        raise ValueError("optimizationCriterion must be an integer 0..7")
    if raw not in range(8):
        raise ValueError("optimizationCriterion must be 0..7")
    return raw


def _strip_ex5(name):
    return name[:-4] if name.lower().endswith(".ex5") else name


def _require_filename(value, field):
    import os
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    name = value.strip()
    if name != os.path.basename(name):
        raise ValueError(f"{field} must be a filename, not a path")
    return name


def _validate_timeframe(value):
    if value not in VALID_TIMEFRAMES:
        raise ValueError(
            f"Invalid timeframe: {value!r}. Use one of {list(VALID_TIMEFRAMES)}"
        )
    return value


def _positive_number(value, field, *, allow_zero=False, kind=float):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    if (allow_zero and value < 0) or (not allow_zero and value <= 0):
        raise ValueError(f"{field} must be {'>= 0' if allow_zero else '> 0'}")
    return kind(value)


def build_ini(params: dict) -> str:
    """Return INI text for the MT5 Strategy Tester from JSON params.

    Required: symbol, timeframe, expert, and a date window
    (fromDate+toDate OR lastYears OR lastDays).
    Optional: modelling, latencyMs, deposit, currency, leverage,
    expertParameters, reportName, optimization, optimizationCriterion,
    forwardMode, visual.
    """
    if not isinstance(params, dict):
        raise ValueError("params must be an object")

    symbol = params.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required")
    symbol = symbol.strip()

    timeframe = _validate_timeframe(params.get("timeframe"))
    expert_filename = _require_filename(params.get("expert"), "expert")
    if not expert_filename.lower().endswith(".ex5"):
        raise ValueError("expert must end with .ex5")

    set_filename = ""
    if params.get("expertParameters"):
        set_filename = _require_filename(params["expertParameters"], "expertParameters")
        if not set_filename.lower().endswith(".set"):
            raise ValueError("expertParameters must end with .set")

    from_d, to_d = _resolve_dates(params)
    model = _resolve_modelling(params)
    optimization = _resolve_optimization(params)
    optimization_criterion = _resolve_optimization_criterion(params)

    deposit = _positive_number(params.get("deposit", 10000), "deposit", kind=float)
    leverage = _positive_number(params.get("leverage", 100), "leverage", kind=int)
    latency_ms = _positive_number(
        params.get("latencyMs", 0), "latencyMs", allow_zero=True, kind=int
    )
    currency = params.get("currency", "USD")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("currency must be a non-empty string")
    currency = currency.strip().upper()

    default_report_name = (
        "optimization-report.xml" if optimization else "backtest-report.htm"
    )
    report_name = params.get("reportName", default_report_name)
    if not isinstance(report_name, str) or not report_name.strip():
        raise ValueError("reportName must be a non-empty string")
    report_name = report_name.strip()
    if optimization:
        if not report_name.lower().endswith(".xml"):
            report_name += ".xml"
    elif not report_name.lower().endswith((".htm", ".html")):
        report_name += ".htm"

    forward_mode = params.get("forwardMode", 0)
    if forward_mode not in (0, 1, 2, 3, 4):
        raise ValueError("forwardMode must be 0..4")
    visual = int(bool(params.get("visual", 0)))

    parser = ConfigParser()
    parser.optionxform = str  # preserve key casing — MT5 is case-sensitive.

    parser["Common"] = {
        # Login/Password/Server are injected by the runner from config.yaml.
        "Login": "",
        "Password": "",
        "Server": "",
        "KeepPrivate": "0",
        "AutoTrading": "1",
        "NewsEnable": "0",
    }
    parser["Experts"] = {
        "AllowLiveTrading": "1",
        "AllowDllImport": "1",
        "Enabled": "1",
    }
    parser["Tester"] = {
        "Expert": f"Uploaded\\{_strip_ex5(expert_filename)}",
        "Symbol": symbol,
        "Period": timeframe,
        "Deposit": str(int(deposit)) if deposit.is_integer() else f"{deposit:.2f}",
        "Currency": currency,
        "Leverage": f"1:{leverage}",
        "Model": str(model),
        "ExecutionMode": str(latency_ms),
        "Optimization": str(optimization),
        "OptimizationCriterion": str(optimization_criterion),
        "ForwardMode": str(forward_mode),
        "FromDate": from_d.strftime(_DATE_FMT_OUT),
        "ToDate": to_d.strftime(_DATE_FMT_OUT),
        "Report": f"Reports\\{report_name}",
        "ReplaceReport": "1",
        "ShutdownTerminal": "1",
        "Visual": str(visual),
        "UseLocal": "1",
        "UseRemote": "0",
        "UseCloud": "0",
        "AllowDll": "1",
    }
    if set_filename:
        parser["Tester"]["ExpertParameters"] = set_filename

    buffer = io.StringIO()
    parser.write(buffer, space_around_delimiters=False)
    return buffer.getvalue()
