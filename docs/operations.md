# Operations — keeping the bastard alive

The commands, ports, tunnels, locks, queues, and logs you need after the thing boots and eventually does something stupid.

## Contents

- [Make targets](#make-targets)
- [Ports](#ports)
- [Tailscale](#tailscale-optional)
- [Cloudflare Tunnel](#cloudflare-tunnel-optional)
- [Project structure](#project-structure)
- [Concurrency and backpressure](#concurrency-and-backpressure)
- [Auto-recovery](#auto-recovery)
- [Logs](#logs)

## Make Targets

```
make up          Fire up the VM (downloads ISO if needed)
make down        Shut it down
make logs        Tail the logs
make status      Check VM and API status
make lint        Lint every .ps1/.sh script in a throwaway Docker image
make format      Apply shfmt formatting to the .sh files in place
make test        Run the complete automated test suite
make test-unit   Run unit and contract tests in a throwaway Docker image
make test-integration  Container-backed suites: nginx routing + MCP unifier
make test-go     Compile and race-test the public Go client
make clean       Nuke VM disk and state (keeps ISO)
make distclean   Nuke everything including ISO
```

`make test` is the complete automated gate: it runs `make test-unit`,
`make test-integration`, and `make test-go`. Use a scoped target directly while
iterating.

`make test-unit` is the offline suite — it runs inside a throwaway image with
the MT5 SDK stubbed, so it needs nothing but docker and finishes in seconds.

`make test-integration` is everything that needs real containers, driven by
pytest + testcontainers in `tests/integration/`:

- **nginx routing** renders the config `config_helper.py` generates and boots
  real nginx against it with one VM deliberately absent, proving a single dead
  VM cannot stop nginx starting and taking every healthy terminal with it.
- **MCP unifier** builds the unifier image, stands it up next to a stub
  terminal, and checks that it routes to the right terminal, that a terminal
  which is down fails only the calls naming it, and that a broker/account pair
  you never configured is refused rather than routed somewhere plausible.

It runs on the host because it starts sibling containers through the docker
socket. Its dependencies live in a reusable, gitignored `.venv-test/`; the
testcontainers fixtures remove the containers and networks they create after
the run, including on ordinary test failures.

`make test-go` runs the public client with the race detector in a pinned Go
container. The source mount is read-only and module/build caches are ephemeral.

`make status` is separate from the automated suite: it runs
`scripts/status.sh` against an already-running MT5 deployment and reports the
live terminals and read-only API checks.

`make lint` covers the scripts that run inside the VM as well as the host-side
shell: `.ps1` gets a pure-ASCII check, a parse check, and PSScriptAnalyzer;
`.sh` gets shellcheck and shfmt. The ASCII rule is not cosmetic — Windows
PowerShell 5.1 reads `.ps1` as ANSI unless the file has a UTF-8 BOM, so a
multi-byte character inside a string literal gets mangled into a parse error you
won't see until the VM boots. `make format` fixes whatever shfmt flags.

## Ports

| Port  | Service                 | Override                          |
| ----- | ----------------------- | --------------------------------- |
| 8006  | noVNC (VM desktop)      | `NOVNC_PORT=9006 make up`         |
| 8888  | HTTP API (nginx, all terminals) | `API_HOST_PORT=9999 make up` |

In single-VM mode, only these two ports leave the docker network. A
[multi-VM deployment](multi-vm-setup.md) publishes one distinct noVNC port per
VM while keeping the single nginx API port. Per-terminal ports from
`config.yaml` stay container-internal — nginx routes each terminal path to the
owning VM over docker DNS, then that container's iptables DNAT forwards it into
Windows. The API binds to loopback (`127.0.0.1:8888`) by default; change the
bind in `docker-compose.yml` for LAN exposure, or use a private access option
below.

## Tailscale (optional)

Shove the API through your tailnet at `http://mt5-httpapi/<broker>/<account>/...`. This works with stock Tailscale or self-hosted Headscale. It deliberately uses plain HTTP inside WireGuard because bare MagicDNS names do not have matching certs and the tunnel already encrypts the traffic.

How it works: a `tailscale` sidecar joins the tailnet in its **own netns** (bridge mode, not host net) so it gets its own tailnet identity — ACLs scope to the sidecar's node only, and the host's tailscale (if any) stays out of the sidecar's inbound path. Tailscale Serve listens on port 80 inside that netns and proxies to the always-on `nginx` sidecar (`http://nginx:80`) over docker's internal network. nginx then strips `/<broker>/<account>/` and proxies to the right terminal via docker DNS. `nginx.conf` is auto-generated from `config.yaml`'s `terminals:` list on every `make up`; the Tailscale Serve config is wired in via the `tailscale serve` CLI from inside the sidecar (it needs the live FQDN, which only the CLI knows) and persisted in tailscaled state.

The sidecar runs in TUN mode (`TS_USERSPACE=false`) so a real `tailscale0` interface exists inside its netns. That means any outbound to `100.64.0.0/10` from the sidecar is routed via the sidecar's own tunnel under its tailnet identity — not via the host's `tailscale0` (if the host has one on a different account). Note the scope: this only applies to traffic originating in the sidecar's netns. The other containers (`nginx`, `mt5`) are on the docker bridge, not in the sidecar's netns, so any tailnet-bound traffic from them would still fall through to the host's tunnel. We don't initiate tailnet outbound from those containers in the default setup, so it doesn't bite — but if you add a service that does, put it on `network_mode: service:tailscale`.

**Setup**:

1. Set your auth key in `config/config.yaml`:
   ```yaml
   tailscale:
     auth_key: "tskey-auth-..."
     login_server: ""   # for Headscale, set "https://headscale.your.domain"
   ```

2. Uncomment the `tailscale` block in `docker-compose.yml`. (nginx is always on — no need to uncomment anything for it.)

3. `make up`. `run.sh` reads `config.yaml`, writes `TS_AUTHKEY` (and `TS_EXTRA_ARGS=--login-server=...` if Headscale) to `.env`, brings the stack up, waits for tailscaled to authenticate, and runs `tailscale serve --bg --http=80 http://nginx:80` inside the sidecar to wire the tailnet :80 listener to nginx. The Serve config persists in `.data/tailscale/state`, so subsequent `make up` calls don't need to redo it.

**State persistence**: tailnet identity lives in `.data/tailscale/state/`. `make down`/`make up` reuses the existing login — `TS_AUTHKEY` is consumed only on first auth (or after `rm -rf .data/tailscale/state`). Use a reusable auth key if you expect to wipe state.

**Multi-terminal URL scheme**:
```
http://mt5-httpapi/roboforex/main/account
http://mt5-httpapi/roboforex/main/symbols/EURUSD/rates?count=100
http://mt5-httpapi/ftmo/challenge1/positions
```

The API token (if set in `config.yaml`) still applies — Tailscale handles network-level access, the token handles application-level auth.

## Cloudflare Tunnel (optional)

If you absolutely need this shit on the public internet, cloudflared can dial out without opening firewall ports. One tunnel and hostname reach every terminal through `/<broker>/<account>/`.

**Setup**:

1. Install cloudflared on the host (one-off, only needed to create the tunnel):
   ```bash
   curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared
   sudo install /tmp/cloudflared /usr/local/bin/cloudflared
   ```

2. Authenticate, create a tunnel, and route a single hostname to it (must be a zone you control on Cloudflare):
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create mt5-httpapi
   cloudflared tunnel route dns mt5-httpapi mt5-api.yourdomain.com
   ```

3. Drop the credentials and config into `.data/cloudflared/`:
   ```bash
   mkdir -p .data/cloudflared
   cp ~/.cloudflared/<tunnel-id>.json .data/cloudflared/creds.json
   ```

   Create `.data/cloudflared/config.yml`:
   ```yaml
   tunnel: <tunnel-id>
   credentials-file: /etc/cloudflared/creds.json

   ingress:
     - hostname: mt5-api.yourdomain.com
       service: http://nginx:80
     - service: http_status:404
   ```

4. Uncomment the `cloudflared` block in `docker-compose.yml` and `make up`.

**Public URL scheme**:
```
https://mt5-api.yourdomain.com/roboforex/main/account
https://mt5-api.yourdomain.com/ftmo/challenge1/positions
```

Cloudflare terminates TLS at the edge — you get HTTPS for free without managing certs. The connection from cloudflared to `nginx:80` is plain HTTP over the docker bridge, but it never leaves the host.

**Subdomain depth**: Cloudflare's free Universal SSL covers `*.yourdomain.com` but not deeper levels like `*.mt5.yourdomain.com`. Use a single subdomain directly under the root domain.

The API token still applies on top — Cloudflare gates the public reachability, the bearer token gates the application. Treat the public hostname as hostile and **always set `api_token` in `config.yaml`** when using this.

## Project Structure

```
config/                      Your config shit
  config.yaml                Single source of truth (gitignored)
  config.yaml.example        Committed template — copy to config.yaml
  setup.bat                  Custom boot commands (optional)
  hosts                      Extra entries for the VM's hosts file (optional)

vms.yaml                     VM topology definition (optional — absent = single VM)
docker-compose.yml.j2        Jinja2 template for N-VM compose generation

scripts/                     Scripts that run inside the Windows VM
  oem-install.bat            First-boot OEM script (creates startup entry)
  install.bat                Setup (Python, MT5, firewall) — runs every boot
  start.bat                  Boot entrypoint (install + start terminals + APIs)
  reboot.bat                 The only reboot path — writes rebooting.flag and
                             releases both lock dirs before shutting down
  acquire_lock.ps1           Boot-stamped lock acquire for start.bat and
                             install.bat; auto-clears reboot-orphaned locks
  debloat.bat                Windows debloat script
  defender-remover/          Windows Defender removal tool

mt5api/                      Python HTTP API server
  handlers/                  Route handlers
  config.py                  Configuration (--broker, --account, --port CLI args)
  mt5client.py               MT5 wrapper
  server.py                  Flask routes

examples/                    Usage examples
  python/                    TA, charting, and API client modules

mt5installers/               Broker MT5 setup executables (gitignored)
data/                        Generated/volatile data (gitignored)
  win.iso                    Windows ISO
  storage/                   VM disk
  shared/                    Shared folder with VM
    scripts/                 Bat scripts synced from scripts/
    config/                  Config synced from config/
    terminals/               MT5 installs per broker/account
    logs/                    All log files
    mt5api/                  Python API package
  oem/                       First-boot scripts
```

## Concurrency and backpressure

The MT5 Python SDK is single-connection-per-process and not remotely fucking threadsafe. Concurrent `mt5.*` calls corrupt shared state, especially `last_error`, so the API holds one process-wide mutex for the **entire** request handler.

Knock-on effects you'll observe:

- **`/ping`** is the only handler that doesn't take the lock. Use it for liveness probes — it stays responsive even when the SDK is wedged, and now also reports `sdk_threads_alive` and `sdk_oldest_alive_s` so a wedge shows up there before it shows up as a timeout anywhere else.
- Every `mt5.*` call has a hard 30s timeout (`MT5_CALL_TIMEOUT`). A wedged call returns **`504 mt5 call timed out`** and releases the lock — but the orphaned C-thread is still spinning inside the SDK, uninterruptibly. The **single-flight guard** stops that from cascading: while that thread is still alive, every *later* call fails instantly with **`503 mt5 call is wedged` + `Retry-After: 30`** instead of running out its own 30s timeout too (see `docs/spec/mt5-httpapi-sdk-call-thread-leak.md` for the incident this fixes). The one exception is `restart_terminal`'s own reconnect right after it kills the terminal — that call is deliberately allowed through the guard it may have just tripped.
- The **wedge watchdog** (`mt5-wedge-watchdog` thread, started in every mode) exits the process outright (`os._exit(75)`) once the oldest live SDK worker has been stuck longer than `MT5_WEDGE_EXIT_SECONDS` (default 180s) — checked every `MT5_WEDGE_CHECK_INTERVAL_SECONDS` (default 15s). In backtest mode it defers that exit while a tester run holds `RUN_LOCK`, up to `MT5_WEDGE_DEFER_CEILING_SECONDS` (default 1800s = 30 min) measured from when it first wanted to exit — a chain of queued jobs re-acquiring the lock does not reset that ceiling. `api_runner.bat`'s relaunch loop (applied by `deploy-mt5-scripts.sh`, not upstream) brings the process back up after such an exit; look for `PROCESS EXITED exit_code=75` in the VM logs.
- When too many requests pile up on the lock, new ones get **`503 queue depth N exceeds max M`** instead of waiting. Default cap is 20; tune with `MT5_MAX_QUEUE_DEPTH=...` in the environment.
- Per-call timing logs (`<req_id> mt5.<fn> dur_ms=...`) are emitted for every SDK call so wedge investigations have data to chew on. A wedge itself logs as `WEDGED (...)` (not `TIMEOUT after 0.0ms`) and carries the process mode, request path, and live worker count.

If you see persistent 503/504 from a single terminal, check `data/shared/logs/api-<broker>-<account>.log` for `mt5.* TIMEOUT` and `WEDGED` lines — that's the SDK call that wedged, and whether later requests are hitting the guard or the raw SDK.

## VM recreate and the wickworks sidecar

The wickworks TA sidecar shares a VM's network namespace via compose
`network_mode: service:<vm>`. Docker resolves that binding once, at container
start, into an immutable `NetworkMode=container:<owner-id>`.

**Recreating the VM alone orphans the sidecar.** `docker compose up -d
--force-recreate <vm>` gives the VM a new ID and a fresh netns, but leaves the
sidecar pointed at the deleted owner. Its healthcheck detects the orphan and
reports unhealthy, but cannot repair the binding itself. Restarting the VM is
not a reliable alternative either: the owner ID survives a restart, but the
sidecar's netns does not.

The correct recreate operation names the VM together with its sidecar:

```bash
docker compose up -d --force-recreate mt5 wickworks
```

or, with the sidecar list discovered from the generated compose file:

```bash
./scripts/recreate-vm.sh mt5            # recreate mt5 + its sidecars
./scripts/recreate-vm.sh mt5 mt5-b      # both VMs + their sidecars
./scripts/recreate-vm.sh --dry-run mt5  # show what would be recreated
```

This is covered by the real Compose lifecycle regression in
`tests/integration/test_wickworks_lifecycle.py`.
## Auto-recovery

The Windows VM(s) run inside `dockurr/windows` containers with a Docker healthcheck
(`scripts/healthcheck.sh`) that probes every terminal port this VM owns. A crash
inside the guest — an unexpected shutdown (Event 6008), a wedged terminal, an
OOM — leaves the **container** up while the **API** is dead, so
`restart: unless-stopped` never fires and nothing recovers it on its own.

### VM crash watchdog

`vm-watchdog` is a Compose-managed sidecar for exactly that case. It is part of
the project (`docker compose up -d` brings it up with everything else) — no
host cron, no systemd unit, no machine-specific checkout path. It polls Docker
health through the mounted unix socket and uses Docker's own
`State.Health.FailingStreak` as the source of truth.

Behavior:

- Scopes itself to this Compose project (`com.docker.compose.project`,
  discovered from its own container labels) and to the `dockurr/windows` VM
  image, so the VM sweep only ever recovers the VM containers — never nginx,
  the log rotator, or other containers. The netns sidecars get a second,
  narrower sweep of their own (below).
- **Recovers by recreating the VM together with its netns sidecars**, by
  running `scripts/recreate-vm.sh <service>` — the same helper documented above
  and covered by `tests/integration/test_wickworks_lifecycle.py`. It does not
  use `docker restart`: that keeps the container ID but gives the VM a fresh
  netns on start, which strands the wickworks sidecar exactly as recreating the
  VM alone does.
- Acts **only** after its Docker health has stayed `unhealthy`
  for `WATCHDOG_MIN_FAILING_STREAK` consecutive healthcheck failures (default
  `10`, i.e. ~5 minutes at the default 30s interval). A container that is
  healthy or still starting is never touched, so running backtests on a working
  VM are never interrupted — the healthcheck stays green the whole time a
  terminal is serving.
- Keeps a tiny state record per VM on a named volume, keyed by compose
  project + service (`/state/<project>.<service>.json` — stable across the
  recreate that recovery performs, unlike a container id): last restart,
  attempt count, and when the VM
  was last observed healthy.
- Enforces exponential backoff between recovery attempts
  (`WATCHDOG_BACKOFF_ATTEMPTS`, default `300,900,3600` — 5m → 15m → 1h), so a
  VM that crashes again immediately after recovery is not restarted into a
  loop.
- Stops after `WATCHDOG_MAX_ATTEMPTS` consecutive failed recoveries (default
  `3`) and logs loudly, instead of threshing forever.
- Resets the attempt budget only after the VM has stayed **continuously**
  healthy for `WATCHDOG_RESET_SECONDS` (default `1800`), so a VM that recovered
  then crashed later gets a fresh budget. Any non-healthy observation — a
  `starting` container after a restart, or an `unhealthy` poll below the streak
  threshold — restarts that clock; it does not carry over from an earlier
  healthy run.
- Never selects itself, whatever `WATCHDOG_IMAGE_FILTER` is set to — it resolves
  its own full container id at startup and excludes it by equality (falling
  back to Docker's short-id hostname only if that inspect fails) — and matches
  the image **repository exactly** (`dockurr/windows`, `dockurr/windows:5.14`,
  `dockurr/windows@sha256:…` — not `dockurr/windows-something`).
- `WATCHDOG_DRY_RUN=1` (or `--dry-run`) prints what it would do without
  touching any container.

#### The stranded sidecar, which no VM ever reports

A sidecar joins its VM with `network_mode: service:<vm>`. Docker resolves that
**once**, at the sidecar's own start, into an immutable
`NetworkMode=container:<owner-id>`, and it builds a **fresh namespace every
time the owner starts**. So restarting the owner strands the sidecar: the
container id is unchanged, so nothing about the binding looks wrong, but the
namespace it points at is gone. The VM comes back perfectly healthy and the VM
sweep has nothing to act on.

That is not hypothetical here. On 2026-09-07 the `mt5` container exited cleanly
and `restart: unless-stopped` brought it back; its `wickworks` sidecar sat in
the dead namespace for **two days** — `FailingStreak` 13,700, only `lo` left,
every `/rates/ta` call 502-ing — with the VM green throughout.

The failing streak is the point. The sidecar's own healthcheck saw the fault
the whole time. There was simply no supervisor for it. So a second sweep
follows the VM sweep and applies the **same rule** — Docker health, past the
same `WATCHDOG_MIN_FAILING_STREAK`, the same backoff and attempt cap — to the
netns sidecars:

- It recreates **that sidecar alone** (`recreate-vm.sh <sidecar>` →
  `up -d --force-recreate --no-deps`). Nothing declares
  `network_mode: service:<sidecar>`, so the helper's own discovery returns
  nothing for it and exactly one container is touched. That is the documented
  repair, it is what an operator does by hand, and it leaves the VM and its
  terminals alone.
- It acts **only while the owner is a running, healthy VM of this project**. An
  unhealthy owner belongs to the VM sweep, which recreates owner and sidecars
  together. An owner that is not running is left alone too, and that one is
  load-bearing rather than a default: `/containers/json` lists running
  containers only, so a **stopped** owner is indistinguishable from a destroyed
  one. Recreating a sidecar under a stopped owner cannot work — the helper
  stops it first, then `up --no-deps` has no namespace to join — so the sidecar
  would end up stopped, invisible to both sweeps, and never retried. A
  `docker compose stop mt5` for maintenance must not cost you the sidecar.
- `container:<name>` and `container:<short-id>` are legal to write by hand and
  are not normalised anywhere, so the owner reference is matched by full id, by
  a 12-character-or-longer prefix, and by container name.

**This makes an orphan-aware sidecar healthcheck a requirement, not a nicety.**
A check that only probes loopback stays green inside a dead namespace, and
Docker health is this daemon's only source of truth, so nothing here will ever
fire for it. `scripts/wickworks-healthcheck.py` is the worked example: it
probes the owner's gateway services, which disappear the moment the namespace
does.

Set `WATCHDOG_WATCH_SIDECARS=0` to restore the VM-only scope.

Environment overrides: `WATCHDOG_INTERVAL_SECONDS`, `WATCHDOG_MIN_FAILING_STREAK`,
`WATCHDOG_IMAGE_FILTER`, `WATCHDOG_WATCH_SIDECARS`, `WATCHDOG_BACKOFF_ATTEMPTS`,
`WATCHDOG_MAX_ATTEMPTS`, `WATCHDOG_RESET_SECONDS`, `WATCHDOG_COMPOSE_PROJECT`,
`WATCHDOG_STATE_DIR`, `WATCHDOG_DOCKER_SOCKET`, `WATCHDOG_DRY_RUN`,
`WATCHDOG_PROJECT_DIR`, `WATCHDOG_RECREATE_SCRIPT`, `WATCHDOG_RECREATE_TIMEOUT`.

`WATCHDOG_PROJECT_DIR` is the **host** path of this project, and the compose
service mounts the project through at that same absolute path. Compose resolves
the relative bind mounts in `docker-compose.yml` client-side, so a
container-local path would rewrite every mount to something that does not exist
on the host. If it is missing the watchdog reports it at startup and refuses to
act, rather than falling back to a restart that looks like recovery and is not.

That path reaches compose as `MT5_PROJECT_DIR`, and compose interpolates it on
**every** command against `docker-compose.yml`, not only the first `up`:

- `run.sh` exports it for its own run **and writes it to `.env`**, so `make
  down`, `make logs` and a manual `docker compose …` keep working after `run.sh`
  has exited. Starting the stack some other way? Put
  `MT5_PROJECT_DIR=<absolute host path of this directory>` in `.env` yourself.
- The watchdog sets it explicitly in the environment of the `recreate-vm.sh` it
  runs (from its own `WATCHDOG_PROJECT_DIR`), because the container is not
  handed the host's shell variables. `tests/test_vm_watchdog.py` runs the real
  helper under exactly that environment, and
  `tests/integration/test_vm_watchdog_lifecycle.py` drives a real recovery
  through the built sidecar on a disposable Compose project.

### Busy is not dead — and hung is not busy

`healthcheck.sh` reports a port **healthy** when the TCP handshake completes but
no HTTP answer arrives inside the probe window: something is listening, the
guest is just saturated (a compile, a Strategy Tester run). Restarting a VM for
being busy would turn a slow batch into an outage.

That tolerance is **bounded**. A port that accepts TCP but stays silent for
`HEALTHCHECK_SLOW_GRACE` consecutive checks (default `10`, ≈5 minutes at the 30s
interval) is reported as `hung` and the check fails — from there the watchdog's
own streak gate (`WATCHDOG_MIN_FAILING_STREAK`, another ≈5 minutes) applies, so
a wedged API is recovered in roughly ten minutes rather than never. The
per-port counters live in `HEALTHCHECK_STATE_DIR` (default `/tmp/healthcheck-slow`
inside the VM container); an HTTP answer or a refused connection resets a port's
count, and a recreate starts every count from zero. A refused connection
(nothing listening) is `DOWN` immediately, as before. If the counters cannot be
written (a full disk), the bound is off for that check and the verdict says so:
`ok (slow but listening: …) [slow-state unwritable: hung detection off]`.

**Blast radius.** One hung terminal API is enough to mark the whole VM `DOWN`,
and the watchdog's recovery is the whole VM — every other terminal on it, and
whatever they were running, goes with it. That is the same rule the check has
always applied to a dead port; it is just now applied to a hung one after the
grace. When you catch a single wedged terminal before the watchdog does,
`POST /terminal/restart` on that terminal (see `docs/rest-api.md`) is the
cheaper first response.

This complements the in-VM `MT5AutoReboot` scheduled task, which reboots on a
fixed timer and can interrupt long-running backtests; operators who disable that
task still get crash recovery from the watchdog.

### The probe budget: why the ports are probed concurrently

The check's wall clock used to be the **product** of the probe timeout and the
terminal count. `PROBE_TIMEOUT_SECONDS` is 3, so a 24-terminal VM whose ports
are all silent took 72s against the compose `timeout: 30s`. Docker killed the
check before it reached a verdict and recorded `Health check exceeded timeout
(30s)` instead of naming the ports that were down.

That failure is worse than useless, because a supervisor cannot tell it apart
from a dead VM. It is also most likely exactly when it hurts most: every port
is slow while the VM is still booting its terminals, so a VM that was merely
starting looked identical to one that had crashed.

The probes therefore run **concurrently**, one background job per port. The
worst case is now one port's **host walk**, not one probe: each job still tries
the leased VM IP and then the two fallback hosts in turn, so the bound is
`PROBE_TIMEOUT_SECONDS × 3` = 9s, independent of terminal count. Keep three
times `PROBE_TIMEOUT_SECONDS` comfortably under the compose `timeout:` — at 10s
it would be 30s and the original bug is back.

**The verdicts travel in exit statuses, not in files, and that is the whole
point.** The obvious implementation gives each job a verdict file under a
`mktemp -d`. It is fine until the disk is full: the write fails, the parent
finds no verdict, and its fail-closed rule reports *every* terminal on the VM
as down. Ten of those in a row and the watchdog recreates a perfectly healthy
VM, destroying two dozen running backtests, because `/tmp` filled up. The
sequential loop this replaced needed no disk, and neither does this: each job
returns `0` up, `1` busy, `2` hung, `3` dead, `4` busy-but-the-counter-could-
not-be-written, and the parent reads them back with `wait` in port order.
Anything else — a job killed by a signal — is unknown and fails closed as down.

The only thing here that touches the disk is the slow/hung counter, and it is
deliberately off the path that decides up or down: if the counters cannot be
written the bound is off for that check and the verdict says
`[slow-state unwritable: hung detection off]`, while every port's liveness is
still whatever its probe actually found.

A port configured twice is probed once. Two terminals on one port is a
misconfiguration rather than a topology, and acting on it twice meant two jobs
writing one counter — and, before the fan-out, a hung bound that fired at half
the configured grace. `config_helper.py` is where a duplicated port should be
reported; a healthcheck's job is liveness.

## Logs

Inside the VM's shared folder (`data/shared/logs/`):

- `install.log` - MT5 installation progress (install.bat)
- `start.log` - Boot sequence log (start.bat)
- `pip.log` - Python package installation
- `api-<broker>-<account>.log` - Per-terminal API logs
- `windows-events.log` - Tailed Windows System + Application event logs (Warning/Error/Critical level only). Catches OOM kills (`Microsoft-Windows-Resource-Exhaustion-Detector`), terminal64.exe crashes (`Application Error`), BSODs (`BugCheck`), service failures, etc.
- `full.log` - Single narrative of all of the above with `[start]` / `[install]` / `[api:<broker>/<account>]` / `[winevt]` tags. `tail -f full.log` is the one-stop diagnostic view.

### Rotation

A small alpine sidecar (`log-rotator`) rotates every `*.log` in
`data/shared/logs/` daily and prunes archives older than 7 days. It also deletes
dated journals older than the same retention period from each installed
terminal's `logs/`, `Tester/logs/`, and `Tester/Agent-*/logs/` directories. It
does not touch MQL5 expert logs, MetaEditor logs, reports, or backtest jobs.
Naming: `full.log` → `full.log.YYYYMMDD` (yesterday's date) at the next
post-midnight wakeup. Idempotent, hourly check loop, no cron daemon needed.

Override defaults via `docker-compose.yml`:

- `RETAIN_DAYS` (default `7`) - how many days of rotated shared logs and dated MT5 journals to keep
- `INTERVAL` (default `3600`) - how often to check for the day boundary, in seconds

Truncation is in-place (the archive is a copy, then the original is `:>`-truncated) so the Python API's open log handle keeps writing without reopening.

#### Size cap on terminal journals

Retention alone does not bound the terminal journals, because it will not consider a journal until it is `RETAIN_DAYS` old. A high-frequency grid strategy logs every order placement, modification and cancellation, so a single backtest can write tens of gigabytes into *today's* journal — and the disk fills a week before that file is even eligible for the age pass.

So a journal still inside the retention window is reclaimed by size instead, once it goes quiet:

- `MAX_LOG_BYTES` (default `2147483648`, 2 GiB) - truncate any single journal larger than this
- `IDLE_MINUTES` (default `30`) - never touch a journal written more recently

Both are set on the `log-rotator` service in `docker-compose.yml` alongside `RETAIN_DAYS`. All three are validated at startup: a non-positive or non-numeric value exits non-zero rather than silently falling back to a default and quietly pruning on the wrong terms.

**What this does not do:** `IDLE_MINUTES` deliberately exempts a journal a backtest is actively writing, because truncating it destroys the diagnostics for the run producing it. So the cap reclaims the space *after* the run goes quiet — it will not stop a single runaway backtest filling the disk while it is still going. If that is your failure mode, the lever is the strategy's own logging, or a larger volume; the rotator only guarantees the space comes back afterwards instead of never. A journal over the cap and still active is logged as `over cap but still active, left alone` on each pass, so it is visible in `docker compose logs log-rotator` rather than silently skipped.

Oversized journals are truncated in place rather than deleted: the terminal holds them open, so unlinking the inode would leave `terminal64.exe` writing to a deleted file and the space would not come back until it exited. The same exclusions as the age pass apply — MQL5 expert logs, MetaEditor logs, reports and backtest jobs are never touched.

When shit breaks, check these first.
