#!/bin/bash
set -eo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEBLOAT=0
for arg in "$@"; do
    if [ "$arg" = "--debloat" ]; then
        DEBLOAT=1
    fi
done

LOG_FILE="${DIR}/run.log"
exec > >(tee "${LOG_FILE}") 2>&1

mkdir -p "${DIR}/data/storage" "${DIR}/data/shared/scripts" "${DIR}/data/shared/config" "${DIR}/data/shared/terminals" "${DIR}/data/oem"

# Bootstrap docker-compose.yml from example on first run; user owns the real file.
if [ ! -f "${DIR}/docker-compose.yml" ]; then
    if [ -f "${DIR}/docker-compose.yml.example" ]; then
        echo "docker-compose.yml not found — seeding from docker-compose.yml.example"
        cp "${DIR}/docker-compose.yml.example" "${DIR}/docker-compose.yml"
    else
        echo "ERROR: neither docker-compose.yml nor docker-compose.yml.example found."
        exit 1
    fi
fi

# Check for KVM
if [ ! -e /dev/kvm ]; then
    echo "ERROR: /dev/kvm not found. Enable virtualization in BIOS."
    exit 1
fi

# Download win.iso if missing
if [ ! -f "${DIR}/data/win.iso" ]; then
    echo "win.iso not found, downloading tiny11..."
    if ! command -v aria2c &>/dev/null; then
        echo "Installing aria2..."
        sudo apt-get install -y aria2
    fi
    aria2c -x 16 -s 16 --summary-interval=5 \
        -d "${DIR}/data" -o win.iso \
        "https://archive.org/download/tiny-11-NTDEV/tiny11%2023H2%20x64.iso"
fi

# Copy scripts to their mount points
echo "Syncing scripts to mount points..."
cp "${DIR}/scripts/oem-install.bat" "${DIR}/data/oem/install.bat"
cp "${DIR}/scripts/install.bat" "${DIR}/data/shared/scripts/install.bat"
cp "${DIR}/scripts/start.bat" "${DIR}/data/shared/scripts/start.bat"
cp "${DIR}/scripts/api_runner.bat" "${DIR}/data/shared/scripts/api_runner.bat"
cp "${DIR}/scripts/check_health.py" "${DIR}/data/shared/scripts/check_health.py"
cp "${DIR}/scripts/healthcheck.sh" "${DIR}/data/shared/scripts/healthcheck.sh"
chmod +x "${DIR}/data/shared/scripts/healthcheck.sh"
cp "${DIR}/scripts/debloat.bat" "${DIR}/data/shared/scripts/debloat.bat"
rm -rf "${DIR}/data/shared/scripts/defender-remover"
cp -r "${DIR}/scripts/defender-remover" "${DIR}/data/shared/scripts/defender-remover"

