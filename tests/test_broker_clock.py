"""Tests for mt5api.broker_clock: the auto broker UTC offset."""

import email.utils
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from mt5api import broker_clock as bc

H = 3600


def fresh(offset, jitter=0.0):
    """A raw reading from a live tick for a broker at `offset` seconds."""
    return offset - 2.0 + jitter


def test_first_measurement_replaces_the_fallback():
    history = bc.History()
    assert bc.decide([fresh(2 * H), fresh(2 * H, 1)], 0, bc.STATUS_FALLBACK, history) == (
        2 * H, "first measurement")


def test_an_unchanged_value_is_accepted_and_clears_pending():
    history = bc.History(pending=3 * H, count=1)
    value, reason = bc.decide([fresh(2 * H), fresh(2 * H)], 2 * H, bc.STATUS_MEASURED, history)
    assert (value, reason) == (2 * H, "unchanged")
    assert history.pending is None


def test_a_dst_step_needs_two_readings_in_a_row():
    history = bc.History()
    readings = [fresh(3 * H), fresh(3 * H)]
    assert bc.decide(readings, 2 * H, bc.STATUS_MEASURED, history)[0] is None
    assert bc.decide(readings, 2 * H, bc.STATUS_MEASURED, history) == (3 * H, "changed")


def test_any_other_jump_needs_three_readings_in_a_row():
    history = bc.History()
    readings = [fresh(5 * H), fresh(5 * H)]
    assert bc.decide(readings, 2 * H, bc.STATUS_SAVED, history)[0] is None
    assert bc.decide(readings, 2 * H, bc.STATUS_SAVED, history)[0] is None
    assert bc.decide(readings, 2 * H, bc.STATUS_SAVED, history) == (5 * H, "changed")


def test_a_different_reading_restarts_the_count():
    history = bc.History()
    bc.decide([fresh(3 * H)] * 2, 2 * H, bc.STATUS_MEASURED, history)
    bc.decide([fresh(1 * H)] * 2, 2 * H, bc.STATUS_MEASURED, history)
    assert bc.decide([fresh(3 * H)] * 2, 2 * H, bc.STATUS_MEASURED, history)[0] is None


def test_stale_ticks_from_a_closed_market_are_refused():
    stale = 2 * H - 2 * 86400 - 1234  # a Friday tick read on Sunday
    value, reason = bc.decide([stale, stale - 50], 2 * H, bc.STATUS_SAVED, bc.History())
    assert value is None
    assert "stale" in reason


def test_the_freshest_tick_decides_and_a_stale_one_does_not_count():
    history = bc.History()
    readings = [fresh(2 * H), fresh(2 * H, -1), 2 * H - 4000]
    assert bc.decide(readings, 0, bc.STATUS_FALLBACK, history)[0] == 2 * H


def test_symbols_that_disagree_are_refused():
    value, reason = bc.decide([fresh(2 * H), fresh(-3 * H)], 0,
                              bc.STATUS_FALLBACK, bc.History())
    assert value is None
    assert reason == "symbols disagree"


def test_a_single_symbol_broker_is_measured():
    assert bc.decide([fresh(H)], 0, bc.STATUS_FALLBACK, bc.History())[0] == H


def test_a_reading_outside_fourteen_hours_is_refused():
    value, reason = bc.decide([fresh(16 * H)] * 2, 0, bc.STATUS_FALLBACK, bc.History())
    assert value is None
    assert "14h" in reason


def test_no_ticks_change_nothing():
    assert bc.decide([], 2 * H, bc.STATUS_SAVED, bc.History()) == (None, "no ticks")


def test_half_hour_offsets_round_correctly():
    assert bc.round_offset(5.5 * H - 3) == 5.5 * H


def test_saved_value_round_trips(tmp_path):
    path = str(tmp_path / bc.STATE_BASENAME)
    bc._save(path, 3 * H, 1700000000.0, bc.CLOCK_HOST)
    assert bc._load_saved(path) == 3 * H


@pytest.mark.parametrize("content", ["", "{", '{"offset_seconds": "x"}',
                                     '{"offset_seconds": 1000}', '{"offset_seconds": 90000}'])
def test_a_torn_or_odd_saved_file_is_ignored(tmp_path, content):
    path = tmp_path / bc.STATE_BASENAME
    path.write_text(content, encoding="utf-8")
    assert bc._load_saved(str(path)) is None


def test_a_missing_saved_file_is_ignored(tmp_path):
    assert bc._load_saved(str(tmp_path / "nope.json")) is None


