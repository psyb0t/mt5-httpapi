"""Unit tests for chartctl: path safety, set parsing, tpl generation,
registry desired-state + status derivation.

All Linux-safe: no MT5 SDK, no Windows. The registry/paths modules are
repointed at a tmp dir per test via monkeypatch, mirroring the backtest
jobs test fixture.
"""
from __future__ import annotations

import json
import os

import pytest


# ── paths.safe_name ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "  ", "..", ".", "a/b.ex5", "a\\b.ex5", "../evil.ex5",
    "C:\\evil.ex5", "\\\\host\\share\\x.ex5", ".hidden.ex5",
    "na<me>.ex5", 'quote".ex5', "pipe|.ex5", "null\x00.ex5",
])
def test_safe_name_rejects(bad):
    from mt5api.chartctl import paths
    with pytest.raises(ValueError):
        paths.safe_name(bad, "expert", ".ex5")


def test_safe_name_accepts_and_checks_ext():
    from mt5api.chartctl import paths
    assert paths.safe_name("HappyGoldScalp.ex5", "expert", ".ex5") \
        == "HappyGoldScalp.ex5"
    with pytest.raises(ValueError):
        paths.safe_name("HappyGoldScalp.set", "expert", ".ex5")


# ── setparse ─────────────────────────────────────────────────────────

def test_parse_plain_and_optimized_set():
    from mt5api.chartctl.setparse import parse_set_text
    text = (
        "; comment line\n"
        "Lots=0.10\n"
        "StopLoss=50||10||5||100||Y\n"
        "UseTrailing=1\n"
    )
    got = parse_set_text(text)
    assert got[0] == {"name": "Lots", "value": "0.10"}
    assert got[1]["name"] == "StopLoss"
    assert got[1]["value"] == "50"
    assert got[1]["optimize"] is True
    assert got[1]["start"] == "10" and got[1]["stop"] == "100"
    assert got[2] == {"name": "UseTrailing", "value": "1"}


def test_parse_utf16_bytes():
    from mt5api.chartctl.setparse import parse_set_bytes
    raw = "Lots=0.01\nMagic=12345\n".encode("utf-16")
    got = parse_set_bytes(raw)
    assert {"name": "Lots", "value": "0.01"} in got
    assert {"name": "Magic", "value": "12345"} in got


def test_parse_ascii_bytes_even_length():
    # Even-length ASCII input decodes "successfully" as utf-16 garbage if
    # utf-16 is blind-tried first, silently yielding zero inputs — and the
    # deployment would run on EA defaults. Regression for that decode bug.
    from mt5api.chartctl.setparse import parse_set_bytes
    raw = b"; comment\r\nLots=0.10\r\nMagic=99\r\n"
    assert len(raw) % 2 == 0
    got = parse_set_bytes(raw)
    assert {"name": "Lots", "value": "0.10"} in got
    assert {"name": "Magic", "value": "99"} in got


def test_parse_bomless_utf16_bytes():
    from mt5api.chartctl.setparse import parse_set_bytes
    raw = "Lots=0.01\nMagic=12345\n".encode("utf-16-le")  # no BOM
    got = parse_set_bytes(raw)
    assert {"name": "Lots", "value": "0.01"} in got
    assert {"name": "Magic", "value": "12345"} in got


# ── tpl_builder ──────────────────────────────────────────────────────

def test_tpl_text_structure():
    from mt5api.chartctl import tpl_builder
    text = tpl_builder.build_tpl_text(
        deployment_id="dep_abc123",
        expert_name="HappyGoldScalp",
        expert_rel_path="Experts\\Uploaded\\HappyGoldScalp.ex5",
        inputs=[{"name": "Lots", "value": "0.10"},
                {"name": "Magic", "value": "777"}],
        terminal_build=4620,
    )
    assert "<chart>" in text and "</chart>" in text
    assert "<expert>" in text and "</expert>" in text
    assert "name=HappyGoldScalp" in text
    assert "path=Experts\\Uploaded\\HappyGoldScalp.ex5" in text
    assert "expertmode=1" in text
    assert "__chartctl_id=dep_abc123" in text     # attribution input
    assert "Lots=0.10" in text and "Magic=777" in text
    assert "build=4620" in text                   # forensic stamp
    assert text.endswith("\r\n")


