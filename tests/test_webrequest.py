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
    from mt5api.chartctl import autoit_webrequest as autoit
    from mt5api.chartctl import webrequest_apply as wa
    from mt5api.handlers import webrequest as handler

    term = str(tmp_path / "term")
    os.makedirs(wr.config_dir(term), exist_ok=True)
    monkeypatch.setattr(handler, "TERMINAL_DIR", term)
    monkeypatch.setattr(wa, "TERMINAL_DIR", term)
    monkeypatch.setattr(handler, "_last_verify", [0.0])

    calls = {"restart": 0}

    def fake_restart():
        calls["restart"] += 1
        # emulate the real restart applying desired -> common.ini
        wr.apply_from_desired(term)
        return True

    monkeypatch.setattr(handler, "restart_terminal", fake_restart)

    # default: bare metal (no window driver, no AutoIt) unless a test flips it
    monkeypatch.setattr(wa, "available", lambda: False)
    monkeypatch.setattr(wa, "backtest_running", lambda: False)
    monkeypatch.setattr(autoit, "available", lambda: False)

    app = Flask(__name__)
    app.get("/webrequest")(handler.get_webrequest)
    app.put("/webrequest")(handler.put_webrequest)
    app.post("/webrequest/apply")(handler.apply_webrequest)
    app.post("/webrequest/verify")(handler.verify_webrequest)
    c = app.test_client()
    c._term = term
    c._calls = calls
    c._autoit = autoit
    c._wa = wa
    return c


@pytest.fixture
def vm(client, monkeypatch):
    """The VM path: the terminal's windows are driven (here: a fake driver
    holding a live list), and the pid lookup finds one terminal."""
    from mt5api.chartctl import terminal_pid, winoptions

    live = {"urls": [], "enabled": False, "calls": [], "raise": None, "rejected": []}

    def fake_apply(pid, add, remove, *, exact=None, partial=False, wait_window=30.0):
        live["calls"].append({"pid": pid, "add": list(add), "remove": list(remove),
                              "exact": exact, "partial": partial})
        if live["raise"] is not None:
            raise live["raise"]
        before = list(live["urls"])
        if exact is not None:
            live["urls"] = [u for u in exact if u not in live["rejected"]]
            live["enabled"] = bool(exact)
        else:
            live["urls"] = [u for u in live["urls"] if u not in remove] + \
                [u for u in add if u not in live["urls"]]
            live["enabled"] = live["enabled"] or bool(add)
        return {"enabled": live["enabled"], "urls": list(live["urls"]),
                "added": [u for u in live["urls"] if u not in before],
                "removed": [u for u in before if u not in live["urls"]],
                "rejected": [u for u in (exact or []) if u in live["rejected"]],
                "changed": before != live["urls"]}

    monkeypatch.setattr(client._wa, "available", lambda: True)
    monkeypatch.setattr(client._wa, "_applied_generation", None)
    monkeypatch.setattr(terminal_pid, "_last_generation", None)
    monkeypatch.setattr(terminal_pid, "find_terminal", lambda *_a: (4242, 1.0))
    monkeypatch.setattr(winoptions, "apply", fake_apply)
    return live


def test_get_empty(client):
    r = client.get("/webrequest")
    assert r.status_code == 200 and r.get_json() == {
        "urls": [], "last_apply": None, "terminal_pid": None, "terminal_started_at": None,
        "stale": False}


def test_put_replace_restarts_and_applies(client):
    r = client.put("/webrequest", json={"urls": REAL_URLS})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True and body["urls"] == REAL_URLS
    assert body["applied_via"] == "restart"        # bare-metal fallback path
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


@pytest.mark.parametrize(
    "body",
    [
        {"urls": ["https://a.example", "https://bad example.com"]},
        {"urls": ["https://"]},
        {"add": ["ftp://a.example"]},
        {"remove": ["https://a.example;b"]},
        {"urls": [42]},
    ],
)
def test_put_refuses_a_url_the_driver_could_not_confirm(client, body):
    """A URL the Options-dialog driver would not count as a row reads as
    missing after the apply, so the whole apply is cancelled, every time,
    boot after boot. Refuse it before anything is stored."""
    r = client.put("/webrequest", json=body)

    assert r.status_code == 400
    assert r.get_json()["code"] == "INVALID_URL"
    assert r.get_json()["rejected"]
    assert wr.load_desired(wr.config_dir(client._term)) is None
    assert client._calls["restart"] == 0


