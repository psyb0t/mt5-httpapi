"""WebRequest allowlist endpoints.

GET  /webrequest        -> current effective allowlist for this terminal.
PUT  /webrequest        -> set/patch the allowlist and apply it now.
POST /webrequest/apply  -> re-apply the current allowlist (boot / manual hook,
                           since the VM terminal drops the list on restart).

The apply mechanism is chosen at runtime. Inside the dockur VM the allowlist is
set by driving MT5's Options dialog with AutoIt (the only thing that persists it
in-session there — see chartctl/autoit_webrequest.py). Elsewhere (a bare-metal
terminal where ``common.ini`` IS the WebRequest store) it falls back to writing
``common.ini`` and restarting the terminal.

Dedicated call (not a deployment field): URLs are only needed by the minority of
EAs that use WebRequest. First use migrates from whatever the terminal already
has so manually-configured URLs are preserved.
"""
from flask import jsonify, request

from mt5api.chartctl import autoit_webrequest as autoit
from mt5api.chartctl import webrequest as wr
from mt5api.config import TERMINAL_DIR
from mt5api.mt5client import restart_terminal, session


def _cfg_dir():
    return wr.config_dir(TERMINAL_DIR)


def get_webrequest():
    return jsonify({"urls": wr.effective_urls(_cfg_dir())})


def _resolve_urls(body, cfg_dir):
    """Return (urls, error). Full replace via 'urls', or patch via 'add'/'remove'."""
    if "urls" in body:
        return wr.clean_urls(body.get("urls")), None
    if "add" in body or "remove" in body:
        current = wr.effective_urls(cfg_dir)  # migrate-from-current on first use
        remove = set(wr.clean_urls(body.get("remove", [])))
        new_urls = [u for u in current if u not in remove]
        for u in wr.clean_urls(body.get("add", [])):
            if u not in new_urls:
                new_urls.append(u)
        return new_urls, None
    return None, "provide 'urls', or 'add'/'remove'"


def _apply(urls, use_runas=False):
    """Apply ``urls`` to the running terminal. Returns (ok, detail)."""
    if autoit.available():
        status, _log = autoit.apply_urls(urls, use_runas=use_runas)
        return status == "OK", f"autoit:{status}"
    # bare-metal fallback: write common.ini from desired, then restart.
    with session():
        ok = restart_terminal()
    return ok, "restart" if ok else "restart-failed"


def _use_runas():
    return request.args.get("runas", "0") == "1"


def put_webrequest():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "JSON body required"}), 400

    cfg_dir = _cfg_dir()
    new_urls, err = _resolve_urls(body, cfg_dir)
    if err:
        return jsonify({"success": False, "error": err}), 400

    wr.save_desired(cfg_dir, new_urls)
    ok, detail = _apply(new_urls, _use_runas())
    if not ok:
        return jsonify(
            {"success": False, "error": f"apply failed ({detail})", "urls": new_urls}
        ), 500
    return jsonify({"success": True, "urls": new_urls, "applied_via": detail})


def apply_webrequest():
    """Re-apply the current desired allowlist (idempotent). Boot/manual hook."""
    # dev: ?script=<name.au3> runs a repo-shipped AutoIt script (VM only), e.g.
    # inspect_options.au3. ?runas=0 launches it non-elevated for diagnostics.
    dev_script = request.args.get("script")
    if dev_script and autoit.available():
        status, txt = autoit.run_named(dev_script, use_runas=_use_runas())
        return jsonify({"script": dev_script, "status": status, "log": txt})

    urls = wr.effective_urls(_cfg_dir())
    if not urls:
        return jsonify({"success": True, "urls": [], "note": "nothing to apply"})
    ok, detail = _apply(urls, _use_runas())
    if not ok:
        return jsonify(
            {"success": False, "error": f"apply failed ({detail})", "urls": urls}
        ), 500
    return jsonify({"success": True, "urls": urls, "applied_via": detail})
