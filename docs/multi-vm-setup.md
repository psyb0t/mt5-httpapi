# Multi-VM Setup

This guide covers deploying **mt5-httpapi** with more than one Windows VM — spreading terminals across NUMA nodes, isolating hot-SSD tiers from bulk-HDD storage, or scaling past what a single VM can hold.

## How it works

- **`vms.yaml`** (copy `vms.yaml.example`) declares each VM's resources (cpuset, RAM, CPU cores, disk size, storage path, noVNC port, wickworks sidecar name).
- **`config/config.yaml`** gains a `vm` field on each terminal entry, binding that terminal to a specific VM.
- **`scripts/config_helper.py`** reads both files to generate nginx routes targeting the correct container (`proxy_pass http://mt5:<port>` vs `http://mt5-b:<port>`).
- **`run.sh`** loops over all VMs for DNAT/iptables setup and auto-generates per-VM group files.
- **`docker-compose.yml.j2`** renders the compose file from `vms.yaml` via `config_helper.py generate_compose`.

## Backward compatibility

- **No `vms.yaml`** → single-VM mode. All terminals route to the default `mt5` container. The example file is documentation only and does not opt into multi-VM generation.
- **No `vm` field on a terminal** → defaults to `default`, routes to `mt5`.

## Prerequisites

- mt5-httpapi v4.10.0+
- Linux host with KVM (`/dev/kvm`), enough RAM for N VMs, and CPU cores you can pin

## Step-by-step

### 1. Copy and edit `vms.yaml.example`

Start from the example and customize it:

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
| `container_name` | yes | Docker container hostname for nginx routing |
| `cpuset` | no | CPU pinning (docker compose `cpuset`) |
| `ram` | yes | RAM for the Windows VM (e.g. `"112G"`, `"4G"`) |
| `cpu_cores` | yes | vCPU count |
| `disk_size` | yes | VM disk size (e.g. `"300G"`) |
| `storage` | yes | Host path for VM system disk |
| `log_dir` | no | Host path mounted at `/shared/logs`; omit to use the compose template's default log storage |
| `novnc_port` | no | Host port for noVNC (e.g. `8006`, `8007`) |
| `wickworks_service` | no | Name of the wickworks sidecar service |
| `mem_limit` | no | Docker memory limit (default `116G`) |
| `memswap_limit` | no | Docker mem+swap limit (default `120G`) |
| `extra_binds` | no | Additional host→container bind mounts (hot-tier terminal dirs) |

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

This renders one service block per VM (with its wickworks sidecar), plus the shared services (log-rotator, mcpunifier, nginx).

## Generated files

`run.sh` auto-generates these from the active `vms.yaml` + `config.yaml`:

| File | Contents |
|---|---|
| `data/vm-group-<name>.txt` | Terminal filter for each VM (broker + account + instance lines) |
| `.data/nginx/nginx.conf` | nginx routes with per-terminal `proxy_pass` to the owning VM's container |

## Adding a third VM

1. Add a new entry to `vms.yaml` (copy from `vms.yaml.example`) with a unique `name`, `service`, and `container_name`.
2. Assign some terminals to it via `vm: <new-name>` in `config.yaml`.
3. Regenerate the compose file.
4. `docker compose up -d` — nginx routes terminals to the new container automatically.

## Troubleshooting

- **Nginx 502 for a terminal**: check the generated nginx.conf at `.data/nginx/nginx.conf`. The `proxy_pass` should target the correct container name for that terminal's VM.
- **VM not booting**: verify `vms.yaml` has correct `cpuset` (don't overlap pins) and enough host RAM for all VMs combined. Start from `vms.yaml.example`.
- **Port conflicts**: each VM needs a unique `novnc_port`. Default single-VM is `8006`; add VMs on `8007`, `8008`, etc.
