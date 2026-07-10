"""Chart Deployments REST handlers.

Lock-free by design: nothing here touches the MT5 SDK, so these routes
never queue behind the process-wide MT5 lock (mt5client.py). Everything
is file I/O against TERMINAL_DIR plus the loader-EA file protocol.
"""
import hashlib
import os

from flask import jsonify, request, send_file

from mt5api.chartctl import command as cmd
from mt5api.chartctl import paths, registry
from mt5api.chartctl.setparse import parse_set_bytes
from mt5api.chartctl.tpl_builder import write_tpl
from mt5api.config import CHARTCTL_MAX_UPLOAD_BYTES
from mt5api.logger import log

_VALID_TIMEFRAMES = frozenset({
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
})


def _err(status: int, code: str, message: str):
    return jsonify({"error": message, "code": code}), status


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_dir(directory: str, ext: str, source: str) -> list[dict]:
    items = []
    if not os.path.isdir(directory):
        return items
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(ext):
            continue
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        stat = os.stat(full)
        items.append({
            "name": name,
            "size": stat.st_size,
            "sha256": _sha256(full),
            "modified_at": int(stat.st_mtime),
            "source": source,
        })
    return items


def _read_upload(field: str, required_ext: str) -> tuple[str, bytes]:
    upload = request.files.get(field)
    if upload is None or not upload.filename:
        raise ValueError(f"Missing form file: {field}")
    name = paths.safe_name(upload.filename, field, required_ext)
    data = upload.stream.read(CHARTCTL_MAX_UPLOAD_BYTES + 1)
    if len(data) > CHARTCTL_MAX_UPLOAD_BYTES:
        raise ValueError(
            f"{field}: file exceeds {CHARTCTL_MAX_UPLOAD_BYTES} bytes")
    if not data:
        raise ValueError(f"{field}: file is empty")
    return name, data


# ── Artifacts: experts ───────────────────────────────────────────────

def upload_expert():
    try:
        name, data = _read_upload("expert", ".ex5")
    except ValueError as exc:
        return _err(400, "BAD_REQUEST", str(exc))
    paths.ensure_dirs()
    dest = os.path.join(paths.EXPERTS_DIR, name)
    new_hash = hashlib.sha256(data).hexdigest()
    overwrite = (request.args.get("overwrite", "false").lower() == "true")
    if os.path.exists(dest) and not overwrite:
        if _sha256(dest) == new_hash:
            return jsonify({"name": name, "sha256": new_hash,
                            "skipped": True})
        return _err(409, "EXISTS",
                    f"{name} exists with different content; "
                    "pass ?overwrite=true to replace")
    paths.atomic_write_bytes(dest, data)
    log.info("chartctl expert staged: %s (%d bytes, %s)",
             name, len(data), new_hash[:12])
    return jsonify({"name": name, "sha256": new_hash, "size": len(data)}), 201


def list_experts():
    return jsonify({
        "experts": _list_dir(paths.EXPERTS_DIR, ".ex5", "uploaded")
        + _list_dir(paths.HOST_EXPERTS_DIR, ".ex5", "host"),
    })


def delete_expert(name):
    try:
        name = paths.safe_name(name, "expert", ".ex5")
    except ValueError as exc:
        return _err(400, "BAD_REQUEST", str(exc))
    if os.path.exists(os.path.join(paths.HOST_EXPERTS_DIR, name)) and \
            not os.path.exists(os.path.join(paths.EXPERTS_DIR, name)):
        return _err(403, "HOST_ASSET", "host-managed assets are read-only")
    target = os.path.join(paths.EXPERTS_DIR, name)
    if not os.path.exists(target):
        return _err(404, "ARTIFACT_NOT_FOUND", f"{name} is not staged")
    if registry.expert_in_use(name):
        return _err(409, "IN_USE",
                    f"{name} is referenced by an existing deployment")
    os.remove(target)
    return jsonify({"deleted": name})


# ── Artifacts: sets ──────────────────────────────────────────────────

def upload_set():
    try:
        name, data = _read_upload("set", ".set")
        inputs = parse_set_bytes(data)
    except ValueError as exc:
        return _err(400, "BAD_REQUEST", str(exc))
    paths.ensure_dirs()
    dest = os.path.join(paths.SETS_DIR, name)
    paths.atomic_write_bytes(dest, data)
    log.info("chartctl set staged: %s (%d inputs)", name, len(inputs))
    return jsonify({"name": name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "inputs": inputs}), 201


