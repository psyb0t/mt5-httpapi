"""End-to-end chartctl endpoint tests via Flask's test client, with the
Python FakeLoader playing the terminal side of the protocol.

Repoints every chartctl path at a tmp dir, registers the routes on a
fresh Flask app (config gating is bypassed here — we wire handlers
directly so the suite doesn't depend on CHARTCTL_ENABLED at import).
"""
from __future__ import annotations

import io
import json
import os

import pytest
from flask import Flask

from tests.chartctl_fake_loader import FakeLoader


@pytest.fixture
def client(monkeypatch, tmp_path):
    from mt5api.chartctl import paths, registry, command

    experts = tmp_path / "experts"
    sets_ = tmp_path / "sets"
    proto = tmp_path / "proto"
    tpls = tmp_path / "tpls"
    host_e = tmp_path / "host_experts"
    host_s = tmp_path / "host_sets"
    for d in (experts, sets_, proto, tpls, host_e, host_s,
              proto / "shots"):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(paths, "EXPERTS_DIR", str(experts))
    monkeypatch.setattr(paths, "SETS_DIR", str(sets_))
    monkeypatch.setattr(paths, "PROTOCOL_DIR", str(proto))
    monkeypatch.setattr(paths, "TEMPLATES_DIR", str(tpls))
    monkeypatch.setattr(paths, "SCREENSHOTS_DIR", str(proto / "shots"))
    monkeypatch.setattr(paths, "HOST_EXPERTS_DIR", str(host_e))
    monkeypatch.setattr(paths, "HOST_SETS_DIR", str(host_s))
    monkeypatch.setattr(paths, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(paths, "DESIRED_PATH", str(proto / "desired.json"))
    monkeypatch.setattr(paths, "OBSERVED_PATH", str(proto / "observed.json"))
    monkeypatch.setattr(paths, "COMMAND_PATH", str(proto / "command.json"))
    monkeypatch.setattr(paths, "COMMAND_RESULT_PATH",
                        str(proto / "command_result.json"))
    # registry caches some path constants via import — repoint those too.
    monkeypatch.setattr(registry.paths, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(registry.paths, "DESIRED_PATH", str(proto / "desired.json"))
    monkeypatch.setattr(registry.paths, "OBSERVED_PATH", str(proto / "observed.json"))
    monkeypatch.setattr(registry.paths, "PROTOCOL_DIR", str(proto))
    monkeypatch.setattr(registry, "_STATE", None)
    # tpl_builder writes into TEMPLATES_DIR imported at module load
    from mt5api.chartctl import tpl_builder
    monkeypatch.setattr(tpl_builder, "TEMPLATES_DIR", str(tpls))
    # shorten command timeout so the timeout test is fast
    monkeypatch.setattr(command, "CHARTCTL_COMMAND_TIMEOUT_SECONDS", 1)

    from mt5api.handlers import chartctl
    app = Flask(__name__)
    app.post("/experts")(chartctl.upload_expert)
    app.get("/experts")(chartctl.list_experts)
    app.delete("/experts/<name>")(chartctl.delete_expert)
    app.post("/sets")(chartctl.upload_set)
    app.get("/sets")(chartctl.list_sets)
    app.get("/sets/<name>")(chartctl.get_set)
    app.post("/deployments")(chartctl.create_deployment)
    app.get("/deployments")(chartctl.list_deployments)
    app.post("/deployments/reconcile")(chartctl.reconcile)
    app.get("/deployments/<dep_id>")(chartctl.get_deployment)
    app.patch("/deployments/<dep_id>")(chartctl.patch_deployment)
    app.delete("/deployments/<dep_id>")(chartctl.delete_deployment)
    app.get("/charts")(chartctl.charts)
    app.get("/loader")(chartctl.loader_status)
    app.post("/charts/<chart_id>/screenshot")(chartctl.screenshot)
    app.post("/charts/<chart_id>/close")(chartctl.close_chart)

    c = app.test_client()
    c._proto_dir = str(proto)   # stash for the fake loader
    return c


def _upload_expert(client, name="EA.ex5", content=b"MZ\x00fakeex5"):
    return client.post("/experts", data={
        "expert": (io.BytesIO(content), name)},
        content_type="multipart/form-data")


def _upload_set(client, name="gold.set", text="Lots=0.10\nMagic=777\n"):
    return client.post("/sets", data={
        "set": (io.BytesIO(text.encode("utf-16")), name)},
        content_type="multipart/form-data")


# ── artifacts ────────────────────────────────────────────────────────

def test_expert_upload_list_dedupe(client):
    r = _upload_expert(client)
    assert r.status_code == 201
    sha = r.get_json()["sha256"]
    # re-upload identical -> skipped
    r2 = _upload_expert(client)
    assert r2.get_json()["skipped"] is True
    lst = client.get("/experts").get_json()["experts"]
    assert any(e["name"] == "EA.ex5" and e["sha256"] == sha for e in lst)


def test_expert_upload_conflict_on_hash_change(client):
    _upload_expert(client, content=b"one")
    r = _upload_expert(client, content=b"two")
    assert r.status_code == 409
    assert r.get_json()["code"] == "EXISTS"


def test_set_upload_returns_parsed_inputs(client):
    r = _upload_set(client)
    assert r.status_code == 201
    inputs = r.get_json()["inputs"]
    assert {"name": "Lots", "value": "0.10"} in inputs


def test_expert_traversal_rejected(client):
    r = client.post("/experts", data={
        "expert": (io.BytesIO(b"x"), "../evil.ex5")},
        content_type="multipart/form-data")
    assert r.status_code == 400


# ── deployment lifecycle with fake loader ────────────────────────────

def test_full_deploy_verify_cycle(client):
    _upload_expert(client)
    _upload_set(client)
    r = client.post("/deployments", json={
        "expert": "EA.ex5", "set": "gold.set",
        "symbol": "XAUUSD", "timeframe": "M5"})
    assert r.status_code == 202
    dep_id = r.get_json()["id"]

    # Before the loader runs: pending, not converged.
    v = client.get("/deployments").get_json()
    assert v["deployments"][0]["status"] == "pending"
    assert v["converged"] is False

    # A .tpl was generated.
    from mt5api.chartctl import paths
    assert os.path.exists(os.path.join(paths.TEMPLATES_DIR, f"{dep_id}.tpl"))

    # Loader reconciles -> running + converged.
    loader = FakeLoader(client._proto_dir)
    loader.reconcile()
    v = client.get("/deployments").get_json()
    assert v["deployments"][0]["status"] == "running"
    assert v["converged"] is True

    # /charts reflects the live inventory.
    charts = client.get("/charts").get_json()
    assert charts["loader_alive"] is True
    assert charts["charts"][0]["symbol"] == "XAUUSD"


def test_deploy_requires_staged_expert(client):
    r = client.post("/deployments", json={
        "expert": "NOPE.ex5", "symbol": "EURUSD", "timeframe": "H1"})
    assert r.status_code == 404
    assert r.get_json()["code"] == "ARTIFACT_NOT_FOUND"


def test_duplicate_chart_conflict(client):
    _upload_expert(client)
    client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "EURUSD", "timeframe": "H1"})
    r = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "EURUSD", "timeframe": "H1"})
    assert r.status_code == 409
    assert r.get_json()["code"] == "DUPLICATE_CHART"


