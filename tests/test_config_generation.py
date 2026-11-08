"""Guards on the artifacts config_helper.py generates.

These cover the generated files — nginx config, terminal INI, compose — rather
than the parsing that feeds them. Each case matches a way a generated file has
been wrong without anything failing: the text was valid, the helper exited 0,
and the damage only surfaced when a container tried to start.

"""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# Where a VM without its own log_dir writes, through its /data/mt5-shared mount.
SHARED_LOG_DIR = "/data/mt5-shared/logs"

TWO_VMS = [
    {"name": "fast", "service": "mt5", "container_name": "mt5", "novnc_port": 8006},
    {"name": "bulk", "service": "mt5-b", "container_name": "mt5-b", "novnc_port": 8007},
]

TWO_VMS_WITH_LOG_DIRS = [
    {
        "name": "fast",
        "service": "mt5",
        "container_name": "mt5",
        "novnc_port": 8006,
        "log_dir": SHARED_LOG_DIR,
    },
    {
        "name": "bulk",
        "service": "mt5-b",
        "container_name": "mt5-b",
        "novnc_port": 8007,
        "log_dir": "/data/mt5-vm-b/logs",
    },
]

TWO_VMS_WITH_WICKWORKS = [
    {
        "name": "fast",
        "service": "mt5",
        "container_name": "mt5",
        "novnc_port": 8006,
        "wickworks_service": "wickworks",
    },
    {
        "name": "bulk",
        "service": "mt5-b",
        "container_name": "mt5-b",
        "novnc_port": 8007,
        "wickworks_service": "wickworks-b",
    },
]

WICKWORKS_IMAGE = (
    "psyb0t/wickworks:v0.7.0@sha256:"
    "2973055356e8879a4a9a4025422d5835e18235eece898c780fa61ed563c43590"
)


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "config_helper.py"
COMPOSE_TEMPLATE = Path(__file__).resolve().parents[1] / "docker-compose.yml.j2"


def _load_config_helper_module():
    module_path = HELPER_PATH
    spec = importlib.util.spec_from_file_location("config_helper_gen_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path, terminals):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"api_token": "test-token", "terminals": terminals}),
        encoding="utf-8",
    )
    return path


def _write_vms(tmp_path, vms):
    path = tmp_path / "vms.yaml"
    path.write_text(yaml.safe_dump({"vms": vms}), encoding="utf-8")
    return path


def _generate_nginx_conf(helper, config_path, tmp_path, monkeypatch, vms_path=None):
    outpath = tmp_path / "nginx.conf"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(
        helper,
        "VMS_PATH",
        str(vms_path) if vms_path else str(tmp_path / "absent-vms.yaml"),
    )
    monkeypatch.setattr("sys.argv", ["config_helper.py", "nginx_conf", str(outpath)])

    helper.main()

    return outpath.read_text(encoding="utf-8")


@pytest.fixture
def single_terminal_config(tmp_path):
    return _write_config(tmp_path, [{"broker": "acme", "account": "main", "port": 5001}])


def test_nginx_routes_never_use_a_literal_upstream_host(
    single_terminal_config, tmp_path, monkeypatch
):
    """nginx resolves a literal `proxy_pass http://host:port` at config-parse
    time, so one absent container aborts startup entirely and takes every
    healthy route down with it. Routes have to resolve per-request instead.
    """
    helper = _load_config_helper_module()

    content = _generate_nginx_conf(helper, single_terminal_config, tmp_path, monkeypatch)

    literal_upstreams = re.findall(r"proxy_pass\s+http://[^$\s;]+;", content)
    assert literal_upstreams == [], f"literal upstreams reintroduced: {literal_upstreams}"


def test_every_terminal_route_carries_a_resolver(
    single_terminal_config, tmp_path, monkeypatch
):
    """A variable upstream only defers resolution while a resolver is in scope.
    Without one nginx fails the request instead of the parse — the same outage,
    moved later, and invisible to a syntax check.
    """
    helper = _load_config_helper_module()

    content = _generate_nginx_conf(helper, single_terminal_config, tmp_path, monkeypatch)

    blocks = re.findall(r"location /acme/main[^{]*\{(.*?)\n        \}", content, re.S)
    assert blocks, "no terminal location blocks were generated"
    for block in blocks:
        assert "resolver " in block
        assert re.search(r"proxy_pass\s+\$", block), "route lacks a variable upstream"


