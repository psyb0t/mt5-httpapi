"""Tests for scripts/sync_clock.py, which sets the guest clock from the host."""

import email.utils
import importlib.util
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sync_clock.py"


@pytest.fixture(scope="module")
def sc():
    spec = importlib.util.spec_from_file_location("sync_clock", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _server(skew=0.0, header=None):
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):  # noqa: N802 - http.server naming
            # send_response would add the real Date header first.
            self.send_response_only(200)
            self.send_header("Date", header or email.utils.formatdate(
                time.time() + skew, usegmt=True))
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"


def test_a_clock_in_sync_is_left_alone(sc):
    assert not sc.needs_set(0.4)
    assert not sc.needs_set(-1.9)


def test_a_clock_more_than_two_seconds_off_is_set(sc):
    assert sc.needs_set(5.0)
    assert sc.needs_set(-3600.0)


def test_no_reference_never_sets_the_clock(sc):
    assert not sc.needs_set(None)


def test_the_delta_comes_from_the_host_date_header(sc):
    server, url = _server(skew=300.0)
    try:
        delta = sc.host_time_delta(url, timeout=2)
    finally:
        server.shutdown()
    assert 298 <= delta <= 302


def test_an_unreachable_reference_gives_no_delta(sc):
    assert sc.host_time_delta("http://127.0.0.1:9/", timeout=0.5) is None


def test_a_malformed_date_header_gives_no_delta(sc):
    server, url = _server(header="yesterday-ish")
    try:
        assert sc.host_time_delta(url, timeout=2) is None
    finally:
        server.shutdown()


def test_the_wickworks_url_comes_from_the_config(sc, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("wickworks:\n  url: http://10.0.0.9:8000/\n", encoding="utf-8")
    assert sc.reference_url(str(cfg)) == "http://10.0.0.9:8000/"


def test_a_missing_config_uses_the_default_url(sc, tmp_path):
    assert sc.reference_url(str(tmp_path / "none.yaml")) == sc.DEFAULT_REFERENCE_URL


def test_an_unreachable_reference_logs_a_warning_and_exits_zero(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "DEFAULT_REFERENCE_URL", "http://127.0.0.1:9/")
    monkeypatch.setattr(sc, "REQUEST_TIMEOUT_SECONDS", 0.5)
    start, full = tmp_path / "start.log", tmp_path / "full.log"
    assert sc.main(["sync_clock.py", str(tmp_path / "none.yaml"), str(start), str(full)]) == 0
    assert "WARN: clock sync" in start.read_text(encoding="utf-8")
    assert "[clock] WARN: clock sync" in full.read_text(encoding="utf-8")


def test_a_drifted_clock_is_set_and_logged(sc, tmp_path, monkeypatch):
    server, url = _server(skew=60.0)
    calls = []
    monkeypatch.setattr(sc, "reference_url", lambda path: url)
    monkeypatch.setattr(sc, "set_system_time_utc", lambda target: calls.append(target) or True)
    start = tmp_path / "start.log"
    try:
        assert sc.main(["sync_clock.py", "cfg", str(start), str(tmp_path / "full.log")]) == 0
    finally:
        server.shutdown()
    assert len(calls) == 1
    assert abs(calls[0] - (time.time() + 60)) < 3
    assert "guest clock was +" in start.read_text(encoding="utf-8")


def test_start_bat_syncs_the_clock_after_install_and_hourly():
    text = (SCRIPT.parent / "start.bat").read_text(encoding="ascii")
    call = '"%PYDIR%\\python.exe" "%SCRIPTS%\\sync_clock.py"'
    install_done = text.index('"install.bat done."')
    first_sync = text.index(call)
    terminals_parsed = text.index(":: -- Parse config.yaml terminals once")
    assert install_done < first_sync < terminals_parsed
    assert text.count(call) == 2
    assert text.index(call, first_sync + 1) > text.index(":status_loop")
    assert "w32tm /config /syncfromflags:NO /update" in text


def test_run_sh_copies_sync_clock_into_the_vm():
    run_sh = (SCRIPT.parent.parent / "run.sh").read_text(encoding="utf-8")
    assert 'scripts/sync_clock.py" "${DIR}/data/shared/scripts/sync_clock.py"' in run_sh