def list_sets():
    return jsonify({
        "sets": _list_dir(paths.SETS_DIR, ".set", "uploaded")
        + _list_dir(paths.HOST_SETS_DIR, ".set", "host"),
    })


def get_set(name):
    try:
        name = paths.safe_name(name, "set", ".set")
    except ValueError as exc:
        return _err(400, "BAD_REQUEST", str(exc))
    path = _resolve_set(name)
    if path is None:
        return _err(404, "ARTIFACT_NOT_FOUND", f"{name} is not staged")
    with open(path, "rb") as handle:
        data = handle.read()
    return jsonify({"name": name, "inputs": parse_set_bytes(data)})


def _resolve_set(name: str) -> str | None:
    for base in (paths.SETS_DIR, paths.HOST_SETS_DIR):
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _resolve_expert(name: str) -> str | None:
    for base in (paths.EXPERTS_DIR, paths.HOST_EXPERTS_DIR):
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    return None


# ── Deployments ──────────────────────────────────────────────────────

def _terminal_build() -> int | None:
    """Best-effort build stamp for the template header. Never blocks:
    peeks at cached terminal info without taking the MT5 lock."""
    try:
        from mt5api import mt5client
        info = getattr(mt5client, "LAST_TERMINAL_INFO", None)
        if info and getattr(info, "build", None):
            return int(info.build)
    except Exception:  # noqa: BLE001 — stamp is cosmetic, never fail on it
        pass
    return None


def _materialize_tpl(dep: dict) -> None:
    inputs: list[dict] = []
    if dep.get("set_file"):
        set_path = _resolve_set(dep["set_file"])
        if set_path is None:
            raise ValueError(f"set file {dep['set_file']} disappeared")
        with open(set_path, "rb") as handle:
            inputs = parse_set_bytes(handle.read())
        # Optimization tails are meaningless on a live chart.
        inputs = [{"name": i["name"], "value": i["value"]} for i in inputs]
    write_tpl(
        deployment_id=dep["id"],
        expert_name=dep["expert_name"],
        expert_rel_path=f"Experts\\Uploaded\\{dep['expert_file']}",
        inputs=inputs,
        terminal_build=_terminal_build(),
    )


def create_deployment():
    body = request.get_json(silent=True) or {}
    try:
        expert_file = paths.safe_name(body.get("expert", ""), "expert", ".ex5")
        set_file = None
        if body.get("set"):
            set_file = paths.safe_name(body["set"], "set", ".set")
        symbol = str(body.get("symbol", "")).strip()
        timeframe = str(body.get("timeframe", "")).strip().upper()
        enabled = bool(body.get("enabled", True))
        if not symbol:
            raise ValueError("symbol is required")
        if timeframe not in _VALID_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of "
                             f"{sorted(_VALID_TIMEFRAMES)}")
    except ValueError as exc:
        return _err(400, "BAD_REQUEST", str(exc))

    expert_path = _resolve_expert(expert_file)
    if expert_path is None:
        return _err(404, "ARTIFACT_NOT_FOUND",
                    f"expert {expert_file} is not staged — upload it first")
    if expert_path.startswith(paths.HOST_EXPERTS_DIR):
        # Host asset: mirror into Uploaded/ so the terminal can load it.
        paths.ensure_dirs()
        with open(expert_path, "rb") as handle:
            paths.atomic_write_bytes(
                os.path.join(paths.EXPERTS_DIR, expert_file), handle.read())
    if set_file and _resolve_set(set_file) is None:
        return _err(404, "ARTIFACT_NOT_FOUND",
                    f"set {set_file} is not staged — upload it first")

    expert_name = expert_file[:-4] if expert_file.lower().endswith(".ex5") \
        else expert_file
    try:
        dep = registry.add_deployment(
            expert_file=expert_file, expert_name=expert_name,
            set_file=set_file, symbol=symbol, timeframe=timeframe,
            enabled=enabled)
    except registry.DuplicateChart as exc:
        return _err(409, "DUPLICATE_CHART", str(exc))

    try:
        _materialize_tpl(dep)
    except ValueError as exc:
        registry.remove_deployment(dep["id"])
        return _err(500, "TPL_GENERATION_FAILED", str(exc))

    return jsonify({"id": dep["id"], "status": "pending",
                    "deployment": dep}), 202