def test_only_the_long_running_routes_wait_longer_than_nginx_default(
    single_terminal_config, tmp_path, monkeypatch
):
    """POST /compile can wait its turn behind other compiles, and the
    WebRequest, per-terminal MCP and file routes can run for minutes, so they
    need more than nginx's 60s read timeout. Every other route keeps the
    default: a stuck order or history call should not hold its connection
    for minutes.
    """
    helper = _load_config_helper_module()

    content = _generate_nginx_conf(helper, single_terminal_config, tmp_path, monkeypatch)

    blocks = dict(
        re.findall(r"        (location [^{]*?) \{(.*?)\n        \}", content, re.S)
    )
    compile_routes = {k: v for k, v in blocks.items() if k.endswith("compile")}
    long_routes = {
        f"location {prefix}{route}"
        for prefix in ("/acme/main/default/", "/acme/main/")
        for route in ("webrequest", "mcp", "files", "compile/files", "terminal/restart")
    }
    other_routes = {
        k: v for k, v in blocks.items()
        if k.startswith("location /acme/") and k not in long_routes
    }
    assert set(compile_routes) == {
        "location = /acme/main/default/compile",
        "location = /acme/main/compile",
    }
    for block in compile_routes.values():
        assert f"proxy_read_timeout {helper.COMPILE_PROXY_TIMEOUT};" in block
        assert "rewrite ^/acme/main/" in block
    assert long_routes <= set(blocks)
    for name in long_routes:
        assert f"proxy_read_timeout {helper.LONG_PROXY_TIMEOUT};" in blocks[name]
        assert f"proxy_send_timeout {helper.LONG_PROXY_TIMEOUT};" in blocks[name]
        assert "rewrite ^/acme/main/" in blocks[name]
    assert other_routes
    for name, block in other_routes.items():
        assert "proxy_read_timeout" not in block, f"{name} got a long timeout"


def test_terminals_route_to_their_own_vm_container(tmp_path, monkeypatch):
    helper = _load_config_helper_module()
    config_path = _write_config(
        tmp_path,
        [
            {"broker": "acme", "account": "hot", "port": 5001, "vm": "fast"},
            {"broker": "acme", "account": "cold", "port": 5002, "vm": "bulk"},
        ],
    )
    vms_path = _write_vms(tmp_path, TWO_VMS)

    content = _generate_nginx_conf(
        helper, config_path, tmp_path, monkeypatch, vms_path=vms_path
    )

    assert "http://mt5:5001" in content
    assert "http://mt5-b:5002" in content


def test_absent_vms_file_keeps_every_terminal_on_the_default_container(
    tmp_path, monkeypatch
):
    """Backward compatibility: a deployment that never heard of vms.yaml has to
    generate what it did before multi-VM existed.
    """
    helper = _load_config_helper_module()
    config_path = _write_config(
        tmp_path,
        [
            {"broker": "acme", "account": "one", "port": 5001},
            {"broker": "acme", "account": "two", "port": 5002},
        ],
    )

    content = _generate_nginx_conf(helper, config_path, tmp_path, monkeypatch)

    assert "http://mt5:5001" in content
    assert "http://mt5:5002" in content
    assert "mt5-b" not in content


def _checkout_with_only_the_example_topology(tmp_path):
    """A throwaway checkout: config_helper.py resolves vms.yaml, the compose
    template and config/ from its own location, so the real script runs against
    files laid out the way an install without a vms.yaml has them. Only
    vms.yaml.example is there, with two VMs no terminal belongs to.
    """
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "config_helper.py"
    shutil.copy(HELPER_PATH, script)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "api_token": "test-token",
                "terminals": [{"broker": "acme", "account": "main", "port": 5001}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "vms.yaml.example").write_text(
        yaml.safe_dump({"vms": TWO_VMS}), encoding="utf-8"
    )
    return script