def test_pause_then_delete(client):
    _upload_expert(client)
    dep_id = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "USDJPY", "timeframe": "M30"
    }).get_json()["id"]

    # pause
    r = client.patch(f"/deployments/{dep_id}", json={"enabled": False})
    assert r.status_code == 200
    loader = FakeLoader(client._proto_dir)
    loader.reconcile()
    item = client.get(f"/deployments/{dep_id}").get_json()
    assert item["status"] == "paused"

    # delete removes the tpl and the row
    r = client.delete(f"/deployments/{dep_id}")
    assert r.status_code == 200
    from mt5api.chartctl import paths
    assert not os.path.exists(os.path.join(paths.TEMPLATES_DIR, f"{dep_id}.tpl"))
    assert client.get("/deployments").get_json()["deployments"] == []


def test_loader_reports_failure(client):
    _upload_expert(client)
    dep_id = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "GBPUSD", "timeframe": "M15"
    }).get_json()["id"]
    loader = FakeLoader(client._proto_dir)
    loader.reconcile(fail_ids={dep_id})
    item = client.get(f"/deployments/{dep_id}").get_json()
    assert item["status"] == "failed"
    assert item["error"]["code"] == "EXPERT_NOT_ATTACHED"


def test_patch_set_regenerates_tpl(client):
    _upload_expert(client)
    _upload_set(client, name="a.set", text="Lots=0.01\n")
    _upload_set(client, name="b.set", text="Lots=0.99\n")
    dep_id = client.post("/deployments", json={
        "expert": "EA.ex5", "set": "a.set",
        "symbol": "AUDUSD", "timeframe": "H1"}).get_json()["id"]
    from mt5api.chartctl import paths
    tpl = os.path.join(paths.TEMPLATES_DIR, f"{dep_id}.tpl")
    before = open(tpl, "rb").read()
    client.patch(f"/deployments/{dep_id}", json={"set": "b.set"})
    after = open(tpl, "rb").read()
    assert b"0.99" in after.decode("utf-16").encode("utf-8") or before != after


