"""Deployment registry: the API-side source of truth for desired state.

Follows the backtest jobs.py pattern — persistent JSON, in-memory
write-through cache behind a lock, survives API restarts. Every mutation
bumps a monotonic `revision` and rewrites the EA-facing desired.json.

Status model (derived, never stored as truth):
  pending   declared, loader has not confirmed it yet
  running   observed.json shows the expert live on a chart
  degraded  previously running, currently missing (reconciliation active)
  failed    loader reported a terminal error for this deployment
  paused    enabled=false in desired state
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from mt5api.chartctl import paths
from mt5api.chartctl.tpl_builder import tpl_relative_name
from mt5api.config import (
    CHARTCTL_OBSERVED_STALE_SECONDS,
    CHARTCTL_RECONCILE_HINT_SECONDS,
)
from mt5api.logger import log

_LOCK = threading.Lock()
_STATE: dict | None = None  # {"revision": int, "deployments": {id: {...}}}

PROTOCOL_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def new_id() -> str:
    return "dep_" + secrets.token_hex(4)


def _empty_state() -> dict:
    return {"revision": 0, "deployments": {}}


def _load_locked() -> dict:
    global _STATE
    if _STATE is not None:
        return _STATE
    if os.path.exists(paths.REGISTRY_PATH):
        try:
            with open(paths.REGISTRY_PATH, "r", encoding="utf-8") as handle:
                _STATE = json.load(handle)
        except (OSError, ValueError) as exc:
            log.error("chartctl registry unreadable (%s) — starting empty; "
                      "corrupt file preserved as .bad", exc)
            try:
                os.replace(paths.REGISTRY_PATH, paths.REGISTRY_PATH + ".bad")
            except OSError:
                pass
            _STATE = _empty_state()
    else:
        _STATE = _empty_state()
    _STATE.setdefault("revision", 0)
    _STATE.setdefault("deployments", {})
    return _STATE


def _persist_locked(state: dict) -> None:
    paths.ensure_dirs()
    paths.atomic_write_text(paths.REGISTRY_PATH,
                            json.dumps(state, indent=2, sort_keys=True))
    _write_desired_locked(state)


def _write_desired_locked(state: dict) -> None:
    desired = {
        "protocol": PROTOCOL_VERSION,
        "revision": state["revision"],
        "updated_at": _now_iso(),
        "reconcile_interval": CHARTCTL_RECONCILE_HINT_SECONDS,
        "deployments": [
            {
                "id": dep["id"],
                "expert": dep["expert_name"],
                "template": tpl_relative_name(dep["id"]),
                "symbol": dep["symbol"],
                "timeframe": dep["timeframe"],
                "enabled": dep.get("enabled", True),
            }
            for dep in sorted(state["deployments"].values(),
                              key=lambda d: d["created_at"])
        ],
    }
    paths.atomic_write_text(paths.DESIRED_PATH,
                            json.dumps(desired, indent=2, sort_keys=True))


def list_deployments() -> list[dict]:
    with _LOCK:
        state = _load_locked()
        return [dict(d) for d in state["deployments"].values()]


def get_deployment(dep_id: str) -> dict | None:
    with _LOCK:
        state = _load_locked()
        dep = state["deployments"].get(dep_id)
        return dict(dep) if dep else None


def add_deployment(*, expert_file: str, expert_name: str, set_file: str | None,
                   symbol: str, timeframe: str, enabled: bool = True) -> dict:
    with _LOCK:
        state = _load_locked()
        for other in state["deployments"].values():
            if (other.get("enabled", True) and enabled
                    and other["symbol"].upper() == symbol.upper()
                    and other["timeframe"].upper() == timeframe.upper()):
                raise DuplicateChart(
                    f"enabled deployment {other['id']} already targets "
                    f"{symbol} {timeframe}")
        dep = {
            "id": new_id(),
            "expert_file": expert_file,
            "expert_name": expert_name,
            "set_file": set_file,
            "symbol": symbol,
            "timeframe": timeframe,
            "enabled": enabled,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        state["deployments"][dep["id"]] = dep
        state["revision"] += 1
        _persist_locked(state)
        log.info("chartctl deployment %s created: %s %s %s (rev=%d)",
                 dep["id"], expert_name, symbol, timeframe, state["revision"])
        return dict(dep)


def update_deployment(dep_id: str, **changes) -> dict:
    with _LOCK:
        state = _load_locked()
        dep = state["deployments"].get(dep_id)
        if dep is None:
            raise KeyError(dep_id)
        dep.update(changes)
        dep["updated_at"] = _now_iso()
        state["revision"] += 1
        _persist_locked(state)
        log.info("chartctl deployment %s updated (%s) rev=%d",
                 dep_id, ", ".join(changes), state["revision"])
        return dict(dep)


def remove_deployment(dep_id: str) -> dict:
    with _LOCK:
        state = _load_locked()
        dep = state["deployments"].pop(dep_id, None)
        if dep is None:
            raise KeyError(dep_id)
        state["revision"] += 1
        _persist_locked(state)
        log.info("chartctl deployment %s removed (rev=%d)",
                 dep_id, state["revision"])
        return dep


def expert_in_use(expert_file: str) -> bool:
    with _LOCK:
        state = _load_locked()
        return any(d["expert_file"] == expert_file
                   for d in state["deployments"].values())


def current_revision() -> int:
    with _LOCK:
        return _load_locked()["revision"]


def rewrite_desired() -> None:
    """Force-regenerate desired.json from the registry (bumps revision so
    the loader re-reads even if content is identical — used by
    /deployments/reconcile)."""
    with _LOCK:
        state = _load_locked()
        state["revision"] += 1
        _persist_locked(state)


class DuplicateChart(ValueError):
    pass


# ── Observed-state reading & merge ──────────────────────────────────

def read_observed() -> tuple[dict | None, bool]:
    """Return (observed_dict_or_None, is_stale)."""
    try:
        with open(paths.OBSERVED_PATH, "r", encoding="utf-8") as handle:
            observed = json.load(handle)
    except (OSError, ValueError):
        return None, True
    stale = True
    try:
        mtime = os.path.getmtime(paths.OBSERVED_PATH)
        stale = (time.time() - mtime) > CHARTCTL_OBSERVED_STALE_SECONDS
    except OSError:
        pass
    return observed, stale


def loader_alive(observed: dict | None, stale: bool) -> bool:
    return bool(observed) and not stale and bool(observed.get("loader"))


def merged_view() -> dict:
    """The GET /deployments payload: desired ⋈ observed."""
    observed, stale = read_observed()
    obs_by_id: dict[str, dict] = {}
    err_by_id: dict[str, dict] = {}
    if observed:
        for entry in observed.get("deployments") or []:
            if entry.get("id"):
                obs_by_id[entry["id"]] = entry
        for entry in observed.get("errors") or []:
            if entry.get("id"):
                err_by_id[entry["id"]] = entry

    applied_revision = (observed or {}).get("loader", {}).get("applied_revision")
    revision = current_revision()
    items = []
    all_converged = True
    for dep in list_deployments():
        obs = obs_by_id.get(dep["id"])
        err = err_by_id.get(dep["id"])
        status = _derive_status(dep, obs, err, stale)
        if status not in ("running", "paused"):
            all_converged = False
        items.append({
            "id": dep["id"],
            "desired": dep,
            "observed": obs,
            "error": err,
            "status": status,
        })

    return {
        "revision": revision,
        "applied_revision": applied_revision,
        "converged": all_converged and applied_revision == revision and not stale,
        "observed_stale": stale,
        "loader": (observed or {}).get("loader"),
        "deployments": items,
    }


def _derive_status(dep: dict, obs: dict | None, err: dict | None,
                   stale: bool) -> str:
    if not dep.get("enabled", True):
        return "paused"
    if err and (obs is None or obs.get("status") != "running"):
        return "failed"
    if obs and obs.get("status") == "running" and not stale:
        return "running"
    if obs and obs.get("status") == "running" and stale:
        return "degraded"
    if obs and obs.get("status"):
        return str(obs["status"])
    return "pending"