def _run_helper(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_without_vms_yaml_the_only_vm_is_the_default_one_not_the_examples(tmp_path):
    """run.sh builds its VM list from `config_helper.py vms`: it writes a
    vm-group file and sets up DNAT for every name printed. It read
    vms.yaml.example when vms.yaml was absent and so listed that file's
    example VMs, while the compose file it seeds without a vms.yaml runs one
    `mt5` service.
    """
    script = _checkout_with_only_the_example_topology(tmp_path)

    vms = _run_helper(script, "vms")
    service = _run_helper(script, "vm_info", "default", "service")

    assert vms.returncode == 0, vms.stderr
    assert vms.stdout.split() == ["default"]
    assert service.stdout.strip() == "mt5"
    assert _run_helper(script, "vm_info", "fast").returncode != 0


def test_a_real_vms_yaml_still_defines_the_vm_list(tmp_path):
    script = _checkout_with_only_the_example_topology(tmp_path)
    (tmp_path / "vms.yaml").write_text(
        yaml.safe_dump({"vms": TWO_VMS_WITH_LOG_DIRS}), encoding="utf-8"
    )

    vms = _run_helper(script, "vms")

    assert vms.stdout.split() == ["fast", "bulk"]


def test_generate_compose_refuses_to_render_the_example_topology(tmp_path):
    """It overwrites the user's docker-compose.yml, so with no vms.yaml it
    must not quietly render the example's VMs (or the default VM, which has no
    ram or disk size for the template to use)."""
    script = _checkout_with_only_the_example_topology(tmp_path)
    shutil.copy(COMPOSE_TEMPLATE, tmp_path / "docker-compose.yml.j2")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    result = _run_helper(script, "generate_compose")

    assert result.returncode == 1
    assert "vms.yaml not found" in result.stderr
    assert "vms.yaml.example" in result.stderr
    assert (tmp_path / "docker-compose.yml").read_text(encoding="utf-8") == "services: {}\n"


def test_live_terminal_ini_declares_no_startup_expert(tmp_path, monkeypatch):
    """The generated INI decides what a terminal does on launch, so a `[StartUp]`
    section auto-attaches an expert to every live terminal — a fleet-wide
    behaviour change in a file nothing else asserts on. Adding that feature means
    editing this test deliberately, not inheriting it from an unrelated commit.
    """
    helper = _load_config_helper_module()
    config_path = _write_config(
        tmp_path, [{"broker": "acme", "account": "main", "port": 5001}]
    )
    outpath = tmp_path / "terminal.ini"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(
        "sys.argv",
        ["config_helper.py", "write_ini", "acme", "main", str(outpath), "default", "live"],
    )

    helper.main()

    content = outpath.read_text(encoding="utf-8")
    assert "[StartUp]" not in content
    assert "Expert=" not in content
    assert content.count("[Experts]") == 1


@pytest.mark.parametrize("chartctl", [{"enabled": True}, True])
def test_startup_expert_is_written_only_when_chartctl_is_opted_in(
    tmp_path, monkeypatch, chartctl
):
    """The other half of the guard above.

    That test pins the default; this one pins that the feature still works when
    asked for, so "no [StartUp]" cannot be satisfied by quietly breaking the
    loader bootstrap. Chart Deployments is opt-IN: an upgrade must not start
    attaching an EA to every live terminal in a fleet that never enabled it.
    A bare `chartctl: true` is the same opt-in and used to crash the helper.
    """
    helper = _load_config_helper_module()
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({
            "api_token": "test-token",
            "chartctl": chartctl,
            "terminals": [{"broker": "acme", "account": "main", "port": 5001}],
        }),
        encoding="utf-8",
    )
    outpath = tmp_path / "terminal.ini"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(path))
    monkeypatch.setattr(
        "sys.argv",
        ["config_helper.py", "write_ini", "acme", "main", str(outpath), "default", "live"],
    )

    helper.main()

    content = outpath.read_text(encoding="utf-8")
    assert "[StartUp]" in content
    assert "Expert=Advisors\\MT5ChartLoader" in content


