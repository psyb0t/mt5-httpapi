from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.config import BROKER
from mt5api.mt5client import (
    ensure_initialized, init_mt5, to_dict,
    load_accounts, get_account as get_saved_account, switch_account,
)


def get_account():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    info = mt5.account_info()
    if info is None:
        return jsonify({"error": "No account logged in"}), 404
    return jsonify(to_dict(info))


def list_accounts():
    accounts = load_accounts()
    return jsonify({name: {"login": a["login"], "server": a["server"]} for name, a in accounts.items()})


def login(name=None):
    if name:
        # Login by saved account name
        account = get_saved_account(name)
        if not account:
            return jsonify({"error": f"Account '{name}' not found"}), 404
        creds = account
        switch_account(BROKER, name)
    else:
        # Login with credentials in body
        body = request.get_json()
        if not body or "login" not in body or "password" not in body or "server" not in body:
            return jsonify({"error": "login, password, and server required"}), 400
        creds = body

    if not init_mt5(login=creds["login"], password=creds["password"], server=creds["server"]):
        err = mt5.last_error()
        return jsonify({"success": False, "error": f"{err}"}), 500

    info = mt5.account_info()
    return jsonify({
        "success": True,
        "login": info.login if info else None,
        "server": info.server if info else None,
        "balance": info.balance if info else None,
    })
