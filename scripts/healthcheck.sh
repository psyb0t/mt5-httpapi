#!/bin/sh
# Probes every API port from config.yaml.
# Healthy ONLY if every configured port answers HTTP.
#
# Resolves VM IP from dnsmasq leases because dockurr/windows uses iptables
# PREROUTING to forward host:PORT -> VM_IP:PORT — that chain is NOT traversed
# for traffic originating inside the container, so localhost:PORT won't work
# from here. Falls back to localhost / 127.0.0.1 if leases aren't readable yet.

set -u

CONFIG=/shared/config/config.yaml
[ -f "$CONFIG" ] || { echo "no config.yaml"; exit 1; }

# YAML schema: only terminals[] entries have a `port:` key, so a plain grep
# on indented `port: <int>` lines is sufficient and avoids needing python
# / a yaml parser inside the alpine-based dockurr/windows container.
PORTS=$(grep -E '^[[:space:]]+port:[[:space:]]*[0-9]+' "$CONFIG" | grep -oE '[0-9]+$')
[ -n "$PORTS" ] || { echo "no ports parsed"; exit 1; }

VM_IP=$(awk '{print $3}' /var/lib/misc/dnsmasq.leases 2>/dev/null | head -n1)

HOSTS=""
[ -n "$VM_IP" ] && HOSTS="$VM_IP"
HOSTS="$HOSTS 127.0.0.1 localhost"

dead=""
for p in $PORTS; do
    found=0
    for host in $HOSTS; do
        code=$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' \
            "http://$host:$p/ping" 2>/dev/null || echo 000)
        if [ -n "$code" ] && [ "$code" != "000" ]; then
            found=1
            break
        fi
    done
    [ "$found" -eq 0 ] && dead="$dead $p"
done

if [ -n "$dead" ]; then
    echo "DOWN ports:$dead (vm_ip=$VM_IP)"
    exit 1
fi

echo "ok all ports up: $PORTS (vm_ip=$VM_IP)"
exit 0
