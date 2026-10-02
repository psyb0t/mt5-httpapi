"""Guards on the artifacts config_helper.py generates.

These cover the generated files — nginx config, terminal INI, compose — rather
than the parsing that feeds them. Each case matches a way a generated file has
been wrong without anything failing: the text was valid, the helper exited 0,
and the damage only surfaced when a container tried to start.

"""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

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
        "log_dir": "/data/mt5-shared/logs",
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


def _load_config_helper_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "config_helper.py"
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
    monkeypatch.setattr(
        helper, "_VMS_EXAMPLE_PATH", str(tmp_path / "absent-vms.example.yaml")
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


def test_only_the_compile_route_waits_longer_than_nginx_default(
    single_terminal_config, tmp_path, monkeypatch
):
    """POST /compile can wait its turn behind other compiles, so it needs more
    than nginx's 60s read timeout. Every other route keeps the default: a
    stuck order or history call should not hold its connection for minutes.
    """
    helper = _load_config_helper_module()

    content = _generate_nginx_conf(helper, single_terminal_config, tmp_path, monkeypatch)

    blocks = dict(
        re.findall(r"        (location [^{]*?) \{(.*?)\n        \}", content, re.S)
    )
    compile_routes = {k: v for k, v in blocks.items() if k.endswith("compile")}
    other_routes = {
        k: v for k, v in blocks.items() if k.startswith("location /acme/")
    }
    assert set(compile_routes) == {
        "location = /acme/main/default/compile",
        "location = /acme/main/compile",
    }
    for block in compile_routes.values():
        assert f"proxy_read_timeout {helper.COMPILE_PROXY_TIMEOUT};" in block
        assert "rewrite ^/acme/main/" in block
    assert other_routes
    for name, block in other_routes.items():
        assert "proxy_read_timeout" not in block, f"{name} got the compile timeout"


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
        "/data/mt5-shared/logs:/logs-shared",
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
    vms = [dict(vm, log_dir="/data/mt5-shared/logs") for vm in TWO_VMS_WITH_LOG_DIRS]
    services = _generate(tmp_path, monkeypatch, vms)
    svc, mounts = _rotator(services)

    assert list(mounts) == ["/data/mt5-shared/logs"]
    assert svc["environment"]["LOG_DIRS"].count(":") == 0, svc["environment"]["LOG_DIRS"]


def test_log_rotator_falls_back_to_the_shared_directory(tmp_path, monkeypatch):
    """A topology that names no log_dir is a single-VM install writing to the
    shared directory. It must still be rotated, not silently skipped."""
    services = _generate(tmp_path, monkeypatch, TWO_VMS)
    svc, mounts = _rotator(services)

    assert list(mounts) == ["/data/mt5-shared/logs"]
    assert svc["environment"]["LOG_DIRS"] == "/logs-shared"


def test_every_vm_log_dir_in_the_live_topology_is_rotated():
    """Guards the real vms.yaml, not just a fixture: a VM added with a log_dir
    and no matching rotator mount grows unbounded and nothing reports it."""
    root = Path(__file__).resolve().parents[1]
    vms_path = root / "vms.yaml"
    compose_path = root / "docker-compose.yml"
    if not (vms_path.exists() and compose_path.exists()):
        pytest.skip("no generated topology to check")

    vms = yaml.safe_load(vms_path.read_text(encoding="utf-8"))["vms"]
    rotator = yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"].get(
        "log-rotator"
    )
    assert rotator, "the stack has no log-rotator"

    mounted = {vol.split(":")[0] for vol in rotator["volumes"]}
    missing = [vm["log_dir"] for vm in vms if vm.get("log_dir") and vm["log_dir"] not in mounted]
    assert missing == [], missing