# Copy config files
for f in "${DIR}"/config/*; do
    [ -e "$f" ] || continue
    cp -a "$f" "${DIR}/data/shared/config/"
    echo "  copied config: $(basename "$f")"
done

# Copy broker MT5 installers (mt5setup-*.exe)
for f in "${DIR}"/mt5installers/mt5setup-*.exe; do
    [ -f "$f" ] || continue
    echo "Found broker installer: $(basename "$f")"
    cp "$f" "${DIR}/data/shared/terminals/$(basename "$f")"
done

# Copy the mt5api package directory
rm -rf "${DIR}/data/shared/mt5api"
cp -r "${DIR}/mt5api" "${DIR}/data/shared/mt5api"

# Drop debloat flag if requested
if [ "${DEBLOAT}" = "1" ]; then
    echo "Debloat requested — will force re-debloat on next VM boot."
    touch "${DIR}/data/shared/debloat.flag"
fi

# Always clear stale lock dirs (VM may have crashed mid-run)
rm -rf "${DIR}/data/shared/install.running"
rm -rf "${DIR}/data/shared/start.running"

# If VM disk is gone (fresh install), clear done flags so install.bat re-runs everything
if [ ! -f "${DIR}/data/storage/data.img" ]; then
    echo "No VM disk found — clearing done flags for fresh install."
    rm -f "${DIR}/data/shared/"*.done
fi

# terminals.json drives the per-terminal nginx upstreams (and the iptables
# DNAT inside the mt5 container that forwards to the VM). Required.
if [ ! -f "${DIR}/config/terminals.json" ]; then
    echo "ERROR: config/terminals.json not found."
    echo "  Copy config/terminals.example.json and edit."
    exit 1
fi

API_PORTS=$(python3 -c "
import json
ports = [t['port'] for t in json.load(open('${DIR}/config/terminals.json'))]
print(' '.join(str(p) for p in ports))
")
echo "Configured terminal ports (container-internal): ${API_PORTS}"

# Generate fresh .env each run. Vars below are docker-compose interpolation
# inputs; user-managed secrets flow in via config/*.txt files.
: > "${DIR}/.env"

# API_TOKEN flows config/api_token.txt → .env → docker-compose env
if [ -f "${DIR}/config/api_token.txt" ]; then
    API_TOKEN=$(tr -d '[:space:]' < "${DIR}/config/api_token.txt")
    echo "API_TOKEN=${API_TOKEN}" >> "${DIR}/.env"
    echo "API token loaded from config/api_token.txt"
else
    echo "WARNING: config/api_token.txt not found — API will run without auth"
    echo "  Create it: openssl rand -hex 32 > config/api_token.txt"
fi

# Tailscale vars (optional). config/ts_authkey.txt enables the tailscale
# sidecar; config/ts_login_server.txt switches to a Headscale control plane.
TS_AUTHKEY=""
if [ -f "${DIR}/config/ts_authkey.txt" ]; then
    TS_AUTHKEY=$(tr -d '[:space:]' < "${DIR}/config/ts_authkey.txt")
    echo "TS_AUTHKEY=${TS_AUTHKEY}" >> "${DIR}/.env"
    echo "Tailscale auth key loaded from config/ts_authkey.txt"
fi
if [ -f "${DIR}/config/ts_login_server.txt" ]; then
    TS_LOGIN_SERVER=$(tr -d '[:space:]' < "${DIR}/config/ts_login_server.txt")
    echo "TS_EXTRA_ARGS=--accept-dns=false --login-server=${TS_LOGIN_SERVER}" >> "${DIR}/.env"
    echo "Headscale login server: ${TS_LOGIN_SERVER}"
fi

# Always generate nginx.conf from terminals.json. nginx is the single
# entry point — routes /<broker>/<account>/... to mt5:<terminal_port>
# (docker DNS), where the mt5 container's iptables DNATs into the VM.
mkdir -p "${DIR}/.data/nginx"
python3 - "${DIR}/config/terminals.json" "${DIR}/.data/nginx/nginx.conf" <<'PYEOF'
import json, sys
terms_path, nginx_path = sys.argv[1:3]
terms = json.load(open(terms_path))
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
    + "\n".join(locs) + "\n"
    "        location / { return 404 \"no route\\n\"; }\n"
    "    }\n"
    "}\n"
)
with open(nginx_path, "w") as f:
    f.write(nginx_conf)
PYEOF
echo "nginx config generated from terminals.json"

# Tailscale Serve config used to be generated as a static serve.json
# loaded via TS_SERVE_CONFIG. That doesn't work reliably across both
# stock Tailscale and Headscale because the Web handler key has to be
# the node's actual FQDN — which we don't know until tailscaled has
# authenticated. ${TS_CERT_DOMAIN} substitution doesn't happen on
# Headscale (it resolves to literal "no-https"), and a port-only ":80"
# key gets ignored on tailscaled's HTTP dispatch path.
#
# Fix: don't write serve.json at all. After `docker compose up -d`,
# wait for the tailscale sidecar to be authenticated, then run
# `tailscale serve --bg --http=80 http://nginx:80` inside it. The CLI
# fills in the FQDN from local tailscaled state and persists the
# config to /var/lib/tailscale (mounted at .data/tailscale/state),
# so it survives container restarts without us touching it again.
mkdir -p "${DIR}/.data/tailscale/state"

# Stop existing container if running
if docker compose -f "${DIR}/docker-compose.yml" ps -q 2>/dev/null | grep -q .; then
    echo "Container is already running."
    read -p "Stop and restart? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
    docker compose -f "${DIR}/docker-compose.yml" down
fi

echo "Starting MT5 Windows VM..."
docker compose -f "${DIR}/docker-compose.yml" up -d

API_HOST_PORT="${API_HOST_PORT:-8888}"
echo ""
echo "Container starting. Windows will install on first run (~10-15 min)."
echo ""
echo "  noVNC: http://localhost:${NOVNC_PORT:-8006}"
echo "  API entry: http://localhost:${API_HOST_PORT} (nginx, loopback-only)"
python3 -c "
import json
for t in json.load(open('${DIR}/config/terminals.json')):
    print(f\"    http://localhost:${API_HOST_PORT}/{t['broker']}/{t['account']}/\")
"
if [ -n "${TS_AUTHKEY}" ]; then
    echo "  Tailnet: http://mt5-httpapi/<broker>/<account>/"
fi
echo ""
echo "Logs: docker compose -f ${DIR}/docker-compose.yml logs -f"

# Set up DNAT inside the mt5 container so traffic arriving on per-terminal
# ports gets forwarded to the VM. Source is now nginx (or any container on
# the default network) hitting mt5:<port> — PREROUTING fires regardless of
# source since it hooks per-packet on mt5's eth0.
echo ""
echo "Waiting for VM to get an IP (for API port forwarding)..."
for _ in $(seq 1 60); do
    VM_IP=$(docker compose -f "${DIR}/docker-compose.yml" exec -T mt5 bash -c 'cat /var/lib/misc/dnsmasq.leases 2>/dev/null | awk "{print \$3}"' 2>/dev/null || true)
    if [ -n "${VM_IP}" ]; then
        echo "VM IP: ${VM_IP}"
        for PORT in ${API_PORTS}; do
            docker compose -f "${DIR}/docker-compose.yml" exec -T mt5 bash -c "
                iptables -t nat -C PREROUTING -p tcp --dport ${PORT} -j DNAT --to-destination ${VM_IP}:${PORT} 2>/dev/null || \
                iptables -t nat -A PREROUTING -p tcp --dport ${PORT} -j DNAT --to-destination ${VM_IP}:${PORT}
                iptables -t nat -C POSTROUTING -p tcp -d ${VM_IP} --dport ${PORT} -j MASQUERADE 2>/dev/null || \
                iptables -t nat -A POSTROUTING -p tcp -d ${VM_IP} --dport ${PORT} -j MASQUERADE
                iptables -C FORWARD -p tcp -d ${VM_IP} --dport ${PORT} -j ACCEPT 2>/dev/null || \
                iptables -A FORWARD -p tcp -d ${VM_IP} --dport ${PORT} -j ACCEPT
            "
            echo "Port forwarding: nginx -> mt5:${PORT} -> VM:${PORT}"
        done
        break
    fi
    sleep 5
done

if [ -z "${VM_IP}" ]; then
    echo "WARNING: Could not detect VM IP. Port forwarding not set up."
    echo "Re-run this script after the VM boots."
fi

# Wire Tailscale Serve via the CLI inside the sidecar. We do this here
# (not via TS_SERVE_CONFIG) because the Web handler needs the node's
# actual FQDN as a key, and the CLI is the only thing that knows it
# both on stock Tailscale and Headscale. The result persists in
# /var/lib/tailscale state, so it stays wired across restarts.
if [ -n "${TS_AUTHKEY}" ] && docker compose -f "${DIR}/docker-compose.yml" ps --services --filter status=running 2>/dev/null | grep -qx tailscale; then
    echo ""
    echo "Waiting for Tailscale to authenticate..."
    TS_READY=0
    for _ in $(seq 1 30); do
        if docker compose -f "${DIR}/docker-compose.yml" exec -T tailscale tailscale status >/dev/null 2>&1; then
            TS_READY=1
            break
        fi
        sleep 2
    done
    if [ "${TS_READY}" = "1" ]; then
        # Idempotent: reset to clear any stale config from a previous
        # serve.json era, then install the single Web handler.
        docker compose -f "${DIR}/docker-compose.yml" exec -T tailscale sh -c '
            tailscale serve reset >/dev/null 2>&1 || true
            tailscale serve --bg --http=80 http://nginx:80
        ' >/dev/null && echo "Tailscale Serve wired: tailnet :80 → nginx:80" \
                     || echo "WARNING: tailscale serve setup failed; check 'docker compose logs tailscale'"
    else
        echo "WARNING: Tailscale didn't authenticate in time; serve not wired."
        echo "  After it comes up, run:"
        echo "    docker compose exec tailscale tailscale serve --bg --http=80 http://nginx:80"
    fi
fi