def test_a_terminal_can_opt_out_even_when_chartctl_is_on(tmp_path, monkeypatch):
    """Per-terminal `chartctl: false` has to beat the global enable, or there is
    no way to keep one terminal clear of the loader."""
    helper = _load_config_helper_module()
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({
            "api_token": "test-token",
            "chartctl": {"enabled": True},
            "terminals": [
                {"broker": "acme", "account": "main", "port": 5001, "chartctl": False}
            ],
        }),
        encoding="utf-8",
    )
    outpath = tmp_path / "terminal.ini"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(path))
    monkeypatch.setattr(
        "sys.argv",
        ["config_helper.py", "write_ini", "acme", "main", str(outpath), "default", "live"],
    )

    helper.main()

    assert "[StartUp]" not in outpath.read_text(encoding="utf-8")


def test_clean_start_uses_single_vm_compose_without_explicit_topology(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    run_script = (repo_root / "run.sh").read_text(encoding="utf-8")
    bootstrap = run_script.split("# Check for KVM", 1)[0]
    compose_example = (repo_root / "docker-compose.yml.example").read_text(
        encoding="utf-8"
    )

    (tmp_path / "run.sh").write_text(bootstrap, encoding="utf-8")
    (tmp_path / "docker-compose.yml.example").write_text(
        compose_example,
        encoding="utf-8",
    )

    subprocess.run(["bash", str(tmp_path / "run.sh")], cwd=tmp_path, check=True)

    assert (tmp_path / "docker-compose.yml").read_text(
        encoding="utf-8"
    ) == compose_example


def test_generate_compose_emits_one_service_per_vm(tmp_path, monkeypatch):
    """Two VMs sharing a host port renders as valid YAML and dies at
    `docker compose up` with a port conflict, so assert the ports are distinct
    rather than just that both services exist.
    """
    helper = _load_config_helper_module()
    config_path = _write_config(
        tmp_path, [{"broker": "acme", "account": "main", "port": 5001}]
    )
    vms_path = _write_vms(tmp_path, TWO_VMS)
    outpath = tmp_path / "docker-compose.yml"
    template_path = Path(__file__).resolve().parents[1] / "docker-compose.yml.j2"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(helper, "VMS_PATH", str(vms_path))
    monkeypatch.setattr(helper, "COMPOSE_TEMPLATE_PATH", str(template_path))
    monkeypatch.setattr(helper, "COMPOSE_OUTPUT_PATH", str(outpath))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "generate_compose"])

    helper.main()

    services = yaml.safe_load(outpath.read_text(encoding="utf-8"))["services"]
    assert "mt5" in services
    assert "mt5-b" in services

    host_ports = [
        port for name in ("mt5", "mt5-b") for port in (services[name].get("ports") or [])
    ]
    assert len(set(host_ports)) == len(host_ports), f"VMs share a host port: {host_ports}"


def test_generate_compose_gives_wickworks_a_self_healing_healthcheck(
    tmp_path, monkeypatch
):
    """The wickworks TA sidecar shares the mt5 netns. When the mt5 container is
    recreated it is orphaned in the old netns while its own loopback healthcheck
    still passes, so the generated compose must mount and run the self-heal
    healthcheck that detects the orphan and forces a restart.
    """
    helper = _load_config_helper_module()
    config_path = _write_config(
        tmp_path, [{"broker": "acme", "account": "main", "port": 5001}]
    )
    vms_path = _write_vms(tmp_path, TWO_VMS_WITH_WICKWORKS)
    outpath = tmp_path / "docker-compose.yml"
    template_path = Path(__file__).resolve().parents[1] / "docker-compose.yml.j2"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(helper, "VMS_PATH", str(vms_path))
    monkeypatch.setattr(helper, "COMPOSE_TEMPLATE_PATH", str(template_path))
    monkeypatch.setattr(helper, "COMPOSE_OUTPUT_PATH", str(outpath))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "generate_compose"])

    helper.main()

    services = yaml.safe_load(outpath.read_text(encoding="utf-8"))["services"]
    for name in ("wickworks", "wickworks-b"):
        svc = services[name]
        assert svc["image"] == WICKWORKS_IMAGE
        expected_owner = {"wickworks": "mt5", "wickworks-b": "mt5-b"}[name]
        assert svc["network_mode"] == "service:" + expected_owner
        assert "./scripts/wickworks-healthcheck.py:/wickworks-healthcheck.py:ro" in svc["volumes"]
        hc = svc["healthcheck"]["test"]
        assert hc == ["CMD", "python", "/wickworks-healthcheck.py"]


