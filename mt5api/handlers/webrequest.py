"""WebRequest allowlist endpoints.

GET  /webrequest  -> current effective allowlist for this terminal.
PUT  /webrequest  -> set/patch the allowlist, then restart the terminal so MT5
                     picks up the new common.ini. Body is either a full replace
                     ``{"urls": [...]}`` or a patch ``{"add": [...], "remove": [...]}``.

Rare, heavyweight operation (holds the MT5 lock for the ~minutes a restart takes),
which is why it is a dedicated call rather than a field on deployments. First use
migrates from the terminal's existing common.ini so manual URLs are preserved.
"""
import os

from flask import jsonify, request

from mt5api.chartctl import webrequest as wr
from mt5api.config import TERMINAL_DIR
from mt5api.mt5client import restart_terminal, with_mt5


def _cfg_dir():
    return wr.config_dir(TERMINAL_DIR)


def get_webrequest():
    return jsonify({"urls": wr.effective_urls(_cfg_dir())})


@with_mt5
def put_webrequest():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "JSON body required"}), 400

    cfg_dir = _cfg_dir()
    if "urls" in body:
        new_urls = wr.clean_urls(body.get("urls"))
    elif "add" in body or "remove" in body:
        current = wr.effective_urls(cfg_dir)  # migrate-from-current on first use
        remove = set(wr.clean_urls(body.get("remove", [])))
        new_urls = [u for u in current if u not in remove]
        for u in wr.clean_urls(body.get("add", [])):
            if u not in new_urls:
                new_urls.append(u)
    else:
        return jsonify(
            {"success": False, "error": "provide 'urls', or 'add'/'remove'"}
        ), 400

    wr.save_desired(cfg_dir, new_urls)
    # restart_terminal writes common.ini from the desired file while the
    # terminal is down, then relaunches it.
    if not restart_terminal():
        return jsonify(
            {"success": False, "error": "terminal restart failed", "urls": new_urls}
        ), 500
    return jsonify({"success": True, "urls": new_urls})
