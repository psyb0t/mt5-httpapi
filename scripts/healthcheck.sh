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
# Per-VM terminal list, bind-mounted by docker-compose from
# data/vm-group-<vm>.txt. Absent on single-VM installs, which means no filter.
readonly VM_GROUP=/shared/config/vm-group.txt
readonly DNSMASQ_LEASES=/var/lib/misc/dnsmasq.leases
readonly PROBE_PATH=/ping
readonly PROBE_TIMEOUT_SECONDS=3
# Tried after the leased VM IP so a probe still works before the lease lands.
readonly FALLBACK_HOSTS='127.0.0.1 localhost'

[ -f "$CONFIG" ] || {
    echo "no config.yaml at $CONFIG"
    exit 1
}

# Walk terminals[] keeping only the ones this VM actually hosts. The old plain
# grep for `port:` took every port in config.yaml, so on a multi-VM install each
# VM probed the other VM's terminals, found them down, and reported unhealthy
# forever — the fast VM sat at a 25,272-long failing streak listing only
# bulk-VM ports while all 12 of its own terminals were serving. A healthcheck
# that is always red says nothing, and hides the outage it exists to catch.
#
# awk, not python: the dockurr/windows container is alpine and has no python.
# The group file is read inside BEGIN rather than as a second input file
# because the usual FNR==NR idiom misreads the config as the group list when
# that file is empty, which would emit zero ports and fail every single-VM
# install. No group file => no filter, same ports as before.
PORTS=$(awk -v groupfile="$VM_GROUP" '
    function clean(s) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); gsub(/^"|"$/, "", s); return s
    }
    function value(line,   v) {
        v = line; sub(/^[^:]*:/, "", v); sub(/,[[:space:]]*$/, "", v); return clean(v)
    }
    function emit() {
        if (port == "") return
        if (!have_group) { print port; return }
        if ((broker SUBSEP account SUBSEP (instance == "" ? "default" : instance)) in allowed)
            print port
    }
    function reset() { broker = ""; account = ""; instance = ""; port = "" }
    BEGIN {
        if (groupfile != "") {
            while ((getline line < groupfile) > 0) {
                sub(/#.*/, "", line)
                n = split(line, f, /[[:space:]]+/)
                cnt = 0
                for (i = 1; i <= n; i++) if (f[i] != "") { cnt++; g[cnt] = f[i] }
                if (cnt >= 2) {
                    allowed[g[1] SUBSEP g[2] SUBSEP (cnt >= 3 ? g[3] : "default")] = 1
                    have_group = 1
                }
            }
            close(groupfile)
        }
    }
    /^[[:space:]]*-[[:space:]]*"?broker"?[[:space:]]*:/ {
        emit(); reset(); broker = value($0); next
    }
    /^[[:space:]]*"?account"?[[:space:]]*:/  { account  = value($0); next }
    /^[[:space:]]*"?instance"?[[:space:]]*:/ { instance = value($0); next }
    /^[[:space:]]*"?port"?[[:space:]]*:/     { port     = value($0); next }
    END { emit() }
' "$CONFIG")
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

# Unquoted on purpose: collapses the newline-separated list onto one line, so
# the verdict `docker inspect` shows stays readable.
echo "ok all ports up:" $PORTS "(vm_ip=$VM_IP)"
exit 0