def test_log_rotator_mounts_only_logs_and_terminal_journals(tmp_path, monkeypatch):
    helper = _load_config_helper_module()
    config_path = _write_config(
        tmp_path, [{"broker": "acme", "account": "main", "port": 5001}]
    )
    vms_path = _write_vms(tmp_path, TWO_VMS)
    outpath = tmp_path / "docker-compose.yml"
    template_path = Path(__file__).resolve().parents[1] / "docker-compose.yml.j2"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(helper, "VMS_PATH", str(vms_path))
    monkeypatch.setattr(helper, "COMPOSE_TEMPLATE_PATH", str(template_path))
    monkeypatch.setattr(helper, "COMPOSE_OUTPUT_PATH", str(outpath))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "generate_compose"])

    helper.main()

    services = yaml.safe_load(outpath.read_text(encoding="utf-8"))["services"]
    rotator = services["log-rotator"]
    assert rotator["environment"] == {
        "LOG_DIRS": "/logs-shared",
        "TERMINALS_DIR": "/terminals",
        "RETAIN_DAYS": "7",
        # Age alone leaves today's journal unbounded, and one backtest can
        # write tens of gigabytes into it — so the rotator needs the size cap
        # wired up too, not just the retention window.
        "MAX_LOG_BYTES": "2147483648",
        "IDLE_MINUTES": "30",
        "INTERVAL": "3600",
    }
    assert rotator["volumes"] == [
        f"{SHARED_LOG_DIR}:/logs-shared",
        "/data/mt5-shared/terminals:/terminals",
        "./scripts/rotate-logs.sh:/rotate.sh:ro",
    ]


def test_default_compose_exposes_terminal_retention_to_the_rotator():
    repo_root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load(
        (repo_root / "docker-compose.yml.example").read_text(encoding="utf-8")
    )
    rotator = compose["services"]["log-rotator"]

    assert rotator["environment"] == {
        "LOG_DIR": "/logs",
        "TERMINALS_DIR": "/terminals",
        "RETAIN_DAYS": "7",
        # Age alone leaves today's journal unbounded, and one backtest can
        # write tens of gigabytes into it — so the rotator needs the size cap
        # wired up too, not just the retention window.
        "MAX_LOG_BYTES": "2147483648",
        "IDLE_MINUTES": "30",
        "INTERVAL": "3600",
    }
    assert compose["services"]["wickworks"]["image"] == WICKWORKS_IMAGE
    assert rotator["volumes"] == [
        "./data/shared/logs:/logs",
        "./data/shared/terminals:/terminals",
        "./scripts/rotate-logs.sh:/rotate.sh:ro",
    ]


def _generate(tmp_path, monkeypatch, vms):
    helper = _load_config_helper_module()
    config_path = _write_config(
        tmp_path, [{"broker": "acme", "account": "main", "port": 5001}]
    )
    vms_path = _write_vms(tmp_path, vms)
    outpath = tmp_path / "docker-compose.yml"
    template_path = Path(__file__).resolve().parents[1] / "docker-compose.yml.j2"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(helper, "VMS_PATH", str(vms_path))
    monkeypatch.setattr(helper, "COMPOSE_TEMPLATE_PATH", str(template_path))
    monkeypatch.setattr(helper, "COMPOSE_OUTPUT_PATH", str(outpath))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "generate_compose"])
    helper.main()
    return yaml.safe_load(outpath.read_text(encoding="utf-8"))["services"]


def _rotator(services):
    svc = services["log-rotator"]
    mounts = {}
    for vol in svc["volumes"]:
        host, _, container = vol.partition(":")
        if container.startswith("/logs"):
            mounts[host] = container.split(":")[0]
    return svc, mounts