def test_put_replace_drives_the_options_dialog_in_the_vm(client, vm):
    r = client.put("/webrequest", json={"urls": REAL_URLS})

    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True and body["applied_via"] == "win32:OK"
    assert vm["calls"][-1]["exact"] == REAL_URLS
    assert vm["calls"][-1]["pid"] == 4242
    assert client._calls["restart"] == 0            # no restart in the VM
    assert wr.load_desired(wr.config_dir(client._term)) == REAL_URLS
    last = client.get("/webrequest").get_json()["last_apply"]
    assert last["status"] == "OK" and last["reason"] == "put"


def test_a_patch_keeps_live_urls_nobody_named_and_stores_them(client, vm):
    """A user added a URL by hand in the VM; adding another through the API
    must not delete it, and it becomes part of the stored list."""
    vm["urls"] = ["https://hand.example"]
    vm["enabled"] = True

    r = client.put("/webrequest", json={"add": ["https://api.example"]})

    assert r.status_code == 200
    assert vm["calls"][-1]["exact"] is None
    assert vm["calls"][-1]["add"] == ["https://api.example"]
    assert vm["urls"] == ["https://hand.example", "https://api.example"]
    assert r.get_json()["urls"] == ["https://hand.example", "https://api.example"]
    assert wr.load_desired(wr.config_dir(client._term)) == [
        "https://hand.example", "https://api.example"]


def test_a_failed_patch_stores_the_intent_and_answers_500(client, vm):
    from mt5api.chartctl.winoptions import OptionsError

    wr.save_desired(wr.config_dir(client._term), ["https://a.example"])
    vm["raise"] = OptionsError("terminal_busy", "a dialog is open")

    r = client.put("/webrequest", json={"add": ["https://b.example"]})

    assert r.status_code == 409  # a dialog the user can close: try again
    assert "win32:FAIL:terminal_busy" in r.get_json()["error"]
    assert wr.load_desired(wr.config_dir(client._term)) == ["https://a.example", "https://b.example"]
    last = client.get("/webrequest").get_json()["last_apply"]
    assert last["status"] == "FAIL" and last["code"] == "terminal_busy"


def test_put_answers_409_while_a_backtest_runs(client, vm, monkeypatch):
    monkeypatch.setattr(client._wa, "backtest_running", lambda: True)

    r = client.put("/webrequest", json={"urls": REAL_URLS})

    assert r.status_code == 409
    assert vm["calls"] == []
    # stored all the same, so the re-apply carries it once the run ends
    assert wr.load_desired(wr.config_dir(client._term)) == REAL_URLS


def test_put_reports_an_ambiguous_terminal_pid(client, vm, monkeypatch):
    from mt5api.chartctl import terminal_pid

    def ambiguous():
        raise terminal_pid.TerminalPidError("pid_ambiguous", "two processes", {"pids": [1, 2]})

    monkeypatch.setattr(terminal_pid, "find_terminal", ambiguous)

    r = client.put("/webrequest", json={"urls": REAL_URLS})

    assert r.status_code == 500
    assert "pid_ambiguous" in r.get_json()["error"]
    assert vm["calls"] == []


def test_put_failure_in_the_vm_returns_500(client, vm):
    from mt5api.chartctl.winoptions import OptionsError

    vm["raise"] = OptionsError("list_not_changed", "did not take")
    r = client.put("/webrequest", json={"urls": REAL_URLS})
    assert r.status_code == 500
    # desired still persisted so a later /apply or boot can retry
    assert wr.load_desired(wr.config_dir(client._term)) == REAL_URLS


def test_apply_endpoint_reapplies_desired_and_reports_rejected_urls(client, vm):
    wr.save_desired(wr.config_dir(client._term), REAL_URLS)
    vm["rejected"] = [REAL_URLS[1]]

    r = client.post("/webrequest/apply")

    assert r.status_code == 200
    assert r.get_json()["urls"] == REAL_URLS
    assert r.get_json()["rejected"] == [REAL_URLS[1]]
    assert vm["calls"][-1]["exact"] == REAL_URLS
    assert vm["calls"][-1]["partial"] is True


def test_apply_endpoint_unknown_dev_script_is_a_400(client, monkeypatch):
    def refuse(script, timeout=60, use_runas=False):
        raise ValueError(f"script not found: {script}")

    monkeypatch.setattr(client._autoit, "available", lambda: True)
    monkeypatch.setattr(client._autoit, "run_named", refuse)

    r = client.post("/webrequest/apply?script=missing.au3")

    assert r.status_code == 400