def test_auto_starts_from_the_saved_value(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "UTC_OFFSET_AUTO", True)
    path = tmp_path / bc.STATE_BASENAME
    path.write_text(json.dumps({"offset_seconds": 2 * H}), encoding="utf-8")
    monkeypatch.setattr(bc, "_offset", 0)
    monkeypatch.setattr(bc, "_status", bc.STATUS_STATIC)
    bc.init(str(path))
    assert (bc.offset_seconds(), bc._status) == (2 * H, bc.STATUS_SAVED)


def test_auto_without_a_saved_value_falls_back_to_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "UTC_OFFSET_AUTO", True)
    monkeypatch.setattr(bc, "_offset", 7 * H)
    monkeypatch.setattr(bc, "_status", bc.STATUS_STATIC)
    bc.init(str(tmp_path / "missing.json"))
    assert (bc.offset_seconds(), bc._status) == (0, bc.STATUS_FALLBACK)


def test_static_keeps_the_configured_value(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "UTC_OFFSET_AUTO", False)
    monkeypatch.setattr(bc, "UTC_OFFSET_SECONDS", 3 * H)
    monkeypatch.setattr(bc, "_offset", 0)
    monkeypatch.setattr(bc, "_status", bc.STATUS_FALLBACK)
    bc.init(str(tmp_path / "unused.json"))
    snap = bc.snapshot()
    assert snap["broker_utc_offset_seconds"] == 3 * H
    assert snap["broker_utc_offset_mode"] == bc.MODE_STATIC
    assert snap["broker_utc_offset_status"] == bc.STATUS_STATIC


def test_apply_saves_and_reports_the_measurement(monkeypatch, tmp_path):
    monkeypatch.setattr(bc, "UTC_OFFSET_AUTO", True)
    monkeypatch.setattr(bc, "_offset", 0)
    path = str(tmp_path / bc.STATE_BASENAME)
    bc.apply(2 * H, "first measurement", bc.CLOCK_HOST, path)
    snap = bc.snapshot()
    assert snap["broker_utc_offset_seconds"] == 2 * H
    assert snap["broker_utc_offset_status"] == bc.STATUS_MEASURED
    assert snap["broker_utc_offset_clock"] == bc.CLOCK_HOST
    assert bc._load_saved(path) == 2 * H


class _DateHandler(BaseHTTPRequestHandler):
    skew = 0.0
    header = None

    def do_HEAD(self):  # noqa: N802 - http.server naming
        # send_response would add the real Date header first.
        self.send_response_only(200)
        self.send_header("Date", self.header or email.utils.formatdate(
            time.time() + self.skew, usegmt=True))
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def date_server():
    servers = []

    def start(skew=0.0, header=None):
        handler = type("H", (_DateHandler,), {"skew": skew, "header": header})
        server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/"

    yield start
    for server in servers:
        server.shutdown()


def test_the_reference_clock_reads_the_host_date_header(date_server):
    delta, clock = bc.reference_utc_delta(date_server(skew=120.0), timeout=2)
    assert clock == bc.CLOCK_HOST
    assert 118 <= delta <= 122


def test_an_unreachable_reference_falls_back_to_the_guest_clock():
    assert bc.reference_utc_delta("http://127.0.0.1:9/", timeout=0.5) == (0.0, bc.CLOCK_VM)


def test_a_malformed_date_header_falls_back_to_the_guest_clock(date_server):
    assert bc.reference_utc_delta(date_server(header="not a date"), timeout=2) == (
        0.0, bc.CLOCK_VM)


def test_read_offsets_uses_the_shortest_symbol_per_prefix():
    now = time.time()
    ticks = {"EURUSD": now + 2 * H, "GBPUSD.r": now + 2 * H}
    fake = SimpleNamespace(
        symbols_get=lambda group=None: [SimpleNamespace(name=n) for n in
                                        ("EURUSD", "EURUSD.r", "GBPUSD.r", "TESTUSD")],
        symbol_select=lambda name, enable: True,
        symbol_info_tick=lambda name: SimpleNamespace(time=int(ticks[name]),
                                                      time_msc=int(ticks[name] * 1000)),
    )
    seen = []

    def call(fn, *args, **kwargs):
        if fn is fake.symbol_info_tick:
            seen.append(args[0])
        return fn(*args, **kwargs)

    readings = bc.read_offsets(fake, call, 0.0)
    assert seen == ["EURUSD", "GBPUSD.r"]
    assert all(abs(r - 2 * H) < 5 for r in readings)