def test_log_rotator_covers_every_vm_log_directory(tmp_path, monkeypatch):
    """A VM's log_dir is mounted OVER /shared/logs, so VMs with different
    log_dirs write to different HOST directories. The rotator used to mount one
    hardcoded path, which is why mt5-b's logs were never rotated at all."""
    services = _generate(tmp_path, monkeypatch, TWO_VMS_WITH_LOG_DIRS)
    svc, mounts = _rotator(services)

    for vm in TWO_VMS_WITH_LOG_DIRS:
        assert vm["log_dir"] in mounts, (vm["log_dir"], mounts)

    listed = svc["environment"]["LOG_DIRS"].split(":")
    assert sorted(listed) == sorted(mounts.values()), (listed, mounts)


def test_log_rotator_rotates_a_shared_directory_only_once(tmp_path, monkeypatch):
    """Two VMs may point at one directory; rotating it twice per pass is waste
    at best and a race between two passes over the same files at worst."""
    vms = [dict(vm, log_dir=SHARED_LOG_DIR) for vm in TWO_VMS_WITH_LOG_DIRS]
    services = _generate(tmp_path, monkeypatch, vms)
    svc, mounts = _rotator(services)

    assert list(mounts) == [SHARED_LOG_DIR]
    assert svc["environment"]["LOG_DIRS"].count(":") == 0, svc["environment"]["LOG_DIRS"]


def test_log_rotator_falls_back_to_the_shared_directory(tmp_path, monkeypatch):
    """A topology that names no log_dir is a single-VM install writing to the
    shared directory. It must still be rotated, not silently skipped."""
    services = _generate(tmp_path, monkeypatch, TWO_VMS)
    svc, mounts = _rotator(services)

    assert list(mounts) == [SHARED_LOG_DIR]
    assert svc["environment"]["LOG_DIRS"] == "/logs-shared"


def test_log_rotator_covers_a_vm_without_log_dir_beside_one_with_it(
    tmp_path, monkeypatch
):
    """A VM that omits log_dir writes to the shared directory even when another
    VM sets its own, so the shared directory must still be mounted and listed."""
    vms = [TWO_VMS[0], TWO_VMS_WITH_LOG_DIRS[1]]
    services = _generate(tmp_path, monkeypatch, vms)
    svc, mounts = _rotator(services)

    assert mounts == {
        SHARED_LOG_DIR: "/logs-shared",
        "/data/mt5-vm-b/logs": "/logs-bulk",
    }
    assert sorted(svc["environment"]["LOG_DIRS"].split(":")) == [
        "/logs-bulk",
        "/logs-shared",
    ]


def test_every_vm_log_dir_in_the_live_topology_is_rotated():
    """Guards the real vms.yaml, not just a fixture: a VM whose log directory has
    no matching rotator mount grows unbounded and nothing reports it. A VM with
    no log_dir writes to the shared directory, so that one must be mounted too.
    Only a compose generated from the template is checked; one written by hand
    from docker-compose.yml.example mounts its own paths."""
    root = Path(__file__).resolve().parents[1]
    vms_path = root / "vms.yaml"
    compose_path = root / "docker-compose.yml"
    if not (vms_path.exists() and compose_path.exists()):
        pytest.skip("no generated topology to check")

    services = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]
    rotator = services.get("log-rotator") or {}
    if "LOG_DIRS" not in rotator.get("environment", {}):
        pytest.skip("docker-compose.yml was not generated from the template")

    vms = yaml.safe_load(vms_path.read_text(encoding="utf-8"))["vms"]
    mounted = {vol.split(":")[0] for vol in rotator["volumes"]}
    log_dirs = {vm.get("log_dir") or SHARED_LOG_DIR for vm in vms}
    assert sorted(log_dirs - mounted) == []


@pytest.mark.parametrize(
    "value, printed",
    [
        (None, ""),  # unset: start.bat leaves Windows' desktop heap alone
        (0, ""),
        (False, ""),
        (65536, "65536"),
        ("65536", "65536"),
        ("restore", "restore"),
        (" 40960 ", "40960"),
    ],
)
def test_desktop_heap_kb_is_opt_in(tmp_path, monkeypatch, capsys, value, printed):
    helper = _load_config_helper_module()
    cfg = {"api_token": "t", "terminals": []}
    if value is not None:
        cfg["desktop_heap_kb"] = value
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "desktop_heap_kb"])

    helper.main()

    assert capsys.readouterr().out == printed + "\n"
