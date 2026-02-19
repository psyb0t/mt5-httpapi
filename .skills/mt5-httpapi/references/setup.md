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
cp config/terminals.example.json config/terminals.json
# Edit both files with your broker credentials
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

Which terminals to run — each gets its own MT5 instance and API port:

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

Inside the VM shared folder (`data/metatrader5/logs/`):

- `install.log` — MT5 installation progress
- `setup.log` — boot-time setup output
- `pip.log` — Python package install
- `api-<broker>-<account>.log` — per-terminal API logs
