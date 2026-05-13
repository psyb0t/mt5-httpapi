# mt5-httpapi setup

## Requirements

- Linux host with KVM enabled (`/dev/kvm`)
- Docker + Docker Compose
- ~10 GB disk (Windows ISO + VM storage)
- 5 GB RAM (runs mostly on swap — tiny11 + debloat idles at ~1.4 GB)

## Quick Install

```bash
git clone https://github.com/psyb0t/mt5-httpapi
cd mt5-httpapi
cp config/config.yaml.example config/config.yaml
# Edit config.yaml with your broker credentials, api_token, terminals
```

Drop your broker's MT5 installer in `mt5installers/`, named `mt5setup-<broker>.exe`, then:

```bash
make up
```

First run downloads tiny11 (~4 GB), installs Windows (~10 min), then sets up Python + MT5 automatically. On first boot it debloats Windows, reboots, installs MT5 terminals, reboots again, then starts everything. After that, boots in ~1 min.

## Configuration

### `config/config.yaml`

Single file for everything: bearer token, broker credentials, terminals, and optional sidecar settings (tailscale, wickworks). Copy from `config.yaml.example` and fill in.

```yaml
# Bearer token for API auth. Empty string = no auth.
api_token: "your-token-here"   # or: $(openssl rand -hex 32)

# Broker credentials — accounts.<broker>.<account_name>
accounts:
  roboforex:
    main:
      login: 12345678
      server: "RoboForex-Pro"
      password: "your_password"

# Terminal instances — one API process per entry.
# port is container-internal (only nginx and the mt5 container talk to it).
# utc_offset normalizes broker wall-clock timestamps to real UTC on the wire.
terminals:
  - broker: roboforex
    account: main
    port: 6542
    utc_offset: "3h"

# Optional: wickworks TA sidecar (used by POST /symbols/<symbol>/rates/ta)
# Default URL is the dockurr gateway IP — leave as-is unless you change
# the docker-compose networking.
wickworks:
  url: "http://20.20.20.1:8000/"
  timeout: "30s"
```

`broker` matches both the `mt5setup-<broker>.exe` installer name and the key in `accounts`. Each terminal installs to `<broker>/base/` and gets copied to `<broker>/<account>/` at startup so multiple accounts of the same broker don't conflict.

`api_token` empty = open to anyone on the network. If set, all requests require `Authorization: Bearer <token>` and return `401` without it.

## Ports

| Port | Service |
| ---- | ------- |
| 8006 | noVNC (VM desktop) — override with `NOVNC_PORT` |
| 8888 | HTTP API entry (nginx, all terminals) — override with `API_HOST_PORT`, bound to `127.0.0.1` |

Per-terminal ports from `config.yaml`'s `terminals:` list stay container-internal. nginx routes `/<broker>/<account>/...` to the right terminal via docker DNS, and the mt5 container's iptables DNAT forwards into the Windows VM. URL scheme: `http://localhost:8888/<broker>/<account>/...`. noVNC is mainly useful for watching the install progress.

## Management

```bash
make up          # start
make down        # stop
make logs        # tail logs
make status      # check status
make clean       # nuke VM disk (keeps ISO)
make distclean   # nuke everything including ISO
```

## Logs

Inside the VM shared folder (`data/shared/logs/`):

- `install.log` — MT5 installation progress
- `start.log` — boot-time setup output
- `pip.log` — Python package install
- `api-<broker>-<account>.log` — per-terminal API logs
- `full.log` — combined log of everything

## Public Access via Cloudflare Tunnel (optional)

To expose the API publicly without opening firewall ports:

```bash
# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared
sudo install /tmp/cloudflared /usr/local/bin/cloudflared

# Authenticate and create tunnel
cloudflared tunnel login
cloudflared tunnel create mt5-httpapi

# Register one subdomain — nginx routes per-terminal paths internally
cloudflared tunnel route dns mt5-httpapi mt5-api.yourdomain.com

# Put creds in .data/cloudflared/
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

cloudflared points at the always-on nginx sidecar (`http://nginx:80`) — a single backend covers every terminal. Hit `https://mt5-api.yourdomain.com/<broker>/<account>/...` from the public side. Then uncomment the `cloudflared` service in `docker-compose.yml` and run `make up`.

Note: Cloudflare's free Universal SSL covers `*.yourdomain.com` but not deeper subdomains like `*.mt5.yourdomain.com`. Use subdomains directly under your root domain.
