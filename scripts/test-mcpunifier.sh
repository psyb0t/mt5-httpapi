#!/usr/bin/env bash
# End-to-end harness for the mt5-httpapi MCP unifier.
#
# Every resource carries RESOURCE_PREFIX and teardown runs from an EXIT trap,
# so cleanup happens on success, on failure, and on interrupt alike — nothing
# survives the script. The prefix is also swept on entry, so a run killed hard
# (SIGKILL, trap never fires) still gets cleaned up by the next invocation.
set -euo pipefail
trap 'printf "{\"level\":\"ERROR\",\"file\":\"%s\",\"line\":%d,\"msg\":\"command failed exit=%d\"}\n" "${BASH_SOURCE[0]##*/}" "${LINENO}" "$?" >&2' ERR

LOG_FILE="${LOG_FILE:-/tmp/$(basename "$0" .sh).log}"
exec > >(tee -a "${LOG_FILE}") 2>&1

readonly RESOURCE_PREFIX="mcpu-e2e"
readonly IMAGE="${RESOURCE_PREFIX}:local"
readonly NETWORK="${RESOURCE_PREFIX}-net"
readonly UNIFIER_CONTAINER="${RESOURCE_PREFIX}-unifier"
readonly TERMINAL_CONTAINER="${RESOURCE_PREFIX}-terminal"
readonly HOST_PORT=6699
readonly READY_TIMEOUT_SECONDS=30
readonly LIVE_TERMINAL_PORT=6545
readonly DOWN_TERMINAL_PORT=6542
readonly EXPECTED_TOOL_COUNT=25
readonly DIAGNOSE_LOG_LINES=20

# Resolved from this script's own location so the harness works from any cwd
# and needs no absolute path baked in.
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)}"

# Fixtures MUST live under the repo, not /tmp. When the docker daemon is remote
# or belongs to a host that mounts this repo at the same path (the dev-container
# case), a bind-mount source is resolved on the DAEMON's filesystem. A directory
# made by `mktemp -d` under /tmp exists only in this process's namespace, so the
# daemon would silently bind an empty dir and the service would come up with no
# config at all. `.data/` is gitignored, so fixtures never reach a commit.
readonly FIXTURE_ROOT="${REPO_DIR}/.data"
WORK_DIR=""
FAILURES=0

log() {
    local level="$1"
    shift
    local ts file line func
    ts=$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')
    file="${BASH_SOURCE[1]##*/}"
    line="${BASH_LINENO[0]}"
    func="${FUNCNAME[1]:-main}"
    printf '{"time":"%s","level":"%s","file":"%s","line":%d,"func":"%s","msg":"%s"}\n' \
        "${ts}" "${level}" "${file}" "${line}" "${func}" "$*" >&2
}

usage() {
    cat <<'EOF'
Usage: test-mcpunifier.sh [test|clean]

  test   build the unifier image and run the assertion suite (default)
  clean  tear down any leftover resources from a previous run, then exit

Every resource this script creates is named with the "mcpu-e2e" prefix and is
removed by an EXIT trap, so no container, network, image or temp dir outlives
the run.
EOF
}

# Removes exactly the resources this script names. Safe when they do not exist;
# never touches anything without the prefix.
cleanup() {
    local status=$?
    log INFO "cleanup start prefix=${RESOURCE_PREFIX}"

    local container
    for container in "${UNIFIER_CONTAINER}" "${TERMINAL_CONTAINER}"; do
        if docker container inspect "${container}" >/dev/null 2>&1; then
            docker container rm --force "${container}" >/dev/null 2>&1 ||
                log WARN "could not remove container name=${container}"
            log DEBUG "removed container name=${container}"
        fi
    done

    if docker network inspect "${NETWORK}" >/dev/null 2>&1; then
        docker network rm "${NETWORK}" >/dev/null 2>&1 ||
            log WARN "could not remove network name=${NETWORK}"
        log DEBUG "removed network name=${NETWORK}"
    fi

    if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
        docker image rm --force "${IMAGE}" >/dev/null 2>&1 ||
            log WARN "could not remove image name=${IMAGE}"
        log DEBUG "removed image name=${IMAGE}"
    fi

    if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
        rm -rf -- "${WORK_DIR}"
        log DEBUG "removed work dir path=${WORK_DIR}"
    fi

    log INFO "cleanup done exit=${status}"
    return "${status}"
}
trap cleanup EXIT

# A run killed without its trap firing leaves resources behind. Sweep the
# prefix before starting so the harness is idempotent instead of accumulating.
sweep_stale() {
    local stale
    stale=$(docker ps -aq --filter "name=^${RESOURCE_PREFIX}-" 2>/dev/null || true)
    if [[ -n "${stale}" ]]; then
        log WARN "sweeping stale containers from a previous run"
        # shellcheck disable=SC2086  # word-splitting is intended: one id per arg
        docker container rm --force ${stale} >/dev/null 2>&1 ||
            log WARN "stale sweep incomplete"
    fi
}

# Dumps why a container is not answering, BEFORE cleanup destroys the evidence.
# A harness that tears down silently on failure is unusable: the logs it just
# deleted are the whole reason the run failed.
diagnose() {
    local container
    for container in "${UNIFIER_CONTAINER}" "${TERMINAL_CONTAINER}"; do
        if ! docker container inspect "${container}" >/dev/null 2>&1; then
            log WARN "diagnose name=${container} reason=container_absent"
            continue
        fi
        log WARN "diagnose name=${container} state=$(docker container inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' "${container}")"
        docker logs --tail "${DIAGNOSE_LOG_LINES}" "${container}" 2>&1 |
            sed "s/^/    [${container}] /" >&2
    done
}

