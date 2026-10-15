"""WebRequest allowlist endpoints.

GET  /webrequest         -> the stored allowlist, plus the last apply result.
PUT  /webrequest         -> set (``urls``) or patch (``add``/``remove``) the
                            allowlist and apply it now.
POST /webrequest/apply   -> re-apply the stored allowlist (boot / manual hook,
                            since the VM terminal drops the list on restart).
POST /webrequest/verify  -> read the list the running terminal really holds.

The apply mechanism is chosen at runtime. Inside the dockur VM the list is set
in the terminal's own Options dialog with window messages
(chartctl/webrequest_apply.py), the only thing that sets it in-session there.
Elsewhere (a bare-metal terminal where ``common.ini`` IS the WebRequest store)
it falls back to writing ``common.ini`` and restarting the terminal.

Dedicated call (not a deployment field): URLs are only needed by the minority of
EAs that use WebRequest. First use migrates from whatever the terminal already
has so manually-configured URLs are preserved.
"""
import threading
import time

from flask import jsonify, request

from mt5api import mt5client
from mt5api.chartctl import autoit_webrequest as autoit
from mt5api.chartctl import terminal_pid
from mt5api.chartctl import webrequest as wr
from mt5api.chartctl import webrequest_apply as wa
from mt5api.config import TERMINAL_DIR
from mt5api.mt5client import restart_terminal, session

# One live read per this many seconds: each opens a modal dialog on the
# terminal and the edit box once per row, so a client must not poll it.
VERIFY_MIN_INTERVAL_SECONDS = 10

_verify_lock = threading.Lock()
_last_verify = [0.0]  # monotonic time of the last accepted verify call


def _cfg_dir():
    return wr.config_dir(TERMINAL_DIR)


def _error(status: int, code: str, message: str, **extra):
    return jsonify({"success": False, "error": message, "code": code, **extra}), status


def get_webrequest():
    # The generation the watcher last saw: never a lookup, which could fall back
    # to a 30-second WMI query.
    generation = terminal_pid.last_generation() if wa.available() else None
    return jsonify({
        "urls": wr.effective_urls(_cfg_dir()),
        "last_apply": wa.load_status(),
        "terminal_pid": generation[0] if generation else None,
        "terminal_started_at": generation[1] if generation else None,
        "stale": wa.is_stale(generation),
    })


def _patched(current, add, remove):
    removed = set(remove)
    new_urls = [u for u in current if u not in removed]
    for u in add:
        if u not in new_urls:
            new_urls.append(u)
    return new_urls


# Driver failure codes that are a state the caller can wait out (409) or a
# terminal that is not there right now (503); anything else is 500.
_CONFLICT_CODES = frozenset({wa.CODE_BACKTEST_RUNNING, "terminal_busy"})
_UNAVAILABLE_CODES = frozenset({"pid_not_found", "window_not_found"})


def _failure_code(detail: str) -> str:
    """The driver's code from "win32:FAIL:<code>" or "FAIL:<code>", or the
    bare-metal "restart-failed"."""
    return detail.rsplit(":", 1)[-1] if "FAIL:" in detail else detail


def _failure_status(detail: str) -> int:
    code = _failure_code(detail)
    if code in _CONFLICT_CODES:
        return 409
    if code in _UNAVAILABLE_CODES:
        return 503
    return 500


def _apply_failed(detail: str, urls):
    return jsonify({"success": False, "error": f"apply failed ({detail})",
                    "code": _failure_code(detail).upper().replace("-", "_"),
                    "urls": urls}), _failure_status(detail)


def _apply(urls, *, partial=False, reason="put"):
    """Apply ``urls`` to the running terminal. Returns (ok, detail)."""
    if wa.available():
        status, _log = wa.apply_urls(urls, partial=partial, reason=reason)
        return status == wa.STATUS_OK, f"win32:{status}"
    # bare-metal fallback: write common.ini from desired, then restart.
    with session():
        ok = restart_terminal()
    return ok, "restart" if ok else "restart-failed"


