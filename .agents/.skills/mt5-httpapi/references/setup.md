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
cp config/accounts.json.example config/accounts.json
cp config/terminals.json.example config/terminals.json
# Edit both files with your broker credentials

# Generate an API token
openssl rand -hex 32 > config/api_token.txt
```

Drop your broker's MT5 installer in `mt5installers/`, named `mt5setup-<broker>.exe`, then:

```bash
make up
```

First run downloads tiny11 (~4 GB), installs Windows (~10 min), then sets up Python + MT5 automatically. On first boot it debloats Windows, reboots, installs MT5 terminals, reboots again, then starts everything. After that, boots in ~1 min.

## Configuration

### `config/accounts.json`

Broker credentials organized by broker, then account name:

```json
{
  "roboforex": {
    "main": {
      "login": 12345678,
      "password": "your_password",
      "server": "RoboForex-Pro"
    }
  }
}
```

### `config/terminals.json`

Required. Defines which terminals to run — each gets its own MT5 instance and API port:

```json
[
  {
    "broker": "roboforex",
    "account": "main",
    "port": 6542
  },
  {
    "broker": "roboforex",
    "account": "demo",
    "port": 6543
  }
]
```

`broker` matches both the `mt5setup-<broker>.exe` installer name and the key in `accounts.json`. Each terminal installs to `<broker>/base/` and gets copied to `<broker>/<account>/` at startup so multiple accounts of the same broker don't conflict.

### `config/api_token.txt`

Plain text file containing the bearer token used to authenticate all API requests. Generate one:

```bash
openssl rand -hex 32 > config/api_token.txt
```

`run.sh` reads this file and writes `API_TOKEN` to `.env` for docker-compose. The Windows VM reads it from the shared folder and passes it to each API process via `--token`. If the file is missing, the API runs without auth (not recommended).

## Ports

| Port  | Service            |
| ----- | ------------------ |
| 8006  | noVNC (VM desktop) |
| 6542+ | HTTP API per terminal (set in terminals.json) |

noVNC is mainly useful for watching the install progress. After that, just use the REST API.

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

# Register a subdomain per terminal (must be under a zone you control)
cloudflared tunnel route dns mt5-httpapi mt5-roboforex-main.yourdomain.com
# repeat for each terminal

# Put creds in .data/cloudflared/
mkdir -p .data/cloudflared
cp ~/.cloudflared/<tunnel-id>.json .data/cloudflared/creds.json
```

Create `.data/cloudflared/config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/creds.json

ingress:
  - hostname: mt5-roboforex-main.yourdomain.com
    service: http://localhost:6542
  - service: http_status:404
```

Then uncomment the `cloudflared` service in `docker-compose.yml` and run `make up`.

Note: Cloudflare's free Universal SSL covers `*.yourdomain.com` but not deeper subdomains like `*.mt5.yourdomain.com`. Use subdomains directly under your root domain.
