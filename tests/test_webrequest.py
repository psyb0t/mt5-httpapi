"""Tests for the WebRequest allowlist codec + manager + endpoint.

Runs entirely on Linux (no MT5/Windows): the codec is pure Python and the
manager is plain file I/O. The live apply (terminal restart writing common.ini)
is exercised via a stubbed restart_terminal.
"""
from __future__ import annotations

import json
import os

import pytest
from flask import Flask

from mt5api.chartctl import webrequest as wr

# A real captured broker blob and its plaintext URLs (verified byte-identical).
REAL_BLOB = (
    "13E33B7856715A56F2822DF172EBC0D0BD8DF23E89A42B2716A602C68D06506070409EEA021DA6A29"
    "525874B0D86E7F7D8A8FC4898B30E0A3DCDFBBF9D166474552580CC55704642FD8D1DE13AB3CADA08D8"
    "DC28AFCA0C080292FABED14A1828BA8A1B67BCD700FC69F9D094E55E2A3AAE7E17637D986B67D868511"
    "562DB"
)
REAL_URLS = ["https://tracker.algotradingspace.com", "https://api.telegram.org"]

_BOM = b"\xff\xfe"


def _write_utf16_ini(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(_BOM + text.replace("\n", "\r\n").encode("utf-16-le"))


# ── codec ────────────────────────────────────────────────────────────
def test_codec_roundtrip_real_blob():
    assert wr.decode_blob(REAL_BLOB) == REAL_URLS
    assert wr.encode_urls(REAL_URLS) == REAL_BLOB


def test_codec_roundtrip_arbitrary():
    urls = ["https://api.example.com", "http://nfs.faireconomy.media/x.xml"]
    assert wr.decode_blob(wr.encode_urls(urls)) == urls


# ── url hygiene ──────────────────────────────────────────────────────
def test_clean_urls_filters_and_dedupes():
    got = wr.clean_urls([
        " https://a.com ", "https://a.com", "ftp://no.com",
        "https://b.com;evil", "", 42, "HTTPS://C.com",
    ])
    assert got == ["https://a.com", "HTTPS://C.com"]


# ── desired store ────────────────────────────────────────────────────
def test_desired_store_roundtrip(tmp_path):
    cfg = str(tmp_path / "Config")
    assert wr.load_desired(cfg) is None
    wr.save_desired(cfg, REAL_URLS)
    assert wr.load_desired(cfg) == REAL_URLS
    with open(wr.desired_path(cfg)) as f:
        assert json.load(f)["urls"] == REAL_URLS


# ── migrate-from-current ─────────────────────────────────────────────
def test_read_current_urls_from_existing_common_ini(tmp_path):
    cfg = str(tmp_path / "Config")
    _write_utf16_ini(
        wr.common_ini_path(cfg),
        f"[Charts]\nProfileLast=Default\n[Experts]\nAllowDllImport=1\n"
        f"WebRequest=1\nWebRequestUrl={REAL_BLOB}\n[Objects]\nShow=0\n",
    )
    assert wr.read_current_urls(cfg) == REAL_URLS


def test_read_current_urls_absent(tmp_path):
    assert wr.read_current_urls(str(tmp_path / "Config")) == []


def test_effective_prefers_desired_then_migrates(tmp_path):
    cfg = str(tmp_path / "Config")
    _write_utf16_ini(
        wr.common_ini_path(cfg),
        f"[Experts]\nWebRequest=1\nWebRequestUrl={REAL_BLOB}\n",
    )
    # no desired yet -> migrate from current
    assert wr.effective_urls(cfg) == REAL_URLS
    wr.save_desired(cfg, ["https://only.example.com"])
    assert wr.effective_urls(cfg) == ["https://only.example.com"]


# ── write_common_ini: preserve everything, upsert the two keys ───────
def test_write_common_ini_preserves_other_sections(tmp_path):
    cfg = str(tmp_path / "Config")
    _write_utf16_ini(
        wr.common_ini_path(cfg),
        "[Charts]\nProfileLast=Default\n[Experts]\nAllowDllImport=1\n"
        "WebRequest=0\nWebRequestUrl=DEADBEEF\n[Objects]\nShow=0\n",
    )
    new = ["https://api.example.com"]
    wr.write_common_ini(cfg, new)
    lines = wr._read_lines(wr.common_ini_path(cfg))
    assert "[Charts]" in lines and "ProfileLast=Default" in lines
    assert "[Objects]" in lines and "Show=0" in lines
    assert "AllowDllImport=1" in lines           # sibling key preserved
    assert "WebRequest=1" in lines               # flipped on
    assert wr.read_current_urls(cfg) == new       # blob replaced, decodes to new
    # exactly one WebRequestUrl line
    assert sum(l.strip().lower().startswith("webrequesturl=") for l in lines) == 1


def test_write_common_ini_creates_file_and_section(tmp_path):
    cfg = str(tmp_path / "Config")
    wr.write_common_ini(cfg, REAL_URLS)
    assert os.path.exists(wr.common_ini_path(cfg))
    with open(wr.common_ini_path(cfg), "rb") as f:
        assert f.read(2) == _BOM                 # UTF-16LE BOM preserved
    assert wr.read_current_urls(cfg) == REAL_URLS


def test_write_common_ini_is_utf16(tmp_path):
    cfg = str(tmp_path / "Config")
    wr.write_common_ini(cfg, REAL_URLS)
    with open(wr.common_ini_path(cfg), "rb") as f:
        raw = f.read()
    assert b"\x00" in raw                        # wide chars
    assert raw.decode("utf-16").count("[Experts]") == 1


def test_apply_from_desired(tmp_path):
    term = str(tmp_path / "term")
    cfg = wr.config_dir(term)
    assert wr.apply_from_desired(term) is None   # no desired -> no-op
    assert not os.path.exists(wr.common_ini_path(cfg))
    wr.save_desired(cfg, REAL_URLS)
    assert wr.apply_from_desired(term) == len(REAL_URLS)
    assert wr.read_current_urls(cfg) == REAL_URLS


# ── endpoint ─────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    from mt5api.handlers import webrequest as handler

    term = str(tmp_path / "term")
    os.makedirs(wr.config_dir(term), exist_ok=True)
    monkeypatch.setattr(handler, "TERMINAL_DIR", term)

    calls = {"restart": 0}

    def fake_restart():
        calls["restart"] += 1
        # emulate the real restart applying desired -> common.ini
        wr.apply_from_desired(term)
        return True

    monkeypatch.setattr(handler, "restart_terminal", fake_restart)

    app = Flask(__name__)
    app.get("/webrequest")(handler.get_webrequest)
    app.put("/webrequest")(handler.put_webrequest)
    c = app.test_client()
    c._term = term
    c._calls = calls
    return c


def test_get_empty(client):
    r = client.get("/webrequest")
    assert r.status_code == 200 and r.get_json() == {"urls": []}


def test_put_replace_restarts_and_applies(client):
    r = client.put("/webrequest", json={"urls": REAL_URLS})
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "urls": REAL_URLS}
    assert client._calls["restart"] == 1
    # persisted + written to common.ini
    assert wr.load_desired(wr.config_dir(client._term)) == REAL_URLS
    assert wr.read_current_urls(wr.config_dir(client._term)) == REAL_URLS
    # readable back through GET
    assert client.get("/webrequest").get_json()["urls"] == REAL_URLS


