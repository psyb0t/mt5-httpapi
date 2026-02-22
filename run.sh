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

mkdir -p "${DIR}/data/storage" "${DIR}/data/shared/scripts" "${DIR}/data/shared/config" "${DIR}/data/shared/brokers" "${DIR}/data/oem"

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
    cp "$f" "${DIR}/data/shared/brokers/$(basename "$f")"
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

# Generate .env with port range from terminals.json for docker-compose
if [ -f "${DIR}/config/terminals.json" ]; then
    PORTS=$(python3 -c "
import json
ports = [t['port'] for t in json.load(open('${DIR}/config/terminals.json'))]
print(min(ports), max(ports))
" 2>/dev/null)
    read -r PORT_MIN PORT_MAX <<< "$PORTS"
    if [ "$PORT_MIN" = "$PORT_MAX" ]; then
        echo "API_PORT_RANGE=${PORT_MIN}" > "${DIR}/.env"
    else
        echo "API_PORT_RANGE=${PORT_MIN}-${PORT_MAX}" > "${DIR}/.env"
    fi
    API_PORTS=$(python3 -c "
import json
ports = [t['port'] for t in json.load(open('${DIR}/config/terminals.json'))]
print(' '.join(str(p) for p in ports))
" 2>/dev/null)
    echo "Configured API ports: ${API_PORTS}"
else
    echo "API_PORT_RANGE=6542" > "${DIR}/.env"
    API_PORTS="6542"
fi

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

echo ""
echo "Container starting. Windows will install on first run (~10-15 min)."
echo ""
echo "  noVNC: http://localhost:${NOVNC_PORT:-8006}"
for PORT in ${API_PORTS}; do
    echo "  API:   http://localhost:${PORT}/ping"
done
echo ""
echo "Logs: docker compose -f ${DIR}/docker-compose.yml logs -f"

# Set up port forwarding from container to Windows VM for the HTTP API
echo ""
echo "Waiting for VM to get an IP (for API port forwarding)..."
for i in $(seq 1 60); do
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
            echo "Port forwarding: host:${PORT} -> container:${PORT} -> VM:${PORT}"
        done
        break
    fi
    sleep 5
done

if [ -z "${VM_IP}" ]; then
    echo "WARNING: Could not detect VM IP. Port forwarding not set up."
    echo "Re-run this script after the VM boots."
fi
