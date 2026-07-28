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

try:
    from jinja2 import Template
except ImportError:
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "jinja2"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    from jinja2 import Template

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SHARED_DIR = os.path.dirname(_SCRIPTS_DIR)
CONFIG_PATH = os.path.join(_SHARED_DIR, "config", "config.yaml")
VMS_PATH = os.path.join(_SHARED_DIR, "vms.yaml")
COMPOSE_TEMPLATE_PATH = os.path.join(_SHARED_DIR, "docker-compose.yml.j2")
COMPOSE_OUTPUT_PATH = os.path.join(_SHARED_DIR, "docker-compose.yml")
DEFAULT_INSTANCE = "default"
DEFAULT_VM = "default"

MCP_ROUTE_PREFIX = "/mcp/"
MCP_UNIFIER_SERVICE = "mcpunifier"
MCP_UNIFIER_PORT = 6600
# Docker's embedded DNS. nginx needs an explicit resolver to look a hostname up
# at request time instead of at config-parse time.
DOCKER_EMBEDDED_DNS = "127.0.0.11"


def _load():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_vms():
    try:
        with open(VMS_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        vms = data.get("vms", [])
        if not vms:
            return {DEFAULT_VM: {"service": "mt5", "container_name": "mt5"}}
        return {vm["name"]: vm for vm in vms}
    except FileNotFoundError:
        return {DEFAULT_VM: {"service": "mt5", "container_name": "mt5"}}


def _vm_container_name(terminal):
    vms = _load_vms()
    vm_name = terminal.get("vm", DEFAULT_VM)
    vm = vms.get(vm_name)
    if vm:
        return vm.get("container_name", vm.get("service", "mt5"))
    return "mt5"


def _normalize_instance(value):
    if value in (None, ""):
        return DEFAULT_INSTANCE
    return str(value).strip() or DEFAULT_INSTANCE


def _route_prefixes(terminal):
    broker = terminal["broker"]
    account = terminal["account"]
    instance = _normalize_instance(terminal.get("instance"))
    prefixes = [f"/{broker}/{account}/{instance}/"]
    if instance == DEFAULT_INSTANCE:
        prefixes.append(f"/{broker}/{account}/")
    return prefixes


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
            instance = _normalize_instance(t.get("instance"))
            if mode not in ("live", "backtest"):
                mode = "live"
            print(t["broker"], t["account"], instance, t["port"], utc, mode)

    elif cmd == "ports":
        ports = [t["port"] for t in (cfg.get("terminals") or [])]
        if not ports:
            print("6542")
        elif min(ports) == max(ports):
            print(str(min(ports)))
        else:
            print(f"{min(ports)}-{max(ports)}")

    elif cmd == "port_list":
        vm_filter = None
        if len(sys.argv) >= 4 and sys.argv[2] == "--vm":
            vm_filter = sys.argv[3]
        ports = [t["port"] for t in (cfg.get("terminals") or [])
                 if not vm_filter or t.get("vm", DEFAULT_VM) == vm_filter]
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
            # A broker literally named "mcp" would produce /mcp/<account>/,
            # which nginx resolves before the unifier's /mcp/ prefix and would
            # silently shadow it. Refuse rather than ship a confusing route.
            if str(t.get("broker", "")).strip().lower() == MCP_ROUTE_PREFIX.strip("/"):
                print(
                    f"ERROR: broker name '{MCP_ROUTE_PREFIX.strip('/')}' collides with "
                    f"the unified MCP route {MCP_ROUTE_PREFIX}; rename the broker",
                    file=sys.stderr,
                )
                sys.exit(1)
            container = _vm_container_name(t)
            for p in _route_prefixes(t):
                locs.append(
                    f"        location {p} {{\n"
                    f"            rewrite ^{p}(.*)$ /$1 break;\n"
                    f"            proxy_pass http://{container}:{t['port']};\n"
                    f"            proxy_set_header Host $host;\n"
                    f"            proxy_set_header X-Forwarded-For $remote_addr;\n"
                    f"        }}"
                )

        # The unified MCP endpoint: one session that reaches every terminal,
        # selecting which via broker/account tool params. The per-terminal
        # /<broker>/<account>/mcp routes above are unaffected — this is an
        # additional surface, not a replacement.
        # The upstream is reached through a VARIABLE on purpose. nginx resolves a
        # literal proxy_pass hostname while PARSING the config, so if the
        # container is absent nginx refuses to start at all — one missing
        # optional service takes every terminal route down with it. Routing
        # through a variable defers the lookup to request time: nginx starts
        # regardless, every terminal keeps serving, and only this location fails
        # until the unifier is up.
        #
        # That matters because the service genuinely is optional here:
        # docker-compose.yml is gitignored, so pulling this generator does not
        # add the container to a running deployment.
        locs.append(
            f"        location {MCP_ROUTE_PREFIX} {{\n"
            f"            resolver {DOCKER_EMBEDDED_DNS} valid=10s ipv6=off;\n"
            f"            set $mcp_upstream http://{MCP_UNIFIER_SERVICE}:{MCP_UNIFIER_PORT};\n"
            f"            proxy_pass $mcp_upstream;\n"
            f"            proxy_set_header Host $host;\n"
            f"            proxy_set_header X-Forwarded-For $remote_addr;\n"
            f"            proxy_http_version 1.1;\n"
            f"            proxy_buffering off;\n"
            f"            proxy_read_timeout 300s;\n"
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

    elif cmd == "vm_group":
        if len(sys.argv) < 3:
            print("Usage: config_helper.py vm_group <vm_name>", file=sys.stderr)
            sys.exit(1)
        vm_name = sys.argv[2]
        for t in cfg.get("terminals", []):
            if t.get("vm", DEFAULT_VM) == vm_name:
                broker = t["broker"]
                account = t["account"]
                instance = _normalize_instance(t.get("instance"))
                if instance == DEFAULT_INSTANCE:
                    print(f"{broker} {account}")
                else:
                    print(f"{broker} {account} {instance}")

    elif cmd == "vms":
        vms = _load_vms()
        for name in vms:
            print(name)

    elif cmd == "vm_info":
        if len(sys.argv) < 3:
            print("Usage: config_helper.py vm_info <vm_name> [field]", file=sys.stderr)
            sys.exit(1)
        vm_name = sys.argv[2]
        field = sys.argv[3] if len(sys.argv) >= 4 else None
        vms = _load_vms()
        vm = vms.get(vm_name)
        if not vm:
            print(f"ERROR: unknown VM '{vm_name}'", file=sys.stderr)
            sys.exit(1)
        if field:
            print(vm.get(field, ""))
        else:
            print(yaml.dump(vm, default_flow_style=False).strip())

    elif cmd == "generate_compose":
        if not os.path.exists(COMPOSE_TEMPLATE_PATH):
            print(f"ERROR: template not found at {COMPOSE_TEMPLATE_PATH}", file=sys.stderr)
            sys.exit(1)
        with open(COMPOSE_TEMPLATE_PATH, encoding="utf-8") as f:
            template_source = f.read()
        vms = _load_vms()
        vm_list = list(vms.values())
        template = Template(template_source)
        rendered = template.render(vms=vm_list, enable_mcpunifier=True)
        with open(COMPOSE_OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"Generated {COMPOSE_OUTPUT_PATH} from template ({len(vm_list)} VM(s))")

    elif cmd == "show_terminals":
        vms = _load_vms()
        for t in cfg.get("terminals", []):
            instance = _normalize_instance(t.get("instance"))
            vm_name = t.get("vm", DEFAULT_VM)
            vm = vms.get(vm_name, {})
            container = vm.get("container_name", vm.get("service", "mt5"))
            print(f"  - /{t['broker']}/{t['account']}/{instance}/ -> {container}")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
