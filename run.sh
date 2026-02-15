#!/bin/bash
set -eo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REINSTALL=0
if [ "${1:-}" = "--reinstall" ]; then
    REINSTALL=1
fi

LOG_FILE="${DIR}/run.log"
exec > >(tee "${LOG_FILE}") 2>&1

mkdir -p "${DIR}/data/storage" "${DIR}/data/metatrader5" "${DIR}/data/oem"

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
cp "${DIR}/scripts/install.bat" "${DIR}/data/oem/install.bat"
cp "${DIR}/scripts/install.bat" "${DIR}/data/metatrader5/install.bat"
for f in setup.bat requirements.txt account.json.example; do
    if [ -f "${DIR}/config/${f}" ]; then
        cp "${DIR}/config/${f}" "${DIR}/data/metatrader5/${f}"
    fi
done
cp "${DIR}/scripts/start-mt5.bat" "${DIR}/data/metatrader5/start-mt5.bat"
# Copy broker MT5 installers (mt5setup-*.exe)
for f in "${DIR}"/mt5installers/mt5setup-*.exe; do
    [ -f "$f" ] || continue
    echo "Found broker installer: $(basename "$f")"
    cp "$f" "${DIR}/data/metatrader5/$(basename "$f")"
done
# Copy terminal config (broker + account selection)
cp "${DIR}/config/terminal.json" "${DIR}/data/metatrader5/terminal.json"
echo "MT5 terminal config: $(cat "${DIR}/config/terminal.json")"
# Copy the mt5api package directory
rm -rf "${DIR}/data/metatrader5/mt5api"
cp -r "${DIR}/mt5api" "${DIR}/data/metatrader5/mt5api"
# Copy account.json only if it doesn't already exist in metatrader5
# (the API server may have updated it at runtime)
if [ ! -f "${DIR}/data/metatrader5/account.json" ] && [ -f "${DIR}/config/account.json" ]; then
    cp "${DIR}/config/account.json" "${DIR}/data/metatrader5/account.json"
fi

# Drop reinstall flag if requested
if [ "${REINSTALL}" = "1" ]; then
    echo "Reinstall requested — install.bat will run on next VM boot."
    touch "${DIR}/data/metatrader5/reinstall.flag"
fi

# Stop existing container if running
# Stop existing container if running
if docker compose -f "${DIR}/docker-compose.yml" ps -q 2>/dev/null | grep -q .; then
    echo "Stopping existing container..."
    docker compose -f "${DIR}/docker-compose.yml" down
fi

echo "Starting MT5 Windows VM..."
docker compose -f "${DIR}/docker-compose.yml" up -d

echo ""
echo "Container starting. Windows will install on first run (~10-15 min)."
echo ""
echo "  noVNC: http://localhost:${NOVNC_PORT:-8006}"
echo "  API:   http://localhost:${API_PORT:-6542}/ping"
echo ""
echo "Logs: docker compose -f ${DIR}/docker-compose.yml logs -f"

# Set up port forwarding from container to Windows VM for the HTTP API
echo ""
echo "Waiting for VM to get an IP (for API port forwarding)..."
for i in $(seq 1 60); do
    VM_IP=$(docker compose -f "${DIR}/docker-compose.yml" exec -T metatrader5 bash -c 'cat /var/lib/misc/dnsmasq.leases 2>/dev/null | awk "{print \$3}"' 2>/dev/null || true)
    if [ -n "${VM_IP}" ]; then
        echo "VM IP: ${VM_IP}"
        docker compose -f "${DIR}/docker-compose.yml" exec -T metatrader5 bash -c "
            iptables -t nat -C PREROUTING -p tcp --dport 6542 -j DNAT --to-destination ${VM_IP}:6542 2>/dev/null || \
            iptables -t nat -A PREROUTING -p tcp --dport 6542 -j DNAT --to-destination ${VM_IP}:6542
            iptables -t nat -C POSTROUTING -p tcp -d ${VM_IP} --dport 6542 -j MASQUERADE 2>/dev/null || \
            iptables -t nat -A POSTROUTING -p tcp -d ${VM_IP} --dport 6542 -j MASQUERADE
            iptables -C FORWARD -p tcp -d ${VM_IP} --dport 6542 -j ACCEPT 2>/dev/null || \
            iptables -A FORWARD -p tcp -d ${VM_IP} --dport 6542 -j ACCEPT
        "
        echo "Port forwarding: host:${API_PORT:-6542} -> container:6542 -> VM:6542"
        break
    fi
    sleep 5
done

if [ -z "${VM_IP}" ]; then
    echo "WARNING: Could not detect VM IP. Port forwarding not set up."
    echo "Re-run this script after the VM boots."
fi
