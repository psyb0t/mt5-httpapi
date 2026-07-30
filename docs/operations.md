# Operations

Supported Make targets, service ports, remote-access options, project layout, concurrency controls, and log locations.

## Contents

- [Make targets](#make-targets)
- [Ports](#ports)
- [Tailscale](#tailscale-optional)
- [Cloudflare Tunnel](#cloudflare-tunnel-optional)
- [Project structure](#project-structure)
- [Concurrency and backpressure](#concurrency-and-backpressure)
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

Expose the API over your tailnet using a bare MagicDNS hostname — `http://mt5-httpapi/<broker>/<account>/...` — works with both stock Tailscale and self-hosted Headscale. Plain HTTP (no TLS) by design: bare hostnames don't have matching certs, and the wireguard layer already encrypts everything inside the tailnet.

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

Expose the API publicly without opening firewall ports. cloudflared dials out to Cloudflare's edge and proxies to the always-on `nginx` sidecar — one tunnel, one hostname, every terminal reachable behind `/<broker>/<account>/`.

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

The MT5 Python SDK is single-connection-per-process and not threadsafe — concurrent calls into `mt5.*` corrupt internal state (notably `last_error`, which several flows read implicitly). The API enforces a process-wide mutex around all SDK calls, held for the **entire** duration of a request handler so multi-call handlers (`POST /orders`, the `get_rates` retry loop, etc.) are atomic against everything else.

Knock-on effects you'll observe:

- **`/ping`** is the only handler that doesn't take the lock. Use it for liveness probes — it stays responsive even when the SDK is wedged.
- Every `mt5.*` call has a hard 30s timeout. A wedged call returns **`504 mt5 call timed out`** and releases the lock. The orphaned C-thread is still spinning inside the SDK; the health monitor will detect a dead terminal and run `restart_terminal` to free it.
- When too many requests pile up on the lock, new ones get **`503 queue depth N exceeds max M`** instead of waiting. Default cap is 20; tune with `MT5_MAX_QUEUE_DEPTH=...` in the environment.
- Per-call timing logs (`<req_id> mt5.<fn> dur_ms=...`) are emitted for every SDK call so wedge investigations have data to chew on.

If you see persistent 503/504 from a single terminal, check `data/shared/logs/api-<broker>-<account>.log` for `mt5.* TIMEOUT` lines — that's the SDK call that wedged.

## Logs

Inside the VM's shared folder (`data/shared/logs/`):

- `install.log` - MT5 installation progress (install.bat)
- `start.log` - Boot sequence log (start.bat)
- `pip.log` - Python package installation
- `api-<broker>-<account>.log` - Per-terminal API logs
- `windows-events.log` - Tailed Windows System + Application event logs (Warning/Error/Critical level only). Catches OOM kills (`Microsoft-Windows-Resource-Exhaustion-Detector`), terminal64.exe crashes (`Application Error`), BSODs (`BugCheck`), service failures, etc.
- `full.log` - Single narrative of all of the above with `[start]` / `[install]` / `[api:<broker>/<account>]` / `[winevt]` tags. `tail -f full.log` is the one-stop diagnostic view.

### Rotation

A small alpine sidecar (`log-rotator`) rotates every `*.log` in `data/shared/logs/` daily and prunes archives older than 7 days. Naming: `full.log` → `full.log.YYYYMMDD` (yesterday's date) at the next post-midnight wakeup. Idempotent, hourly check loop, no cron daemon needed.

Override defaults via `docker-compose.yml`:

- `RETAIN_DAYS` (default `7`) - how many days of archives to keep
- `INTERVAL` (default `3600`) - how often to check for the day boundary, in seconds

Truncation is in-place (the archive is a copy, then the original is `:>`-truncated) so the Python API's open log handle keeps writing without reopening.

When shit breaks, check these first.