def test_loader_absent_hint(client):
    r = client.get("/loader").get_json()
    assert r["alive"] is False
    assert "hint" in r


def test_screenshot_via_command_channel(client, monkeypatch):
    _upload_expert(client)
    dep_id = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "XAUUSD", "timeframe": "M5"
    }).get_json()["id"]
    loader = FakeLoader(client._proto_dir)
    loader.reconcile()
    charts = client.get("/charts").get_json()["charts"]
    chart_id = charts[0]["chart_id"]

    # Command channel is synchronous in the handler; drive the fake loader
    # from a thread so it answers while the request blocks.
    import threading
    import time

    def answer():
        for _ in range(20):
            if loader.handle_command():
                return
            time.sleep(0.05)

    t = threading.Thread(target=answer)
    t.start()
    r = client.post(f"/charts/{chart_id}/screenshot")
    t.join()
    assert r.status_code == 200
    assert r.mimetype == "image/png"


def _run_with_fake_loader(client, loader, method, url):
    """Issue a command-channel request while the fake loader answers."""
    import threading
    import time

    def answer():
        for _ in range(20):
            if loader.handle_command():
                return
            time.sleep(0.05)

    t = threading.Thread(target=answer)
    t.start()
    r = getattr(client, method)(url)
    t.join()
    return r


def test_close_chart_via_command_channel(client):
    _upload_expert(client)
    client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "XAUUSD", "timeframe": "M5"})
    loader = FakeLoader(client._proto_dir)
    loader.reconcile()
    chart_id = client.get("/charts").get_json()["charts"][0]["chart_id"]

    r = _run_with_fake_loader(client, loader, "post", f"/charts/{chart_id}/close")
    assert r.status_code == 200
    assert r.get_json() == {"closed": chart_id}


def test_close_chart_loader_failure(client):
    loader = FakeLoader(client._proto_dir)
    loader.reconcile()
    # chart_id -1 is the fake loader's CLOSE_FAILED sentinel
    r = _run_with_fake_loader(client, loader, "post", "/charts/-1/close")
    assert r.status_code == 502
    assert r.get_json()["code"] == "CLOSE_FAILED"


def test_close_chart_bad_id(client):
    r = client.post("/charts/notanint/close")
    assert r.status_code == 400
