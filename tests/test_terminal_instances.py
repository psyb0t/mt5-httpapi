import importlib.util
from pathlib import Path

import pytest

from mt5api import config as cfg


def _load_config_helper_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "config_helper.py"
    spec = importlib.util.spec_from_file_location("config_helper_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def duplicate_terminals_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
accounts:
  darwinex:
    live:
      login: 1
      password: secret
      server: Darwinex-Live
terminals:
  - broker: darwinex
    account: live
    instance: a
    port: 6542
    utc_offset: "0"
    mode: backtest
  - broker: darwinex
    account: live
    instance: b
    port: 6543
    utc_offset: "0"
    mode: backtest
  - broker: ictrading
    account: demo
    port: 6544
    utc_offset: "2"
    mode: live
""".strip(),
        encoding="utf-8",
    )
    return config_path


def test_match_terminal_config_distinguishes_instances():
    terms = [
        {"broker": "darwinex", "account": "live", "instance": "a", "port": 6542},
        {"broker": "darwinex", "account": "live", "instance": "b", "port": 6543},
        {"broker": "ictrading", "account": "demo", "port": 6544},
    ]

    first = cfg.match_terminal_config(terms, broker="darwinex", account="live", instance="a")
    second = cfg.match_terminal_config(terms, broker="darwinex", account="live", instance="b")
    legacy = cfg.match_terminal_config(terms, broker="ictrading", account="demo")

    assert first["port"] == 6542
    assert first["instance"] == "a"
    assert second["port"] == 6543
    assert second["instance"] == "b"
    assert legacy["instance"] == cfg.DEFAULT_INSTANCE


def test_terminal_dir_candidates_prefer_instance_dir():
    candidates = cfg.terminal_dir_candidates("/terminals", "darwinex", "live", "a")

    assert candidates == [
        "/terminals/darwinex/live/a/terminal64.exe",
        "/terminals/darwinex/live/terminal64.exe",
        "/terminals/darwinex/base/terminal64.exe",
    ]


def test_make_identity_includes_instance():
    assert cfg.make_identity("darwinex", "live", "a") == "darwinex/live/a"
    assert cfg.make_identity("ictrading", "demo") == "ictrading/demo/default"
    assert cfg.make_identity("metaquotes") == "metaquotes"


def test_config_helper_terminals_emits_instance(duplicate_terminals_config, monkeypatch, capsys):
    helper = _load_config_helper_module()
    monkeypatch.setattr(helper, "CONFIG_PATH", str(duplicate_terminals_config))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "terminals"])

    helper.main()

    out = capsys.readouterr().out.strip().splitlines()
    assert out == [
        "darwinex live a 6542 0 backtest",
        "darwinex live b 6543 0 backtest",
        f"ictrading demo {helper.DEFAULT_INSTANCE} 6544 2 live",
    ]


def test_config_helper_nginx_conf_includes_instance_routes(duplicate_terminals_config, tmp_path, monkeypatch):
    helper = _load_config_helper_module()
    outpath = tmp_path / "nginx.conf"
    monkeypatch.setattr(helper, "CONFIG_PATH", str(duplicate_terminals_config))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "nginx_conf", str(outpath)])

    helper.main()

    content = outpath.read_text(encoding="utf-8")
    assert "location /darwinex/live/a/" in content
    assert "location /darwinex/live/b/" in content
    assert f"location /ictrading/demo/{helper.DEFAULT_INSTANCE}/" in content
    assert "location /ictrading/demo/" in content


def test_check_health_probes_only_this_vms_terminals():
    """The per-VM healthcheck must apply the same group filter as the launcher.

    Probing every port in config.yaml means each VM reports the other VM's
    terminals DOWN and the container is permanently unhealthy, which makes a
    real failure indistinguishable from the standing noise. On 2026-08-04 the
    fast VM had a 25,272-long failing streak listing only bulk-VM ports while
    all 12 of its own terminals were serving.
    """
    src = (Path(__file__).resolve().parents[1] / "scripts" / "check_health.py").read_text(
        encoding="utf-8"
    )

    assert "_vm_group_filter" in src, "check_health.py no longer applies the per-VM filter"
    assert "_in_group" in src
    # The filter has to be applied to the list that gets probed, not merely
    # imported and forgotten.
    assert "_in_group(t, allowed)" in src


def test_container_healthcheck_probes_only_this_vms_ports():
    """The container healthcheck must scope its probes to this VM's terminals.

    Docker surfaces this script's verdict as the container health status. Taking
    every port in config.yaml means each VM probes the other VM's terminals and
    reports unhealthy forever, so the status carries no signal and a real outage
    looks exactly like the standing noise.
    """
    src = (Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.sh").read_text(
        encoding="utf-8"
    )

    assert "VM_GROUP=/shared/config/vm-group.txt" in src
    assert 'groupfile="$VM_GROUP"' in src, "the group file is no longer passed to the filter"
    # No group file must mean no filter, not zero ports — otherwise every
    # single-VM install fails its healthcheck.
    assert 'if (!have_group) { print port; return }' in src
