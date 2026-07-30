"""Guards on the artifacts config_helper.py generates.

These cover the generated files — nginx config, terminal INI, compose — rather
than the parsing that feeds them. Each case matches a way a generated file has
been wrong without anything failing: the text was valid, the helper exited 0,
and the damage only surfaced when a container tried to start.

"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

TWO_VMS = [
    {"name": "fast", "service": "mt5", "container_name": "mt5", "novnc_port": 8006},
    {"name": "bulk", "service": "mt5-b", "container_name": "mt5-b", "novnc_port": 8007},
]


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
