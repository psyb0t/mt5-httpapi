"""End-to-end chartctl endpoint tests via Flask's test client, with the
Python FakeLoader playing the terminal side of the protocol.

The app comes from the shared ``chartctl_app`` fixture (tests/conftest.py):
the shipped route table on a fresh Flask app with every chartctl path
repointed at a tmp dir, so the suite does not depend on CHARTCTL_ENABLED.
"""
from __future__ import annotations

import io
import json
import os

import pytest

from tests.chartctl_fake_loader import FakeLoader


@pytest.fixture
def client(chartctl_app):
    c = chartctl_app.test_client()
    c._proto_dir = chartctl_app.proto_dir   # stash for the fake loader
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


def test_a_staged_set_can_be_deleted(client):
    _upload_set(client)

    r = client.delete("/sets/gold.set")

    assert r.status_code == 200
    assert r.get_json() == {"deleted": "gold.set"}
    assert client.get("/sets").get_json()["sets"] == []
    assert client.get("/sets/gold.set").status_code == 404


def test_deleting_a_set_that_is_not_staged_is_404(client):
    r = client.delete("/sets/gold.set")

    assert r.status_code == 404
    assert r.get_json()["code"] == "ARTIFACT_NOT_FOUND"


def test_a_set_a_deployment_uses_cannot_be_deleted_until_the_deployment_goes(client):
    _upload_expert(client)
    _upload_set(client)
    dep_id = client.post("/deployments", json={
        "expert": "EA.ex5", "set": "gold.set",
        "symbol": "EURUSD", "timeframe": "H1"}).get_json()["id"]
    client.patch(f"/deployments/{dep_id}", json={"enabled": False})

    refused = client.delete("/sets/gold.set")

    assert refused.status_code == 409
    assert refused.get_json()["code"] == "IN_USE"
    assert client.get("/sets/gold.set").status_code == 200
    client.delete(f"/deployments/{dep_id}")
    assert client.delete("/sets/gold.set").status_code == 200


def test_a_host_managed_set_cannot_be_deleted(client):
    from mt5api.chartctl import paths
    host_set = os.path.join(paths.HOST_SETS_DIR, "host.set")
    with open(host_set, "w", encoding="utf-8") as handle:
        handle.write("Lots=0.10\n")

    r = client.delete("/sets/host.set")

    assert r.status_code == 403
    assert r.get_json()["code"] == "HOST_ASSET"
    assert os.path.exists(host_set)


@pytest.mark.parametrize("name", ["gold.txt", ".hidden.set", "a..b.set"])
def test_deleting_a_set_with_a_bad_name_is_refused(client, name):
    r = client.delete(f"/sets/{name}")

    assert r.status_code == 400
    assert r.get_json()["code"] == "BAD_REQUEST"


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


def test_resuming_onto_a_pair_another_deployment_runs_is_refused(client):
    """Pausing A frees its symbol/timeframe, so B can be created there; A may
    not then be resumed onto the same pair."""
    _upload_expert(client)
    first = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "EURUSD", "timeframe": "H1"}).get_json()["id"]
    client.patch(f"/deployments/{first}", json={"enabled": False})
    second = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "EURUSD", "timeframe": "H1"})
    assert second.status_code == 202

    r = client.patch(f"/deployments/{first}", json={"enabled": True})

    assert r.status_code == 409
    assert r.get_json()["code"] == "DUPLICATE_CHART"
    stored = client.get(f"/deployments/{first}").get_json()
    assert stored["desired"]["enabled"] is False


def test_resuming_onto_a_free_pair_works(client):
    _upload_expert(client)
    dep = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "EURUSD", "timeframe": "H1"}).get_json()["id"]
    client.patch(f"/deployments/{dep}", json={"enabled": False})

    r = client.patch(f"/deployments/{dep}", json={"enabled": True})

    assert r.status_code == 200
    assert r.get_json()["deployment"]["enabled"] is True


def test_an_enabled_deployment_can_be_re_pointed_without_tripping_the_duplicate_check(client):
    _upload_expert(client)
    _upload_set(client)
    dep = client.post("/deployments", json={
        "expert": "EA.ex5", "symbol": "EURUSD", "timeframe": "H1"}).get_json()["id"]

    r = client.patch(f"/deployments/{dep}", json={"enabled": True, "set": "gold.set"})

    assert r.status_code == 200


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


# ── Navigator refresh after staging an expert ────────────────────────
#
# MT5 loads only experts it saw at startup or after a Navigator refresh, so
# every staged .ex5 must be followed by one, or its deployment never attaches.


