---
title: "Multi-VM setup"
description: "Spread MT5 terminals across several Windows VMs or NUMA nodes with vms.yaml, keep hot storage apart, and add more VMs later."
nav_order: 1
parent: "Operations"
---

# Multi-VM setup when one Windows bastard is not enough

One VM eventually runs out of CPU, RAM, disk, or patience. This is how you spread terminals across several Windows VMs, pin the heavy fuckers to NUMA nodes, and keep hot storage away from bulk junk.

## How it works

- **`vms.yaml`** (copy `vms.yaml.example`) declares each VM's resources (cpuset, RAM, CPU cores, disk size, storage path, noVNC port, wickworks sidecar name).
- **`config/config.yaml`** gains a `vm` field on each terminal entry, binding that terminal to a specific VM.
- **[`scripts/config_helper.py`](https://github.com/psyb0t/mt5-httpapi/blob/master/scripts/config_helper.py)** reads both files to generate nginx routes targeting the correct container (`set $vm_upstream http://mt5:<port>` vs `http://mt5-b:<port>`, then `proxy_pass $vm_upstream`).
- **`run.sh`** loops over all VMs for DNAT/iptables setup and auto-generates per-VM group files.
- **`docker-compose.yml.j2`** renders the compose file from `vms.yaml` via `config_helper.py generate_compose`.

## Backward compatibility

- **No `vms.yaml`**: single-VM mode. `run.sh` seeds `docker-compose.yml` from `docker-compose.yml.example` instead of the template, and all terminals route to the default `mt5` container. The VM list is the single `default` VM (the `mt5` service), so `run.sh` writes one `data/vm-group-default.txt` and sets up DNAT for the `mt5` container only. `vms.yaml.example` is a template to copy and is never read, and `config_helper.py generate_compose` refuses to run without a real `vms.yaml`.
- **No `vm` field on a terminal**: defaults to `default`, routes to `mt5`.

## Prerequisites

- mt5-httpapi v4.10.0+
- Linux host with KVM (`/dev/kvm`), enough RAM for N VMs, and CPU cores you can pin

## Step-by-step

### 1. Copy and edit `vms.yaml.example`

Copy the example instead of writing this YAML from memory:

```bash
cp vms.yaml.example vms.yaml
```

Define one entry per VM. The `name` is how terminals reference it in `config.yaml`.

```yaml
vms:
  - name: fast
    service: mt5
    container_name: mt5
    cpuset: "0-3"
    ram: "8G"
    cpu_cores: 4
    disk_size: "64G"
    storage: /data/mt5-vm-a/storage
    log_dir: /data/mt5-shared/logs
    novnc_port: 8006
    wickworks_service: wickworks
    mem_limit: 10G
    memswap_limit: 12G

  - name: bulk
    service: mt5-b
    container_name: mt5-b
    cpuset: "4-7"
    ram: "8G"
    cpu_cores: 4
    disk_size: "64G"
    storage: /data/mt5-vm-b/storage
    log_dir: /data/mt5-vm-b/logs
    novnc_port: 8007
    wickworks_service: wickworks-b
    mem_limit: 10G
    memswap_limit: 12G
```

Fields:

| Field | Required | Description |
|---|---|---|
| `name` | yes | VM identifier, referenced by `terminals[].vm` in config.yaml |
| `service` | yes | Docker compose service name (e.g. `mt5`, `mt5-b`) |
| `container_name` | yes | Host name nginx proxies this VM's terminals to. The generated compose does not set `container_name:`, so keep it equal to `service` |
| `cpuset` | no | CPU pinning (docker compose `cpuset`) |
| `ram` | yes | RAM for the Windows VM (e.g. `"112G"`, `"4G"`) |
| `cpu_cores` | yes | vCPU count |
| `disk_size` | yes | VM disk size (e.g. `"300G"`) |
| `storage` | yes | Host path for VM system disk |
| `log_dir` | no | Host path mounted at `/shared/logs`; omit to log to `/data/mt5-shared/logs` |
| `novnc_port` | no | Host port for noVNC (e.g. `8006`, `8007`); omitted means `${NOVNC_PORT:-8006}` |
| `wickworks_service` | no | Name of the wickworks sidecar service |
| `mem_limit` | no | Docker memory limit. Omitted means `116G` with a `120G` mem+swap limit |
| `memswap_limit` | no | Docker mem+swap limit, used only when `mem_limit` is set; with `mem_limit` and no `memswap_limit`, none is written |
| `extra_binds` | no | Additional `host:container` bind mounts (hot-tier terminal dirs) |

### 2. Assign terminals to VMs

Add `vm: <name>` to each terminal in `config/config.yaml`:

```yaml
terminals:
  - broker: darwinex
    account: live
    port: 6551
    vm: fast
    mode: live

  - broker: blackbull
    account: live-prime
    port: 6546
    vm: bulk
    mode: backtest
```

### 3. Generate docker-compose.yml

On first run, `run.sh` detects a real `vms.yaml` alongside `docker-compose.yml.j2` and generates the compose file automatically. Without `vms.yaml`, it copies the single-VM `docker-compose.yml.example`. To regenerate a configured multi-VM compose file:

```bash
python3 scripts/config_helper.py generate_compose
```

This shits out one service block per VM and wickworks sidecar, plus the shared log rotator, vm-watchdog, MCP unifier, nginx router, and the `signals` / `telegram` profile services. It overwrites `docker-compose.yml`.

### 4. Point `/data/mt5-shared` at the shared directory

Every generated VM mounts the fixed host path `/data/mt5-shared` as `/shared`, and the log rotator reads `/data/mt5-shared/terminals`. `run.sh` syncs the code, config and Windows scripts into `<project>/data/shared`, not into `/data/mt5-shared`. Make `/data/mt5-shared` the same directory as `<project>/data/shared` (a symlink works), or the VMs boot with whatever `/data/mt5-shared` already holds.

## Generated files

`run.sh` auto-generates these from the active `vms.yaml` + `config.yaml`:

| File | Contents |
|---|---|
| `data/vm-group-<name>.txt` | Terminal filter for each VM (broker + account + instance lines), mounted into that VM as `/shared/config/vm-group.txt` |
| `.data/nginx/nginx.conf` | nginx routes with a per-terminal upstream on the owning VM's container |
| `.env` | Rewritten on every run: `MT5_PROJECT_DIR`, `API_TOKEN`, the `NOVNC_PORT` and `API_HOST_PORT` overrides given on that run, Tailscale and signal copier settings |
| `docker-compose.yml` | Rendered from `docker-compose.yml.j2` only when it does not exist yet |

## Adding a third VM

1. Add a new entry to `vms.yaml` (copy from `vms.yaml.example`) with a unique `name`, `service`, and `container_name`.
2. Assign some terminals to it via `vm: <new-name>` in `config.yaml`.
3. Regenerate the compose file.
4. `make up`. A bare `docker compose up -d` starts the container, but only `run.sh` regenerates `nginx.conf` and the vm-group files and sets up the DNAT that forwards the terminal ports into the new VM.

## Troubleshooting

- **Nginx 502 for a terminal**: check the generated nginx.conf at `.data/nginx/nginx.conf`. The `set $vm_upstream` line should name the correct container for that terminal's VM.
- **VM not booting**: verify `vms.yaml` has correct `cpuset` (don't overlap pins) and enough host RAM for all VMs combined. Start from `vms.yaml.example`.
- **Port conflicts**: each VM needs a unique `novnc_port`. Default single-VM is `8006`; add VMs on `8007`, `8008`, etc.