def test_verify_reads_the_live_list_beside_the_stored_one(client, vm, monkeypatch):
    from mt5api import mt5client

    monkeypatch.setattr(mt5client, "LAST_TERMINAL_INFO", type("Info", (), {"build": 6257})())
    wr.save_desired(wr.config_dir(client._term), ["https://a.example"])
    vm["urls"] = ["https://a.example"]
    vm["enabled"] = True

    r = client.post("/webrequest/verify")

    assert r.status_code == 200
    assert r.get_json() == {"urls": ["https://a.example"],
                            "live": {"enabled": True, "urls": ["https://a.example"]},
                            "terminal_build": 6257}
    assert vm["calls"][-1] == {"pid": 4242, "add": [], "remove": [], "exact": None,
                               "partial": False}


def test_verify_answers_429_within_ten_seconds(client, vm):
    assert client.post("/webrequest/verify").status_code == 200

    again = client.post("/webrequest/verify")

    assert again.status_code == 429
    assert again.get_json()["code"] == "TOO_MANY_REQUESTS"
    assert 1 <= int(again.headers["Retry-After"]) <= 11
    assert len(vm["calls"]) == 1


def test_verify_answers_409_while_a_backtest_runs(client, vm, monkeypatch):
    monkeypatch.setattr(client._wa, "backtest_running", lambda: True)

    r = client.post("/webrequest/verify")

    assert r.status_code == 409
    assert r.get_json()["code"] == "BACKTEST_RUNNING"
    assert vm["calls"] == []


def test_verify_reports_a_driver_failure_with_its_code(client, vm):
    from mt5api.chartctl.winoptions import OptionsError

    vm["raise"] = OptionsError("terminal_busy", "a dialog is open", {"seen": "login"})

    r = client.post("/webrequest/verify")

    assert r.status_code == 409
    body = r.get_json()
    assert body["live"] is None and body["code"] == "TERMINAL_BUSY"
    assert body["diag"] == {"seen": "login"}


def test_verify_is_refused_on_bare_metal(client):
    r = client.post("/webrequest/verify")

    assert r.status_code == 409
    assert r.get_json()["code"] == "UNAVAILABLE"


def test_apply_endpoint_noop_when_empty(client):
    r = client.post("/webrequest/apply")
    assert r.status_code == 200
    assert r.get_json()["urls"] == []


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


# ── background re-apply (API start, terminal restart) ────────────────

