"""The Chart Deployments (chartctl) and WebRequest route table.

One function so the server, the endpoint tests and the MCP route-catalog test
all register exactly the same routes. server.py calls it only when
CHARTCTL_ENABLED; without it these paths do not exist and answer 404.
"""
from flask import Flask

from mt5api.handlers import chartctl, webrequest


def register_chartctl_routes(app: Flask) -> None:
    """Register every chartctl and WebRequest route on ``app``."""
    app.post("/experts")(chartctl.upload_expert)
    app.get("/experts")(chartctl.list_experts)
    app.delete("/experts/<name>")(chartctl.delete_expert)

    app.post("/sets")(chartctl.upload_set)
    app.get("/sets")(chartctl.list_sets)
    app.get("/sets/<name>")(chartctl.get_set)
    app.delete("/sets/<name>")(chartctl.delete_set)

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

    # Applied in the terminal's Options dialog with window messages (VM) or a
    # common.ini rewrite plus a terminal restart (bare metal). /apply
    # re-applies on demand; /verify reads the list the terminal really holds.
    app.get("/webrequest")(webrequest.get_webrequest)
    app.put("/webrequest")(webrequest.put_webrequest)
    app.post("/webrequest/apply")(webrequest.apply_webrequest)
    app.post("/webrequest/verify")(webrequest.verify_webrequest)
