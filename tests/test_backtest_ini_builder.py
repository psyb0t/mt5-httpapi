"""Unit tests for mt5api.backtest.ini_builder."""
from __future__ import annotations

import configparser
from datetime import date

import pytest

from mt5api.backtest import ini_builder


def _parse(text):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(text)
    return parser


def _base(**overrides):
    params = {
        "symbol": "NZDJPY",
        "timeframe": "M15",
        "expert": "MyEA.ex5",
        "lastYears": 5,
    }
    params.update(overrides)
    return params


def test_happy_path_minimum_params():
    text = ini_builder.build_ini(_base())
    parser = _parse(text)
    tester = parser["Tester"]
    assert tester["Symbol"] == "NZDJPY"
    assert tester["Period"] == "M15"
    assert tester["Expert"] == "Uploaded\\MyEA"
    assert tester["Model"] == "2"  # open-prices default
    assert tester["ExecutionMode"] == "0"
    assert tester["Optimization"] == "0"
    assert tester["Visual"] == "0"
    assert tester["ShutdownTerminal"] == "1"
    assert tester["ReplaceReport"] == "1"
    assert tester["UseLocal"] == "1"
    assert tester["UseRemote"] == "0"
    assert tester["UseCloud"] == "0"
    assert tester["Report"].startswith("Reports\\")
    assert "ExpertParameters" not in tester
    # Common stays empty — runner injects credentials.
    assert parser["Common"]["Login"] == ""


def test_last_years_window():
    text = ini_builder.build_ini(_base(lastYears=5))
    parser = _parse(text)
    to_str = parser["Tester"]["ToDate"]
    from_str = parser["Tester"]["FromDate"]
    to_d = date.fromisoformat(to_str.replace(".", "-"))
    from_d = date.fromisoformat(from_str.replace(".", "-"))
    delta_years = to_d.year - from_d.year
    assert delta_years == 5
    assert to_d == date.today() or (to_d - date.today()).days in (-1, 0, 1)


def test_explicit_dates():
    text = ini_builder.build_ini({
        "symbol": "EURUSD",
        "timeframe": "H1",
        "expert": "EA.ex5",
        "fromDate": "2020-01-01",
        "toDate": "2024-06-30",
    })
    tester = _parse(text)["Tester"]
    assert tester["FromDate"] == "2020.01.01"
    assert tester["ToDate"] == "2024.06.30"


def test_modelling_enum_mapping():
    cases = {
        "every-tick": "0",
        "1m-ohlc": "1",
        "open-prices": "2",
        "real-ticks": "4",
    }
    for name, expected in cases.items():
        text = ini_builder.build_ini(_base(modelling=name))
        assert _parse(text)["Tester"]["Model"] == expected


def test_latency_and_set_file():
    text = ini_builder.build_ini(_base(
        latencyMs=5,
        expertParameters="myparams.set",
    ))
    tester = _parse(text)["Tester"]
    assert tester["ExecutionMode"] == "5"
    assert tester["ExpertParameters"] == "myparams.set"


def test_deposit_currency_leverage():
    text = ini_builder.build_ini(_base(
        deposit=25000,
        currency="eur",
        leverage=200,
    ))
    tester = _parse(text)["Tester"]
    assert tester["Deposit"] == "25000"
    assert tester["Currency"] == "EUR"
    assert tester["Leverage"] == "1:200"


def test_report_name_normalization():
    text = ini_builder.build_ini(_base(reportName="custom"))
    assert _parse(text)["Tester"]["Report"] == "Reports\\custom.htm"


def test_optimization_report_name_normalization():
    text = ini_builder.build_ini(_base(optimization=2, reportName="custom"))
    assert _parse(text)["Tester"]["Report"] == "Reports\\custom.xml"


def test_optimization_modes_are_preserved():
    assert _parse(ini_builder.build_ini(_base(optimization=2)))["Tester"]["Optimization"] == "2"
    assert _parse(ini_builder.build_ini(_base(optimization=3)))["Tester"]["Optimization"] == "3"


def test_invalid_optimization_raises():
    with pytest.raises(ValueError, match="optimization"):
        ini_builder.build_ini(_base(optimization=4))
    with pytest.raises(ValueError, match="optimization"):
        ini_builder.build_ini(_base(optimization=-1))
    with pytest.raises(ValueError, match="optimization"):
        ini_builder.build_ini(_base(optimization="fast"))


def test_optimization_criterion_defaults_and_overrides():
    assert _parse(ini_builder.build_ini(_base()))["Tester"]["OptimizationCriterion"] == "0"
    assert _parse(ini_builder.build_ini(_base(optimizationCriterion=5)))["Tester"]["OptimizationCriterion"] == "5"


def test_invalid_optimization_criterion_raises():
    with pytest.raises(ValueError, match="optimizationCriterion"):
        ini_builder.build_ini(_base(optimizationCriterion=8))


def test_missing_symbol_raises():
    with pytest.raises(ValueError, match="symbol"):
        ini_builder.build_ini({"timeframe": "M15", "expert": "EA.ex5", "lastDays": 30})


def test_invalid_timeframe_raises():
    with pytest.raises(ValueError, match="timeframe"):
        ini_builder.build_ini(_base(timeframe="M7"))


def test_invalid_modelling_raises():
    with pytest.raises(ValueError, match="modelling"):
        ini_builder.build_ini(_base(modelling="bogus"))


def test_no_date_window_raises():
    with pytest.raises(ValueError, match="Date window required"):
        ini_builder.build_ini({"symbol": "EURUSD", "timeframe": "M15", "expert": "EA.ex5"})


def test_overspecified_date_window_raises():
    with pytest.raises(ValueError, match="over-specified"):
        ini_builder.build_ini({
            "symbol": "EURUSD",
            "timeframe": "M15",
            "expert": "EA.ex5",
            "lastDays": 30,
            "lastYears": 1,
        })


def test_inverted_dates_raises():
    with pytest.raises(ValueError, match="must be on or before"):
        ini_builder.build_ini({
            "symbol": "EURUSD",
            "timeframe": "M15",
            "expert": "EA.ex5",
            "fromDate": "2024-12-31",
            "toDate": "2020-01-01",
        })


def test_expert_must_end_with_ex5():
    with pytest.raises(ValueError, match=".ex5"):
        ini_builder.build_ini(_base(expert="MyEA.dll"))


def test_expert_path_traversal_rejected():
    with pytest.raises(ValueError, match="filename"):
        ini_builder.build_ini(_base(expert="../escape.ex5"))


def test_set_must_end_with_set():
    with pytest.raises(ValueError, match=".set"):
        ini_builder.build_ini(_base(expertParameters="params.txt"))


def test_negative_last_days_rejected():
    with pytest.raises(ValueError, match="positive"):
        ini_builder.build_ini({"symbol": "X", "timeframe": "M15", "expert": "EA.ex5", "lastDays": -1})