class _SyncThread:
    """threading.Thread stand-in that runs the target when started."""

    def __init__(self, target, args=(), **_kwargs):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def _reapply_harness(monkeypatch, available, urls, statuses, backtest=None):
    from mt5api.chartctl import webrequest_apply as wa

    calls = []
    remaining = list(statuses)
    busy = list(backtest or [])
    monkeypatch.setattr(wa, "available", lambda: available)
    monkeypatch.setattr(wa.wr, "effective_urls", lambda _cfg: urls)
    monkeypatch.setattr(wa.threading, "Thread", _SyncThread)
    sleeps = []
    monkeypatch.setattr(wa.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(wa, "backtest_running", lambda: busy.pop(0) if busy else False)

    def fake_apply(applied, *, partial=False, reason="put"):
        calls.append((list(applied), partial, reason))
        return remaining.pop(0), ""

    monkeypatch.setattr(wa, "apply_urls", fake_apply)
    return wa, calls, sleeps


def test_reapply_retries_until_the_apply_succeeds(monkeypatch):
    wa, calls, _ = _reapply_harness(
        monkeypatch, True, ["https://a.example"], ["FAIL:terminal_busy", "OK"],
    )

    wa.reapply_in_background(0, "terminal restart")

    assert calls == [(["https://a.example"], True, "terminal restart")] * 2


def test_reapply_gives_up_after_three_attempts(monkeypatch):
    wa, calls, _ = _reapply_harness(
        monkeypatch, True, ["https://a.example"], ["FAIL:x"] * 5,
    )

    wa.reapply_in_background(0, "terminal restart")

    assert len(calls) == 3


def test_reapply_waits_out_a_backtest_without_spending_attempts(monkeypatch):
    wa, calls, sleeps = _reapply_harness(
        monkeypatch, True, ["https://a.example"], ["FAIL:x", "FAIL:x", "OK"],
        backtest=[True, True, True, False],
    )

    wa.reapply_in_background(0, "api start")

    assert len(calls) == 3
    assert sleeps.count(wa._BACKTEST_POLL_SECONDS) == 3


@pytest.mark.parametrize(
    ("available", "urls"),
    [(False, ["https://a.example"]), (True, [])],
)
def test_reapply_is_a_no_op_off_windows_or_without_a_stored_list(monkeypatch, available, urls):
    wa, calls, _ = _reapply_harness(monkeypatch, available, urls, ["OK"])

    wa.reapply_in_background(0, "api start")

    assert calls == []


# ── url hygiene shared with the driver ───────────────────────────────

@pytest.mark.parametrize(
    "url", ["https://a b.example", "https://", "http:// x", "https://a.example;x",
            "javascript:alert(1)"],
)
def test_clean_urls_drops_what_the_driver_would_not_count_as_a_row(url):
    from mt5api.chartctl.winoptions import is_url_row

    assert wr.clean_urls([url]) == []
    assert wr.invalid_urls([url]) == [url]
    assert not (is_url_row(url) and ";" not in url)


# ── one re-apply at a time, recorded by terminal generation ──────────


def test_a_request_while_a_reapply_runs_adds_one_more_pass_not_a_second_thread(monkeypatch):
    from mt5api.chartctl import webrequest_apply as wa

    threads = []

    class _HeldThread:
        """Starts nothing until the test runs it, so the first pass is 'running'."""

        def __init__(self, target, args=(), **_kwargs):
            self.target, self.args = target, args
            threads.append(self)

        def start(self):
            pass

    passes = []
    monkeypatch.setattr(wa, "available", lambda: True)
    monkeypatch.setattr(wa.wr, "effective_urls", lambda _cfg: ["https://a.example"])
    monkeypatch.setattr(wa.threading, "Thread", _HeldThread)
    monkeypatch.setattr(wa, "_reapply_once", lambda delay, reason: passes.append(reason))

    wa.reapply_in_background(0, "api start")
    assert wa.reapply_pending() is True
    wa.reapply_in_background(0, "new terminal process")
    wa.reapply_in_background(0, "terminal restart")

    assert len(threads) == 1
    threads[0].target(*threads[0].args)
    assert passes == ["api start", "terminal restart"]  # one extra pass, the latest ask
    assert wa.reapply_pending() is False


def test_the_reapply_reads_the_stored_list_when_it_applies(monkeypatch):
    wa, calls, _ = _reapply_harness(monkeypatch, True, ["https://a.example"], ["OK"])
    stored = iter([["https://a.example"], ["https://b.example"]])
    monkeypatch.setattr(wa.wr, "effective_urls", lambda _cfg: next(stored))

    wa.reapply_in_background(0, "api start")

    assert calls == [(["https://b.example"], True, "api start")]


def test_a_successful_apply_records_the_terminal_generation(client, vm, monkeypatch):
    from mt5api.chartctl import terminal_pid

    monkeypatch.setattr(client._wa, "_applied_generation", None)
    monkeypatch.setattr(terminal_pid, "find_terminal", lambda *_a: (4242, 123.5))

    client.put("/webrequest", json={"urls": REAL_URLS})

    assert client._wa.applied_generation() == (4242, 123.5)
    last = client.get("/webrequest").get_json()["last_apply"]
    assert (last["pid"], last["pid_started_at"]) == (4242, 123.5)


def test_the_recorded_generation_survives_an_api_restart(client, vm, monkeypatch):
    from mt5api.chartctl import terminal_pid

    monkeypatch.setattr(terminal_pid, "find_terminal", lambda *_a: (4242, 123.5))
    client.put("/webrequest", json={"urls": REAL_URLS})
    monkeypatch.setattr(client._wa, "_applied_generation", None)  # a fresh API process

    assert client._wa.applied_generation() == (4242, 123.5)


def test_a_failed_apply_and_a_verify_record_no_generation(client, vm, monkeypatch):
    from mt5api.chartctl.winoptions import OptionsError

    monkeypatch.setattr(client._wa, "_applied_generation", None)
    client.post("/webrequest/verify")
    vm["raise"] = OptionsError("terminal_busy", "busy")
    client.put("/webrequest", json={"urls": REAL_URLS})

    assert client._wa.applied_generation() is None


def test_get_shows_the_running_terminal_and_whether_its_list_is_stale(client, vm, monkeypatch):
    from mt5api.chartctl import terminal_pid

    monkeypatch.setattr(client._wa, "_applied_generation", None)
    monkeypatch.setattr(terminal_pid, "find_terminal", lambda *_a: (4242, 123.5))
    monkeypatch.setattr(terminal_pid, "last_generation", lambda *_a: (4242, 123.5))
    client.put("/webrequest", json={"urls": REAL_URLS})

    fresh = client.get("/webrequest").get_json()
    assert (fresh["terminal_pid"], fresh["terminal_started_at"], fresh["stale"]) == (
        4242, 123.5, False)

    monkeypatch.setattr(terminal_pid, "last_generation", lambda *_a: (5151, 999.0))
    assert client.get("/webrequest").get_json()["stale"] is True


def test_a_terminal_with_no_stored_list_is_never_stale(client, vm, monkeypatch):
    from mt5api.chartctl import terminal_pid

    monkeypatch.setattr(terminal_pid, "last_generation", lambda *_a: (5151, 999.0))

    assert client.get("/webrequest").get_json()["stale"] is False


# ── review fixes ──────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [{"urls": "https://a.example"}, {"urls": None}, {"add": {}}])
def test_a_url_field_that_is_not_a_list_is_a_400_not_an_empty_list(client, vm, body):
    """Cleaned, a non-list became [], and {"urls": ...} then removed every row."""
    wr.save_desired(wr.config_dir(client._term), ["https://keep.example"])

    r = client.put("/webrequest", json=body)

    assert r.status_code == 400 and r.get_json()["code"] == "BAD_REQUEST"
    assert vm["calls"] == []
    assert wr.load_desired(wr.config_dir(client._term)) == ["https://keep.example"]


def test_a_failed_verify_leaves_the_last_apply_alone(client, vm):
    from mt5api.chartctl.winoptions import OptionsError

    client.put("/webrequest", json={"urls": REAL_URLS})
    before = client.get("/webrequest").get_json()["last_apply"]
    vm["raise"] = OptionsError("terminal_busy", "busy")

    assert client.post("/webrequest/verify").status_code == 409

    assert client.get("/webrequest").get_json()["last_apply"] == before


@pytest.mark.parametrize(
    ("code", "status"),
    [("terminal_busy", 409), ("pid_not_found", 503), ("window_not_found", 503),
     ("list_not_changed", 500)],
)
def test_put_failures_carry_a_code_and_a_fitting_status(client, vm, code, status):
    from mt5api.chartctl.winoptions import OptionsError

    vm["raise"] = OptionsError(code, "test")

    r = client.put("/webrequest", json={"urls": REAL_URLS})

    assert r.status_code == status
    assert r.get_json()["code"] == code.upper()


def test_a_reapply_waits_for_a_patch_and_applies_the_list_it_saved(client, vm, monkeypatch):
    """A re-apply that read the stored list while a patch was in its dialog
    session would apply the old list over it and still record it as applied."""
    import threading

    from mt5api.chartctl import winoptions

    a, b = "https://a.example", "https://b.example"
    wr.save_desired(wr.config_dir(client._term), [a])
    vm["urls"], vm["enabled"] = [a], True
    gate, inside = threading.Event(), threading.Event()
    fake = winoptions.apply

    def slow_patch(pid, add, remove, **kwargs):
        if add:
            inside.set()
            assert gate.wait(5)
        return fake(pid, add, remove, **kwargs)

    monkeypatch.setattr(winoptions, "apply", slow_patch)
    monkeypatch.setattr(client._wa.time, "sleep", lambda _s: None)

    put = threading.Thread(target=lambda: client.put("/webrequest", json={"add": [b]}))
    put.start()
    assert inside.wait(5)
    reapply = threading.Thread(target=client._wa._reapply_once, args=(0, "terminal restart"))
    reapply.start()
    reapply.join(0.3)
    assert reapply.is_alive() and vm["calls"] == []  # waiting for the patch

    gate.set()
    put.join(5)
    reapply.join(5)

    assert vm["calls"][-1]["exact"] == [a, b]
    assert vm["urls"] == [a, b]


def test_a_url_longer_than_mt5_keeps_is_refused():
    """MT5 refused a 1120-character URL live (2026-10-15); refuse it at PUT."""
    long_url = "https://example.org/" + "x" * wr.MAX_URL_LENGTH

    assert wr.clean_urls([long_url]) == []
    assert wr.invalid_urls([long_url]) == [long_url]
    assert wr.clean_urls(["https://example.org/" + "x" * 100]) != []