def list_deployments():
    return jsonify(registry.merged_view())


def get_deployment(dep_id):
    view = registry.merged_view()
    for item in view["deployments"]:
        if item["id"] == dep_id:
            item["revision"] = view["revision"]
            item["observed_stale"] = view["observed_stale"]
            return jsonify(item)
    return _err(404, "NOT_FOUND", f"deployment {dep_id} does not exist")


def patch_deployment(dep_id):
    body = request.get_json(silent=True) or {}
    changes: dict = {}
    try:
        if "set" in body:
            changes["set_file"] = (
                paths.safe_name(body["set"], "set", ".set")
                if body["set"] else None)
            if changes["set_file"] and _resolve_set(changes["set_file"]) is None:
                return _err(404, "ARTIFACT_NOT_FOUND",
                            f"set {changes['set_file']} is not staged")
        if "enabled" in body:
            changes["enabled"] = bool(body["enabled"])
    except ValueError as exc:
        return _err(400, "BAD_REQUEST", str(exc))
    if not changes:
        return _err(400, "BAD_REQUEST",
                    "nothing to change: pass 'set' and/or 'enabled'")
    try:
        dep = registry.update_deployment(dep_id, **changes)
    except KeyError:
        return _err(404, "NOT_FOUND", f"deployment {dep_id} does not exist")
    if "set_file" in changes:
        try:
            _materialize_tpl(dep)
        except ValueError as exc:
            return _err(500, "TPL_GENERATION_FAILED", str(exc))
    return jsonify({"id": dep_id, "deployment": dep})


def delete_deployment(dep_id):
    try:
        dep = registry.remove_deployment(dep_id)
    except KeyError:
        return _err(404, "NOT_FOUND", f"deployment {dep_id} does not exist")
    # Template file is left on disk until the loader confirms detach; the
    # loader clears the chart because the deployment vanished from
    # desired.json. Cleanup of orphaned .tpl files happens lazily here.
    tpl = os.path.join(paths.TEMPLATES_DIR, f"{dep_id}.tpl")
    try:
        os.remove(tpl)
    except OSError:
        pass
    return jsonify({"deleted": dep_id, "was": dep})


def reconcile():
    registry.rewrite_desired()
    return jsonify({"revision": registry.current_revision()}), 202


# ── Observation ──────────────────────────────────────────────────────

def charts():
    observed, stale = registry.read_observed()
    return jsonify({
        "loader_alive": registry.loader_alive(observed, stale),
        "observed_stale": stale,
        "loader": (observed or {}).get("loader"),
        "auto_trading": (observed or {}).get("terminal", {}).get("auto_trading"),
        "charts": (observed or {}).get("charts", []),
    })


def loader_status():
    observed, stale = registry.read_observed()
    alive = registry.loader_alive(observed, stale)
    payload = {
        "alive": alive,
        "observed_stale": stale,
        "loader": (observed or {}).get("loader"),
        "desired_revision": registry.current_revision(),
        "applied_revision":
            (observed or {}).get("loader", {}).get("applied_revision"),
    }
    if not alive:
        payload["hint"] = (
            "No live loader detected. Attach MT5ChartLoader (bundled under "
            "assets/experts/) to any chart, or add ChartControl.mqh to your "
            "own resident EA — see docs/chart-control-protocol.md.")
    return jsonify(payload)


def screenshot(chart_id):
    try:
        result = cmd.run_command("screenshot", {
            "chart_id": int(chart_id),
            "width": int(request.args.get("width", 1280)),
            "height": int(request.args.get("height", 720)),
        })
    except ValueError:
        return _err(400, "BAD_REQUEST", "chart_id/width/height must be ints")
    except cmd.LoaderBusy as exc:
        return _err(409, "LOADER_BUSY", str(exc))
    except cmd.LoaderTimeout as exc:
        return _err(504, "LOADER_TIMEOUT", str(exc))
    if result.get("status") != "ok":
        return _err(502, result.get("error_code", "LOADER_ERROR"),
                    result.get("error_detail", "loader reported failure"))
    filename = paths.safe_name(result.get("file", ""), "screenshot")
    png = os.path.join(paths.SCREENSHOTS_DIR, filename)
    if not os.path.isfile(png):
        return _err(502, "LOADER_ERROR",
                    "loader reported a screenshot that does not exist")
    response = send_file(png, mimetype="image/png")
    return response
