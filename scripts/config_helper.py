#!/usr/bin/env python3
"""
Query helper for config/config.yaml.
Called from run.sh (Linux) and bat scripts (Windows).
Resolves config path relative to this script: ../config/config.yaml
"""
import os
import sys

try:
    import yaml
except ImportError:
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "pyyaml"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import yaml

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SHARED_DIR = os.path.dirname(_SCRIPTS_DIR)
CONFIG_PATH = os.path.join(_SHARED_DIR, "config", "config.yaml")


def _load():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    if len(sys.argv) < 2:
        print("Usage: config_helper.py <cmd> [args...]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    try:
        cfg = _load()
    except FileNotFoundError:
        print(f"ERROR: config.yaml not found at {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    if cmd == "terminals":
        for t in cfg.get("terminals") or []:
            utc = t.get("utc_offset")
            utc = "0" if utc is None else str(utc).replace(" ", "")
            mode = (t.get("mode") or "live").strip().lower() or "live"
            if mode not in ("live", "backtest"):
                mode = "live"
            print(t["broker"], t["account"], t["port"], utc, mode)

    elif cmd == "ports":
        ports = [t["port"] for t in (cfg.get("terminals") or [])]
        if not ports:
            print("6542")
        elif min(ports) == max(ports):
            print(str(min(ports)))
        else:
            print(f"{min(ports)}-{max(ports)}")

    elif cmd == "port_list":
        ports = [t["port"] for t in (cfg.get("terminals") or [])]
        print(" ".join(str(p) for p in ports) if ports else "6542")

    elif cmd == "api_token":
        print(cfg.get("api_token") or "")

    elif cmd == "ts_auth_key":
        print((cfg.get("tailscale") or {}).get("auth_key") or "")

    elif cmd == "ts_login_server":
        print((cfg.get("tailscale") or {}).get("login_server") or "")

    elif cmd == "reboot_interval":
        val = cfg.get("reboot_interval")
        print(30 if val is None else val)

    elif cmd == "requirements":
        for r in cfg.get("requirements") or []:
            print(r)

    elif cmd == "write_ini":
        if len(sys.argv) < 5:
            print("Usage: config_helper.py write_ini <broker> <account> <outpath>", file=sys.stderr)
            sys.exit(1)
        broker, account, outpath = sys.argv[2], sys.argv[3], sys.argv[4]
        accounts = cfg.get("accounts", {})
        b = accounts.get(broker, {})
        creds = b.get(account) if account else next(iter(b.values()), None) if b else None
        ini = "[Common]\n"
        if creds:
            ini += f"Login={creds['login']}\n"
            ini += f"Server={creds['server']}\n"
            ini += f"Password={creds['password']}\n"
        ini += "KeepPrivate=0\nAutoTrading=1\nNewsEnable=0\n"
        ini += "[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n"
        ini += "[Email]\nEnable=0\n"
        with open(outpath, "w", encoding="utf-8") as f:
            f.write(ini)

    elif cmd == "nginx_conf":
        if len(sys.argv) < 3:
            print("Usage: config_helper.py nginx_conf <outpath>", file=sys.stderr)
            sys.exit(1)
        outpath = sys.argv[2]
        terms = cfg.get("terminals", [])
        locs = []
        for t in terms:
            p = f"/{t['broker']}/{t['account']}/"
            locs.append(
                f"        location {p} {{\n"
                f"            rewrite ^{p}(.*)$ /$1 break;\n"
                f"            proxy_pass http://mt5:{t['port']};\n"
                f"            proxy_set_header Host $host;\n"
                f"            proxy_set_header X-Forwarded-For $remote_addr;\n"
                f"        }}"
            )
        nginx_conf = (
            "events {}\n"
            "http {\n"
            "    server {\n"
            "        listen 80;\n"
            "        client_max_body_size 25m;\n"
            "        client_body_timeout 120s;\n"
            + "\n".join(locs) + "\n"
            "        location / { return 404 \"no route\\n\"; }\n"
            "    }\n"
            "}\n"
        )
        with open(outpath, "w", encoding="utf-8") as f:
            f.write(nginx_conf)

    elif cmd == "show_terminals":
        for t in cfg.get("terminals", []):
            print(f"  - /{t['broker']}/{t['account']}/")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
