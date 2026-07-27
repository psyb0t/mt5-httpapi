#!/bin/sh
# Probes every API port from config.yaml.
# Healthy ONLY if every configured port answers HTTP.
#
# Resolves VM IP from dnsmasq leases because dockurr/windows uses iptables
# PREROUTING to forward host:PORT -> VM_IP:PORT — that chain is NOT traversed
# for traffic originating inside the container, so localhost:PORT won't work
# from here. Falls back to localhost / 127.0.0.1 if leases aren't readable yet.
#
# POSIX sh (not bash) because the dockurr/windows container is alpine-based
# and has no guaranteed bash. Hence `[ ]` over `[[ ]]`.
#
# Deliberately NO `set -e` / `set -o pipefail`: this script's whole job is
# running probes that are EXPECTED to fail so it can count them. `set -e`
# would abort on the first dead port and never reach the verdict. stdout here
# is the health verdict Docker surfaces via `docker inspect`, so plain `echo`
# is the script's real output, not diagnostic logging.

set -u

readonly CONFIG=/shared/config/config.yaml
readonly DNSMASQ_LEASES=/var/lib/misc/dnsmasq.leases
readonly PROBE_PATH=/ping
readonly PROBE_TIMEOUT_SECONDS=3
# Tried after the leased VM IP so a probe still works before the lease lands.
readonly FALLBACK_HOSTS='127.0.0.1 localhost'

[ -f "$CONFIG" ] || {
    echo "no config.yaml at $CONFIG"
    exit 1
}

# YAML schema: only terminals[] entries have a `port:` key, so a plain grep
# on indented `port: <int>` lines is sufficient and avoids needing python
# / a yaml parser inside the alpine-based dockurr/windows container.
PORTS=$(grep -E '^[[:space:]]+port:[[:space:]]*[0-9]+' "$CONFIG" | grep -oE '[0-9]+$')
[ -n "$PORTS" ] || {
    echo "no ports parsed from $CONFIG"
    exit 1
}

# Absent/unreadable leases file is normal on early boot — the fallback hosts
# cover that case, so an empty VM_IP is not an error here.
VM_IP=$(awk '{print $3}' "$DNSMASQ_LEASES" 2>/dev/null | head -n1)

HOSTS=""
[ -n "$VM_IP" ] && HOSTS="$VM_IP"
HOSTS="$HOSTS $FALLBACK_HOSTS"

dead=""
for p in $PORTS; do
    found=0
    for host in $HOSTS; do
        # NO `|| echo 000` here. curl's -w ALREADY prints 000 on a failed
        # connection AND exits non-zero, so that fallback appended a SECOND
        # 000 -> "000000", which compared unequal to "000" and marked every
        # dead port as UP. This check could therefore never fail: a total
        # outage sat behind a green healthcheck for hours because Docker was
        # told everything was fine.
        code=$(curl -s --max-time "$PROBE_TIMEOUT_SECONDS" -o /dev/null \
            -w '%{http_code}' "http://$host:$p$PROBE_PATH" 2>/dev/null)
        # Whitelist the valid shape instead of blacklisting one bad string, so
        # any future malformed value fails CLOSED rather than open. Empty means
        # curl is missing or crashed. Any real HTTP status — including 4xx/5xx,
        # e.g. a 401 from the auth layer — proves the process is listening.
        case "$code" in
        [1-5][0-9][0-9])
            found=1
            break
            ;;
        esac
    done
    [ "$found" -eq 0 ] && dead="$dead $p"
done

if [ -n "$dead" ]; then
    echo "DOWN ports:$dead (vm_ip=$VM_IP)"
    exit 1
fi

echo "ok all ports up: $PORTS (vm_ip=$VM_IP)"
exit 0
