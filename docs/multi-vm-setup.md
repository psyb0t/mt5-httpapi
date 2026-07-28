# Multi-VM Setup

This guide covers deploying **mt5-httpapi** with more than one Windows VM — spreading terminals across NUMA nodes, isolating hot-SSD tiers from bulk-HDD storage, or scaling past what a single VM can hold.

## How it works

- **`vms.yaml`** declares each VM's resources (cpuset, RAM, CPU cores, disk size, storage path, noVNC port, wickworks sidecar name).
- **`config/config.yaml`** gains a `vm` field on each terminal entry, binding that terminal to a specific VM.
- **`scripts/config_helper.py`** reads both files to generate nginx routes targeting the correct container (`proxy_pass http://mt5:<port>` vs `http://mt5-b:<port>`).
- **`run.sh`** loops over all VMs for DNAT/iptables setup and auto-generates per-VM group files.
- **`docker-compose.yml.j2`** renders the compose file from `vms.yaml` via `config_helper.py generate_compose`.

## Backward compatibility

- **No `vms.yaml`** → single-VM mode. All terminals route to the default `mt5` container. Everything works exactly as before.
- **No `vm` field on a terminal** → defaults to `default`, routes to `mt5`.

## Prerequisites

- mt5-httpapi v4.10.0+
- Linux host with KVM (`/dev/kvm`), enough RAM for N VMs, and CPU cores you can pin

## Step-by-step

### 1. Create `vms.yaml`

Define one entry per VM. The `name` is how terminals reference it in `config.yaml`.

```yaml
vms:
  - name: fast
    service: mt5
    container_name: mt5
    cpuset: "0-19,40-59"
    ram: "112G"
    cpu_cores: 40
    disk_size: "300G"
    storage: /data/mt5-vm-a/storage
    hot_tier: /mnt/mt5-hot
    log_dir: /data/mt5-shared/logs
    novnc_port: 8006
    wickworks_service: wickworks
    mem_limit: 116G
    memswap_limit: 120G
    extra_binds:
      - /mnt/ssd/terminals/darwinex/live/a:/shared/terminals/darwinex/live/a

  - name: bulk
    service: mt5-b
    container_name: mt5-b
    cpuset: "20-39,60-79"
    ram: "112G"
    cpu_cores: 40
    disk_size: "150G"
    storage: /data/mt5-vm-b/storage
    hot_tier: /mnt/mt5-hot-b
    log_dir: /data/mt5-vm-b/logs
    novnc_port: 8007
    wickworks_service: wickworks-b
    mem_limit: 116G
    memswap_limit: 120G
    extra_binds:
      - /mnt/hdd/terminals/blackbull/live-prime:/shared/terminals/blackbull/live-prime
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

On first run, `run.sh` detects `vms.yaml` + `docker-compose.yml.j2` and generates the compose file automatically. To regenerate:

```bash
python3 scripts/config_helper.py generate_compose
```

This renders one service block per VM (with its wickworks sidecar), plus the shared services (log-rotator, mcpunifier, nginx).

### 4. Set per-VM concurrency caps (optional)

Each VM has its own RAM bank — set independent in-flight ceilings via environment variables:

```bash
export MT5_HTTPAPI_MAX_IN_FLIGHT_FAST=6
export MT5_HTTPAPI_MAX_IN_FLIGHT_BULK=10
```

The global cap `MT5_HTTPAPI_MAX_IN_FLIGHT` still applies on top. Per-VM caps are additive — both must be satisfied.

## Generated files

`run.sh` auto-generates these from `vms.yaml` + `config.yaml`:

| File | Contents |
|---|---|
| `data/vm-group-<name>.txt` | Terminal filter for each VM (broker + account + instance lines) |
| `.data/nginx/nginx.conf` | nginx routes with per-terminal `proxy_pass` to the owning VM's container |

## Adding a third VM

1. Add a new entry to `vms.yaml` with a unique `name`, `service`, and `container_name`.
2. Assign some terminals to it via `vm: <new-name>` in `config.yaml`.
3. Regenerate the compose file.
4. Set `MT5_HTTPAPI_MAX_IN_FLIGHT_<NAME>` if needed.
5. `docker compose up -d` — nginx routes terminals to the new container automatically.

## Troubleshooting

- **Nginx 502 for a terminal**: check the generated nginx.conf at `.data/nginx/nginx.conf`. The `proxy_pass` should target the correct container name for that terminal's VM.
- **VM not booting**: verify `vms.yaml` has correct `cpuset` (don't overlap pins) and enough host RAM for all VMs combined.
- **Port conflicts**: each VM needs a unique `novnc_port`. Default single-VM is `8006`; add VMs on `8007`, `8008`, etc.
- **Per-VM cap not applying**: ensure the env var name matches the VM name in `vms.yaml`, uppercased: `MT5_HTTPAPI_MAX_IN_FLIGHT_<NAME>`.