wait_for_http() {
    local url="$1"
    local deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
    while [[ "${SECONDS}" -lt "${deadline}" ]]; do
        if curl -sf -o /dev/null --max-time 2 "${url}"; then
            log DEBUG "ready url=${url}"
            return 0
        fi
        sleep 1
    done
    log ERROR "timeout url=${url} seconds=${READY_TIMEOUT_SECONDS} reason=never_ready"
    diagnose
    return 1
}

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "${expected}" == "${actual}" ]]; then
        printf '  PASS  %-44s %s\n' "${label}" "${actual}"
        return 0
    fi
    printf '  FAIL  %-44s expected=%s actual=%s\n' "${label}" "${expected}" "${actual}"
    FAILURES=$((FAILURES + 1))
}

http_code() {
    curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@"
}

mcp_call() {
    local base="$1" tool="$2" args="$3"
    curl -s -X POST "${base}/mcp" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"${tool}\",\"arguments\":${args}}}"
}

write_fixtures() {
    mkdir -p "${FIXTURE_ROOT}"
    WORK_DIR=$(mktemp -d "${FIXTURE_ROOT}/${RESOURCE_PREFIX}-XXXXXX")
    log INFO "fixtures path=${WORK_DIR}"

    cat >"${WORK_DIR}/config.yaml" <<EOF
api_token: ""
terminals:
  - broker: ftmo
    account: tenkchallenge
    port: ${LIVE_TERMINAL_PORT}
    mode: demo
  - broker: roboforex
    account: procent
    port: ${DOWN_TERMINAL_PORT}
    mode: live
EOF

    # Stands in for one mt5api process. Only the live port listens, so the
    # other terminal is configured-but-down: the partial-outage case.
    cat >"${WORK_DIR}/terminal.py" <<EOF
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = ${LIVE_TERMINAL_PORT}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "port": PORT, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
EOF
}

start_stack() {
    log INFO "building image=${IMAGE}"
    docker build -q -f "${REPO_DIR}/Dockerfile.mcpunifier" -t "${IMAGE}" "${REPO_DIR}" >/dev/null

    docker network create "${NETWORK}" >/dev/null
    log DEBUG "created network name=${NETWORK}"

    log INFO "starting fake terminal listening=${LIVE_TERMINAL_PORT} down=${DOWN_TERMINAL_PORT}"
    docker run -d --name "${TERMINAL_CONTAINER}" --network "${NETWORK}" --network-alias mt5 \
        -v "${WORK_DIR}/terminal.py:/terminal.py:ro" \
        python:3.12-slim python /terminal.py >/dev/null

    log INFO "starting unifier port=${HOST_PORT}"
    docker run -d --name "${UNIFIER_CONTAINER}" --network "${NETWORK}" \
        -p "127.0.0.1:${HOST_PORT}:6600" \
        -v "${WORK_DIR}/config.yaml:/app/config/config.yaml:ro" \
        "${IMAGE}" >/dev/null
}

run_assertions() {
    local base="http://127.0.0.1:${HOST_PORT}"
    wait_for_http "${base}/health"

    echo
    echo "=== assertions ==="

    assert_eq "health returns 200" "200" "$(http_code "${base}/health")"

    local tool_count
    tool_count=$(curl -s -X POST "${base}/mcp" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' |
        python3 -c 'import sys,json; print(len(json.load(sys.stdin)["result"]["tools"]))')
    assert_eq "tool count" "${EXPECTED_TOOL_COUNT}" "${tool_count}"

    local terminal_count
    terminal_count=$(mcp_call "${base}" list_terminals '{}' |
        python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(json.loads(d["result"]["content"][0]["text"])["terminals"]))')
    assert_eq "list_terminals reports both" "2" "${terminal_count}"

    local routed
    routed=$(mcp_call "${base}" ping '{"broker":"ftmo","account":"tenkchallenge"}' |
        python3 -c 'import sys,json; t=json.load(sys.stdin)["result"]["content"][0]["text"]; print("routed" if "ftmo/tenkchallenge" in t and "6545" in t else "wrong")')
    assert_eq "live terminal routes to its own port" "routed" "${routed}"

    local isolated
    isolated=$(mcp_call "${base}" ping '{"broker":"roboforex","account":"procent"}' |
        python3 -c 'import sys,json; t=json.dumps(json.load(sys.stdin)); print("isolated" if "unreachable" in t else "unexpected")')
    assert_eq "down terminal fails alone" "isolated" "${isolated}"

    local refused
    refused=$(mcp_call "${base}" get_account '{"broker":"ftmo","account":"procent"}' |
        python3 -c 'import sys,json; t=json.dumps(json.load(sys.stdin)); print("refused" if "unknown terminal" in t else "unexpected")')
    assert_eq "mismatched broker/account refused" "refused" "${refused}"

    assert_eq "still healthy after failures" "200" "$(http_code "${base}/health")"

    echo
    if [[ "${FAILURES}" -eq 0 ]]; then
        echo "ALL ASSERTIONS PASSED"
        return 0
    fi
    echo "${FAILURES} ASSERTION(S) FAILED"
    return 1
}

main() {
    local command="${1:-test}"

    case "${command}" in
    -h | --help | help)
        usage
        return 0
        ;;
    clean)
        log INFO "clean-only mode"
        sweep_stale
        ;;
    test)
        sweep_stale
        write_fixtures
        start_stack
        run_assertions
        ;;
    *)
        usage >&2
        return 2
        ;;
    esac
}

main "$@"