def test_tpl_written_as_utf16_with_bom(tmp_path, monkeypatch):
    from mt5api.chartctl import tpl_builder, paths
    monkeypatch.setattr(paths, "TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(tpl_builder, "TEMPLATES_DIR", str(tmp_path))
    path = tpl_builder.write_tpl(
        deployment_id="dep_x", expert_name="EA",
        expert_rel_path="Experts\\Uploaded\\EA.ex5", inputs=[])
    data = open(path, "rb").read()
    assert data[:2] == b"\xff\xfe"                # UTF-16-LE BOM
    assert "name=EA" in data.decode("utf-16")


# ── registry ─────────────────────────────────────────────────────────

@pytest.fixture
def reg(monkeypatch, tmp_path):
    from mt5api.chartctl import registry, paths
    monkeypatch.setattr(paths, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(paths, "DESIRED_PATH", str(tmp_path / "desired.json"))
    monkeypatch.setattr(paths, "OBSERVED_PATH", str(tmp_path / "observed.json"))
    monkeypatch.setattr(paths, "PROTOCOL_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(registry, "_STATE", None)
    return registry, tmp_path


def test_add_bumps_revision_and_writes_desired(reg):
    registry, tmp = reg
    d = registry.add_deployment(
        expert_file="EA.ex5", expert_name="EA", set_file=None,
        symbol="XAUUSD", timeframe="M5")
    assert d["id"].startswith("dep_")
    assert registry.current_revision() == 1
    desired = json.loads((tmp / "desired.json").read_text())
    assert desired["revision"] == 1
    assert desired["deployments"][0]["symbol"] == "XAUUSD"
    assert desired["deployments"][0]["template"].startswith("\\Files\\chartctl\\")


def test_duplicate_enabled_chart_rejected(reg):
    registry, _ = reg
    registry.add_deployment(expert_file="A.ex5", expert_name="A",
                            set_file=None, symbol="EURUSD", timeframe="H1")
    with pytest.raises(registry.DuplicateChart):
        registry.add_deployment(expert_file="B.ex5", expert_name="B",
                                set_file=None, symbol="EURUSD", timeframe="H1")


def test_disabled_does_not_conflict(reg):
    registry, _ = reg
    registry.add_deployment(expert_file="A.ex5", expert_name="A",
                            set_file=None, symbol="EURUSD", timeframe="H1",
                            enabled=False)
    # Same slot, enabled — must be allowed since the first is paused.
    registry.add_deployment(expert_file="B.ex5", expert_name="B",
                            set_file=None, symbol="EURUSD", timeframe="H1")


def test_persistence_across_reload(reg):
    registry, tmp = reg
    registry.add_deployment(expert_file="EA.ex5", expert_name="EA",
                            set_file=None, symbol="GBPUSD", timeframe="M15")
    registry._STATE = None            # simulate API restart
    deps = registry.list_deployments()
    assert len(deps) == 1 and deps[0]["symbol"] == "GBPUSD"


def test_remove_bumps_and_clears(reg):
    registry, _ = reg
    d = registry.add_deployment(expert_file="EA.ex5", expert_name="EA",
                                set_file=None, symbol="USDJPY", timeframe="H4")
    registry.remove_deployment(d["id"])
    assert registry.list_deployments() == []
    assert registry.current_revision() == 2


def test_merged_view_status_pending_without_observed(reg):
    registry, _ = reg
    registry.add_deployment(expert_file="EA.ex5", expert_name="EA",
                            set_file=None, symbol="XAUUSD", timeframe="M5")
    view = registry.merged_view()
    assert view["deployments"][0]["status"] == "pending"
    assert view["converged"] is False
    assert view["observed_stale"] is True


def test_merged_view_running_when_observed_matches(reg):
    registry, tmp = reg
    d = registry.add_deployment(expert_file="EA.ex5", expert_name="EA",
                                set_file=None, symbol="XAUUSD", timeframe="M5")
    observed = {
        "loader": {"applied_revision": registry.current_revision(),
                   "last_loop": "now"},
        "terminal": {"auto_trading": True},
        "charts": [{"chart_id": 1, "expert": "EA", "deployment_id": d["id"]}],
        "deployments": [{"id": d["id"], "status": "running", "chart_id": 1}],
    }
    (tmp / "observed.json").write_text(json.dumps(observed))
    view = registry.merged_view()
    assert view["deployments"][0]["status"] == "running"