def put_webrequest():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "BAD_REQUEST", "JSON body required")
    if "urls" not in body and "add" not in body and "remove" not in body:
        return _error(400, "BAD_REQUEST", "provide 'urls', or 'add'/'remove'")
    # A non-list would clean to [] and, as "urls", replace the list with nothing.
    not_lists = [k for k in ("urls", "add", "remove")
                 if k in body and not isinstance(body[k], list)]
    if not_lists:
        return _error(400, "BAD_REQUEST", f"{', '.join(not_lists)} must be a list of URLs")

    bad = [u for key in ("urls", "add", "remove") for u in wr.invalid_urls(body.get(key, []))]
    if bad:
        return _error(400, "INVALID_URL",
                      f"only http(s) URLs with a host, at most {wr.MAX_URL_LENGTH} characters "
                      "and no spaces, ';' or control characters are accepted", rejected=bad)

    with wa.APPLY_LOCK:  # save and apply as one step against the background re-apply
        return _put_locked(body, _cfg_dir())


def _put_locked(body, cfg_dir):
    if "urls" in body:
        new_urls = wr.clean_urls(body.get("urls"))
        wr.save_desired(cfg_dir, new_urls)
        ok, detail = _apply(new_urls)
    else:
        add = wr.clean_urls(body.get("add", []))
        remove = wr.clean_urls(body.get("remove", []))
        # migrate-from-current on first use
        new_urls = _patched(wr.effective_urls(cfg_dir), add, remove)
        if wa.available():
            # A true patch: the terminal keeps every row nobody named, and the
            # URLs read back become the stored list, so URLs a user added by
            # hand in the VM survive restarts too.
            status, _log, live = wa.patch_urls(add, remove)
            ok, detail = status == wa.STATUS_OK, f"win32:{status}"
            if ok and live is not None:
                new_urls = wr.clean_urls(live)
            wr.save_desired(cfg_dir, new_urls)
        else:
            wr.save_desired(cfg_dir, new_urls)
            ok, detail = _apply(new_urls)
    if not ok:
        return _apply_failed(detail, new_urls)
    return jsonify({"success": True, "urls": new_urls, "applied_via": detail})


def apply_webrequest():
    """Re-apply the current desired allowlist (idempotent). Boot/manual hook."""
    # dev: ?script=<name.au3> runs a repo-shipped AutoIt script (VM only), e.g.
    # inspect_options.au3. ?runas=0 launches it non-elevated for diagnostics.
    dev_script = request.args.get("script")
    if dev_script and autoit.available():
        use_runas = request.args.get("runas", "0") == "1"
        try:
            status, txt = autoit.run_named(dev_script, use_runas=use_runas)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify({"script": dev_script, "status": status, "log": txt})

    with wa.APPLY_LOCK:
        urls = wr.effective_urls(_cfg_dir())
        if not urls:
            return jsonify({"success": True, "urls": [], "note": "nothing to apply"})
        # Partial, like the boot re-apply: a URL MT5 will not take is reported,
        # not a reason to leave the terminal with no list.
        ok, detail = _apply(urls, partial=True, reason="apply")
    if not ok:
        return _apply_failed(detail, urls)
    return jsonify({"success": True, "urls": urls, "applied_via": detail,
                    "rejected": (wa.load_status() or {}).get("rejected", [])})


def verify_webrequest():
    """Read the list the running terminal holds, beside the stored one."""
    if not wa.available():
        return _error(409, "UNAVAILABLE",
                      "the live list can only be read inside the Windows VM; on a bare-metal "
                      "terminal common.ini is the list")
    if wa.backtest_running():
        return _error(409, "BACKTEST_RUNNING",
                      "a backtest is running on this terminal; try again after it")
    with _verify_lock:
        wait = VERIFY_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_verify[0])
        if wait > 0:
            response = _error(429, "TOO_MANY_REQUESTS",
                              f"one live read per {VERIFY_MIN_INTERVAL_SECONDS} s; try again later")
            response[0].headers["Retry-After"] = str(int(wait) + 1)
            return response
        _last_verify[0] = time.monotonic()
    info = mt5client.LAST_TERMINAL_INFO
    status, live = wa.read_live()
    desired = wr.effective_urls(_cfg_dir())
    build = getattr(info, "build", None)
    if status != wa.STATUS_OK:
        code = _failure_code(status)
        return jsonify({"success": False, "urls": desired, "live": None,
                        "terminal_build": build, "error": live.get("error"),
                        "code": code.upper(), "diag": live.get("diag", {})}), \
            _failure_status(status)
    return jsonify({"urls": desired, "live": live, "terminal_build": build})