def test_put_add_remove_migrates_from_current(client):
    cfg = wr.config_dir(client._term)
    _write_utf16_ini(wr.common_ini_path(cfg),
                     f"[Experts]\nWebRequest=1\nWebRequestUrl={REAL_BLOB}\n")
    r = client.put("/webrequest", json={
        "add": ["https://new.example.com"],
        "remove": ["https://api.telegram.org"],
    })
    assert r.status_code == 200
    assert r.get_json()["urls"] == [
        "https://tracker.algotradingspace.com", "https://new.example.com",
    ]


def test_put_rejects_non_dict(client):
    assert client.put("/webrequest", data="nope").status_code == 400


def test_put_rejects_empty_body(client):
    assert client.put("/webrequest", json={}).status_code == 400


def test_put_restart_failure_returns_500(client, monkeypatch):
    from mt5api.handlers import webrequest as handler
    monkeypatch.setattr(handler, "restart_terminal", lambda: False)
    r = client.put("/webrequest", json={"urls": REAL_URLS})
    assert r.status_code == 500
    # desired still persisted (next boot/restart will apply it)
    assert wr.load_desired(wr.config_dir(client._term)) == REAL_URLS


# ── config_helper boot-seed (start.bat deletes common.ini every boot) ─
def _load_config_helper():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "scripts", "config_helper.py")
    spec = importlib.util.spec_from_file_location("config_helper_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_config_helper_write_ini_seeds_common_ini(tmp_path, monkeypatch):
    ch = _load_config_helper()
    cfg_yaml = tmp_path / "config.yaml"
    cfg_yaml.write_text(
        "chartctl:\n  enabled: true\n"
        "accounts:\n  testbroker:\n    acct1:\n"
        "      login: 123\n      server: TestServer\n      password: pw\n"
        "terminals:\n  - broker: testbroker\n    account: acct1\n"
        "    instance: default\n    port: 6542\n    mode: live\n"
    )
    monkeypatch.setattr(ch, "CONFIG_PATH", str(cfg_yaml))

    term = tmp_path / "term"
    (term / "Config").mkdir(parents=True)
    outpath = term / "mt5start.ini"
    # simulate a terminal that already has a desired allowlist persisted
    wr.save_desired(str(term / "Config"), REAL_URLS)

    monkeypatch.setattr(
        ch.sys, "argv",
        ["config_helper.py", "write_ini", "testbroker", "acct1", str(outpath),
         "default", "live"],
    )
    ch.main()

    # mt5start.ini written with the loader StartUp block (chartctl on)
    start_ini = outpath.read_text()
    assert "[StartUp]" in start_ini and "MT5ChartLoader" in start_ini
    # common.ini re-emitted from the desired file, decodes back to the URLs
    assert wr.read_current_urls(str(term / "Config")) == REAL_URLS


def test_config_helper_write_ini_no_desired_no_common_ini(tmp_path, monkeypatch):
    ch = _load_config_helper()
    cfg_yaml = tmp_path / "config.yaml"
    cfg_yaml.write_text(
        "chartctl:\n  enabled: true\n"
        "accounts:\n  testbroker:\n    acct1:\n"
        "      login: 123\n      server: TestServer\n      password: pw\n"
        "terminals:\n  - broker: testbroker\n    account: acct1\n"
        "    instance: default\n    port: 6542\n    mode: live\n"
    )
    monkeypatch.setattr(ch, "CONFIG_PATH", str(cfg_yaml))
    term = tmp_path / "term"
    (term / "Config").mkdir(parents=True)
    outpath = term / "mt5start.ini"
    monkeypatch.setattr(
        ch.sys, "argv",
        ["config_helper.py", "write_ini", "testbroker", "acct1", str(outpath),
         "default", "live"],
    )
    ch.main()
    # no desired file -> boot-seed is a no-op, common.ini left absent
    assert not os.path.exists(wr.common_ini_path(str(term / "Config")))
