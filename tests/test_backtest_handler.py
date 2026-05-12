"""Validation-level tests for mt5api.backtest.handler.

Subprocess (terminal64.exe) is monkeypatched out so the worker thread does
not actually launch MT5. We only test request validation, INI parsing,
asset resolution, credential injection, and the queued response shape.
"""
from __future__ import annotations

import io
import os
from unittest.mock import patch

import pytest

from mt5api.backtest import handler, jobs
from mt5api.server import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Isolate filesystem state for every test.
    terminal_dir = tmp_path / "terminal"
    terminal_dir.mkdir()
    terminal_path = terminal_dir / "terminal64.exe"
    terminal_path.write_text("stub")
    assets_dir = tmp_path / "assets"
    (assets_dir / "experts").mkdir(parents=True)
    (assets_dir / "sets").mkdir(parents=True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    job_dir = log_dir / "backtest-jobs"

    monkeypatch.setattr(handler, "TERMINAL_DIR", str(terminal_dir))
    monkeypatch.setattr(handler, "TERMINAL_PATH", str(terminal_path))
    monkeypatch.setattr(handler, "ASSETS_DIR", str(assets_dir))
    monkeypatch.setattr(handler, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(handler, "BACKTEST_JOB_DIR", str(job_dir))
    monkeypatch.setattr(handler, "BROKER", "testbroker")
    monkeypatch.setattr(handler, "ACCOUNT", "testacct")
    monkeypatch.setattr(jobs, "BACKTEST_JOB_DIR", str(job_dir))
    jobs.BACKTEST_JOBS.clear()

    monkeypatch.setattr(handler, "_load_account_config", lambda: {
        "login": 12345678,
        "password": "secret",
        "server": "Test-Server",
    })

    # Don't actually spawn worker threads; tests inspect queued state only.
    monkeypatch.setattr(handler.threading, "Thread", _NoopThread)

    # Disable auth so /backtest is reachable without a token.
    monkeypatch.setattr("mt5api.server.API_TOKEN", "")

    app.config["TESTING"] = True
    return app.test_client(), tmp_path


class _NoopThread:
    def __init__(self, *_, **__):
        pass

    def start(self):
        pass


_VALID_INI = (
    "[Tester]\n"
    "Expert=will be overridden\n"
    "Symbol=NZDJPY\n"
    "Period=M15\n"
    "FromDate=2020.01.01\n"
    "ToDate=2025.01.01\n"
    "Model=2\n"
)


def _multipart(ini_text=_VALID_INI, expert_bytes=b"EX5BYTES", expert_filename="MyEA.ex5",
               set_bytes=None, set_filename=None, expert_name=None, set_name=None,
               include_ini=True):
    fields = {}
    if include_ini:
        fields["ini"] = (io.BytesIO(ini_text.encode("utf-8")), "tester.ini")
    if expert_bytes is not None:
        fields["expert"] = (io.BytesIO(expert_bytes), expert_filename)
    if set_bytes is not None:
        fields["set"] = (io.BytesIO(set_bytes), set_filename)
    if expert_name is not None:
        fields["expert_name"] = expert_name
    if set_name is not None:
        fields["set_name"] = set_name
    return fields


def test_missing_ini_returns_400(client):
    c, _ = client
    resp = c.post("/backtest", data=_multipart(include_ini=False, expert_bytes=b"x"),
                  content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "ini" in resp.get_json()["error"].lower()


def test_missing_expert_returns_400(client):
    c, _ = client
    resp = c.post("/backtest", data=_multipart(expert_bytes=None),
                  content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "expert" in resp.get_json()["error"].lower()


def test_path_traversal_in_expert_name_rejected(client):
    c, _ = client
    resp = c.post(
        "/backtest",
        data=_multipart(expert_bytes=None, expert_name="../etc/passwd.ex5"),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "filename" in resp.get_json()["error"].lower()


def test_unknown_asset_name_returns_400(client):
    c, _ = client
    resp = c.post(
        "/backtest",
        data=_multipart(expert_bytes=None, expert_name="missing.ex5"),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "not found" in resp.get_json()["error"].lower()


def test_ini_without_tester_section_returns_400(client):
    c, _ = client
    resp = c.post(
        "/backtest",
        data=_multipart(ini_text="[Common]\nLogin=1\n"),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "tester" in resp.get_json()["error"].lower()


def test_happy_path_inline_upload_returns_202(client):
    c, tmp = client
    resp = c.post("/backtest", data=_multipart(),
                  content_type="multipart/form-data")
    assert resp.status_code == 202, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["broker"] == "testbroker"
    assert body["account"] == "testacct"
    assert body["jobId"]
    assert body["statusUrl"].endswith(body["jobId"])
    assert resp.headers.get("Retry-After")

    # Stage dir + persisted files exist.
    job_id = body["jobId"]
    stage_dir = os.path.join(handler.BACKTEST_JOB_DIR, job_id)
    assert os.path.exists(os.path.join(stage_dir, "MyEA.ex5"))
    assert os.path.exists(os.path.join(stage_dir, "normalized.ini"))

    # Credentials were injected; expert was rewritten to Uploaded\<base>.
    norm = open(os.path.join(stage_dir, "normalized.ini"), encoding="utf-8").read()
    assert "Login=12345678" in norm
    assert "Server=Test-Server" in norm
    assert "Expert=Uploaded\\MyEA" in norm


def test_host_managed_asset_resolves(client):
    c, tmp = client
    expert_path = tmp / "assets" / "experts" / "Hosted.ex5"
    expert_path.write_bytes(b"hosted-bytes")
    set_path = tmp / "assets" / "sets" / "hosted.set"
    set_path.write_bytes(b"hosted-set")

    resp = c.post(
        "/backtest",
        data=_multipart(expert_bytes=None, expert_name="Hosted.ex5", set_name="hosted.set"),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    job_id = resp.get_json()["jobId"]
    stage_dir = os.path.join(handler.BACKTEST_JOB_DIR, job_id)
    assert open(os.path.join(stage_dir, "Hosted.ex5"), "rb").read() == b"hosted-bytes"
    # Set file is namespaced with the jobId to avoid concurrent collisions.
    assert os.path.exists(os.path.join(stage_dir, f"{job_id}__hosted.set"))


def test_status_unknown_job_returns_404(client):
    c, _ = client
    resp = c.get("/backtest/does-not-exist")
    assert resp.status_code == 404


def test_report_and_log_404_when_not_ready(client):
    c, _ = client
    resp = c.post("/backtest", data=_multipart(), content_type="multipart/form-data")
    job_id = resp.get_json()["jobId"]
    # Worker is no-op so neither file exists.
    assert c.get(f"/backtest/{job_id}/report").status_code == 404
    assert c.get(f"/backtest/{job_id}/log").status_code == 404


def test_build_ini_route_returns_text(client):
    c, _ = client
    resp = c.post(
        "/backtest/build-ini",
        json={
            "symbol": "NZDJPY",
            "timeframe": "M15",
            "expert": "EA.ex5",
            "lastYears": 5,
            "modelling": "open-prices",
            "latencyMs": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "[Tester]" in body
    assert "Symbol=NZDJPY" in body
    assert "ExecutionMode=5" in body


def test_build_ini_route_validation_error(client):
    c, _ = client
    resp = c.post("/backtest/build-ini", json={"symbol": "X", "timeframe": "BAD",
                                               "expert": "EA.ex5", "lastDays": 1})
    assert resp.status_code == 400
    assert "timeframe" in resp.get_json()["error"].lower()


def test_build_ini_requires_json(client):
    c, _ = client
    resp = c.post("/backtest/build-ini", data="not json", content_type="text/plain")
    assert resp.status_code == 400
