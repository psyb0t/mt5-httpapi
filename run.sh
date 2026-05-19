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

mkdir -p "${DIR}/data/storage" "${DIR}/data/shared/scripts" "${DIR}/data/shared/config" "${DIR}/data/shared/terminals" "${DIR}/data/oem" "${DIR}/assets/experts" "${DIR}/assets/sets"

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
if [ ! -e /dev/kvm ] && [ -z "$SKIP_KVM_CHECK" ]; then
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
cp "${DIR}/scripts/compile-warmup-ea.bat" "${DIR}/data/shared/scripts/compile-warmup-ea.bat"
cp "${DIR}/scripts/check_health.py" "${DIR}/data/shared/scripts/check_health.py"
cp "${DIR}/scripts/config_helper.py" "${DIR}/data/shared/scripts/config_helper.py"
cp "${DIR}/scripts/event-log-tailer.ps1" "${DIR}/data/shared/scripts/event-log-tailer.ps1"
cp "${DIR}/scripts/healthcheck.sh" "${DIR}/data/shared/scripts/healthcheck.sh"
chmod +x "${DIR}/data/shared/scripts/healthcheck.sh"
cp "${DIR}/scripts/debloat.bat" "${DIR}/data/shared/scripts/debloat.bat"
rm -rf "${DIR}/data/shared/scripts/defender-remover"
cp -r "${DIR}/scripts/defender-remover" "${DIR}/data/shared/scripts/defender-remover"

# config.yaml is the single config file — fail fast if missing.
if [ ! -f "${DIR}/config/config.yaml" ]; then
    echo "ERROR: config/config.yaml not found."
    echo "  Copy config/config.yaml.example to config/config.yaml and edit."
    exit 1
fi

# Copy config
cp -a "${DIR}/config/config.yaml" "${DIR}/data/shared/config/config.yaml"
echo "  copied config: config.yaml"
# Copy optional extras (hosts, setup.bat) if present
for f in "${DIR}/config/hosts" "${DIR}/config/setup.bat"; do
    [ -f "$f" ] || continue
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

CFG="${DIR}/scripts/config_helper.py"
python3 -c "import yaml" 2>/dev/null || pip3 install --quiet pyyaml

API_PORTS=$(python3 "$CFG" port_list)
echo "Configured terminal ports (container-internal): ${API_PORTS}"

# Generate fresh .env each run.
: > "${DIR}/.env"

API_TOKEN=$(python3 "$CFG" api_token)
if [ -n "${API_TOKEN}" ]; then
    echo "API_TOKEN=${API_TOKEN}" >> "${DIR}/.env"
    echo "API token loaded from config.yaml"
else
    echo "WARNING: api_token is empty in config.yaml — API will run without auth"
fi

TS_AUTHKEY=$(python3 "$CFG" ts_auth_key)
TS_AUTHKEY="${TS_AUTHKEY//[$'\t\r\n ']}"
if [ -n "${TS_AUTHKEY}" ]; then
    echo "TS_AUTHKEY=${TS_AUTHKEY}" >> "${DIR}/.env"
    echo "Tailscale auth key loaded from config.yaml"
fi
TS_LOGIN_SERVER=$(python3 "$CFG" ts_login_server)
TS_LOGIN_SERVER="${TS_LOGIN_SERVER//[$'\t\r\n ']}"
if [ -n "${TS_LOGIN_SERVER}" ]; then
    echo "TS_EXTRA_ARGS=--accept-dns=false --login-server=${TS_LOGIN_SERVER}" >> "${DIR}/.env"
    echo "Headscale login server: ${TS_LOGIN_SERVER}"
fi

# Generate nginx.conf from config.yaml terminals.
mkdir -p "${DIR}/.data/nginx"
python3 "$CFG" nginx_conf "${DIR}/.data/nginx/nginx.conf"
echo "nginx config generated from config.yaml"

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
python3 "$CFG" show_terminals | sed "s|  - /|    http://localhost:${API_HOST_PORT}/|"
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