@pytest.fixture
def navigator(monkeypatch):
    """Pretend the terminal's windows can be driven and record each Navigator
    refresh: its pid, the Navigator path and the labels it was given."""
    from mt5api.chartctl import terminal_pid, winnavigator
    from mt5api.chartctl import webrequest_apply as wa

    state = {"calls": 0, "result": {"found": True, "already_listed": False, "tried": ["a"]},
             "backtest": False, "args": []}

    def refresh(pid, path, variants, **kwargs):
        state["calls"] += 1
        state["args"].append({"pid": pid, "path": path, "variants": variants, **kwargs})
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(wa, "available", lambda: True)
    monkeypatch.setattr(wa, "backtest_running", lambda: state["backtest"])
    monkeypatch.setattr(terminal_pid, "find_terminal", lambda: (4242, 1.0))
    monkeypatch.setattr(winnavigator, "refresh", refresh)
    return state


def test_upload_without_gui_automation_says_a_restart_is_needed(client):
    body = _upload_expert(client).get_json()

    assert body["navigator_refresh"] == "unavailable"
    assert "restart the terminal" in body["note"]


@pytest.mark.parametrize("second_upload", ["same bytes", "overwrite"])
def test_every_successful_upload_refreshes_the_navigator(client, navigator, second_upload):
    first = _upload_expert(client).get_json()
    if second_upload == "same bytes":
        second = _upload_expert(client)
    else:
        second = client.post("/experts?overwrite=true", data={
            "expert": (io.BytesIO(b"MZ\x00changed"), "EA.ex5")},
            content_type="multipart/form-data")

    assert first["navigator_refresh"] == "ok"
    assert second.get_json()["navigator_refresh"] == "ok"
    assert navigator["calls"] == 2


def test_a_refused_upload_does_not_refresh(client, navigator):
    _upload_expert(client, content=b"one")
    refused = _upload_expert(client, content=b"two")

    assert refused.status_code == 409
    assert navigator["calls"] == 1


def _options_error(code):
    from mt5api.chartctl.winoptions import OptionsError

    return OptionsError(code, "test failure")


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"found": False, "already_listed": False, "tried": ["a", "c"]}, "expert_not_listed"),
        ("options_error", "navigator_not_found"),
        (RuntimeError("ctypes blew up"), "internal_error"),
    ],
)
def test_a_failed_refresh_is_reported_and_the_file_stays_staged(client, navigator, result, code):
    navigator["result"] = _options_error(code) if result == "options_error" else result

    r = _upload_expert(client)

    assert r.status_code == 201
    assert r.get_json()["navigator_refresh"] == "failed"
    assert r.get_json()["navigator_error"] == code
    assert "upload the same file again" in r.get_json()["note"]
    assert [e["name"] for e in client.get("/experts").get_json()["experts"]] == ["EA.ex5"]


def test_the_refresh_verifies_the_expert_under_uploaded_with_variants_a_then_c(client, navigator):
    _upload_expert(client)

    call = navigator["args"][0]
    assert call["pid"] == 4242
    assert call["path"] == ["Uploaded", "EA"]
    assert call["variants"] == ("a", "c")
    assert call["ea_labels"][-1] == "Expert Advisors"
    assert call["refresh_labels"][-1] == "Refresh"


def test_an_expert_listed_before_the_refresh_says_so(client, navigator):
    navigator["result"] = {"found": True, "already_listed": True, "tried": ["a"]}

    body = _upload_expert(client).get_json()

    assert body["navigator_refresh"] == "ok"
    assert body["already_listed"] is True


def test_a_fresh_expert_carries_no_already_listed_field(client, navigator):
    body = _upload_expert(client).get_json()

    assert body["navigator_refresh"] == "ok"
    assert "already_listed" not in body


def test_no_refresh_is_tried_while_a_backtest_runs(client, navigator):
    navigator["backtest"] = True

    body = _upload_expert(client).get_json()

    assert body["navigator_refresh"] == "failed"
    assert body["navigator_error"] == "backtest_running"
    assert navigator["calls"] == 0


def test_configured_navigator_labels_come_before_the_english_ones(client, navigator, monkeypatch):
    from mt5api.handlers import chartctl as handler

    monkeypatch.setattr(handler, "CHARTCTL_NAVIGATOR_LABELS",
                        {"expert_advisors": ["Fake Node"], "refresh": ["Fake Refresh"]})

    _upload_expert(client)

    call = navigator["args"][0]
    assert call["ea_labels"] == ("Fake Node", "Expert Advisors")
    assert call["refresh_labels"] == ("Fake Refresh", "Refresh")


def test_deploying_a_host_expert_refreshes_after_copying_it_in(client, navigator):
    from mt5api.chartctl import paths

    with open(os.path.join(paths.HOST_EXPERTS_DIR, "Host.ex5"), "wb") as handle:
        handle.write(b"MZ\x00host")

    r = client.post("/deployments", json={
        "expert": "Host.ex5", "symbol": "EURUSD", "timeframe": "M5"})

    assert r.status_code == 202
    assert r.get_json()["navigator_refresh"] == "ok"
    assert os.path.exists(os.path.join(paths.EXPERTS_DIR, "Host.ex5"))
    assert navigator["calls"] == 1
