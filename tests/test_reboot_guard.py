"""GET /busy and the write drain that hold a scheduled VM reboot.

The order and account views are swapped for stubs that report what /busy
would say while they run, so the tests see the in-flight state without MT5.
"""
from __future__ import annotations

import os
import time

import pytest
from flask import jsonify

from mt5api import reboot_guard, server
from mt5api.backtest import handler as backtest_handler
from mt5api.backtest import jobs as backtest_jobs

TOKEN = "guard-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "API_TOKEN", TOKEN)
    monkeypatch.setattr(reboot_guard, "DRAIN_FLAG", str(tmp_path / "reboot.draining"))
    monkeypatch.setattr(backtest_jobs, "BACKTEST_JOBS", {})
    server.app.config["TESTING"] = True
    return server.app.test_client()


@pytest.fixture
def busy_seen_by(monkeypatch):
    """Replace a view with one that answers what /busy reports mid-request."""

    def _swap(endpoint):
        monkeypatch.setitem(server.app.view_functions, endpoint, lambda: jsonify(reboot_guard.status()))

    return _swap


def test_an_idle_api_is_not_busy(client):
    body = client.get("/busy", headers=AUTH).get_json()

    assert body == {
        "busy": False,
        "reasons": [],
        "backtests": [],
        "writes_in_flight": [],
        "draining": False,
    }


def test_an_order_being_placed_holds_the_reboot(client, busy_seen_by):
    busy_seen_by("create_order")

    during = client.post("/orders", json={}, headers=AUTH).get_json()

    assert during["busy"] is True
    assert [(w["method"], w["path"]) for w in during["writes_in_flight"]] == [("POST", "/orders")]
    assert during["reasons"][0].startswith("write_in_flight POST /orders")
    assert client.get("/busy", headers=AUTH).get_json()["busy"] is False


def test_a_read_does_not_hold_the_reboot(client, busy_seen_by):
    busy_seen_by("get_account")

    during = client.get("/account", headers=AUTH).get_json()

    assert during["busy"] is False
    assert during["writes_in_flight"] == []


def test_a_write_that_fails_still_stops_holding_the_reboot(client, monkeypatch):
    def _boom():
        raise RuntimeError("handler crashed")

    monkeypatch.setitem(server.app.view_functions, "create_order", _boom)
    server.app.config["TESTING"] = False
    monkeypatch.setitem(server.app.config, "PROPAGATE_EXCEPTIONS", False)

    r = client.post("/orders", json={}, headers=AUTH)

    assert r.status_code == 500
    assert client.get("/busy", headers=AUTH).get_json()["writes_in_flight"] == []


def test_an_unauthorized_write_neither_holds_the_reboot_nor_sees_the_drain(client, busy_seen_by):
    busy_seen_by("create_order")
    open(reboot_guard.DRAIN_FLAG, "w").close()

    r = client.post("/orders", json={})

    assert r.status_code == 401
    assert r.get_json()["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_an_active_backtest_holds_the_reboot(client, status):
    backtest_jobs.BACKTEST_JOBS["a" * 32] = {"jobId": "a" * 32, "status": status}

    body = client.get("/busy", headers=AUTH).get_json()

    assert body["busy"] is True
    assert body["backtests"] == [{"job_id": "a" * 32, "status": status}]
    assert body["reasons"] == [f"backtest {'a' * 32} {status}"]


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_a_finished_backtest_does_not(client, status):
    backtest_jobs.BACKTEST_JOBS["b" * 32] = {"jobId": "b" * 32, "status": status}

    assert client.get("/busy", headers=AUTH).get_json()["busy"] is False


def test_a_cancelled_backtest_still_stopping_holds_the_reboot(client, monkeypatch):
    """Cancelled is final at once, but its worker is still killing the
    terminal's tester processes; a reboot must wait for it."""
    backtest_jobs.BACKTEST_JOBS["d" * 32] = {"jobId": "d" * 32, "status": "cancelled"}
    monkeypatch.setattr(backtest_handler, "_ACTIVE_WORKERS", {"d" * 32})

    body = client.get("/busy", headers=AUTH).get_json()

    assert body["busy"] is True
    assert body["backtests"] == [{"job_id": "d" * 32, "status": "cancelled"}]


def test_another_terminals_backtest_is_not_ours_to_report(client):
    backtest_jobs.BACKTEST_JOBS["c" * 32] = {
        "jobId": "c" * 32, "status": "running", "broker": "someone-else",
    }

    assert client.get("/busy", headers=AUTH).get_json()["busy"] is False


def test_while_draining_writes_are_refused_and_reads_go_through(client, busy_seen_by):
    busy_seen_by("create_order")
    open(reboot_guard.DRAIN_FLAG, "w").close()

    write = client.post("/orders", json={}, headers=AUTH)
    read = client.get("/busy", headers=AUTH)

    assert write.status_code == 503
    assert write.get_json()["code"] == "REBOOT_PENDING"
    assert write.headers["Retry-After"] == str(reboot_guard.DRAIN_RETRY_AFTER_SECONDS)
    assert read.status_code == 200
    assert read.get_json()["draining"] is True
    assert read.get_json()["writes_in_flight"] == []


def test_a_stale_drain_flag_is_ignored(client, busy_seen_by):
    busy_seen_by("create_order")
    open(reboot_guard.DRAIN_FLAG, "w").close()
    old = time.time() - reboot_guard.DRAIN_FLAG_MAX_AGE_SECONDS - 60
    os.utime(reboot_guard.DRAIN_FLAG, (old, old))

    r = client.post("/orders", json={}, headers=AUTH)

    assert r.status_code == 200
    assert r.get_json()["draining"] is False
