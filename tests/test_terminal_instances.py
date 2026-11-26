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


def test_match_terminal_config_matches_an_unquoted_numeric_account():
    """YAML reads `account: 12345678` as an int; start.bat passes the login as
    the string "12345678". Missing the match falls back to the FIRST terminal's
    entry, so that terminal's settings (mode, chartctl) would apply here."""
    terms = [
        {"broker": "roboforex", "account": 11111111, "port": 6542, "chartctl": False},
        {"broker": "roboforex", "account": 22222222, "port": 6543},
    ]

    match = cfg.match_terminal_config(terms, broker="roboforex", account="22222222")

    assert match["port"] == 6543
    assert match["chartctl"] is None


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


def test_config_helper_passes_an_auto_utc_offset_through(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "terminals:\n  - broker: acme\n    account: demo\n    port: 6542\n    utc_offset: auto\n",
        encoding="utf-8",
    )
    helper = _load_config_helper_module()
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "terminals"])

    helper.main()

    assert capsys.readouterr().out.strip() == f"acme demo {helper.DEFAULT_INSTANCE} 6542 auto live"


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


@pytest.fixture
def vm_grouped_terminals_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
terminals:
  - broker: darwinex
    account: live
    vm: fast
    port: 6001
  - broker: darwinex
    account: live
    instance: b
    vm: fast
    port: 6002
  - broker: ictrading
    account: demo
    vm: bulk
    port: 6003
  - broker: fxcm
    account: live
    vm: bulk
    port: 6004
""".strip(),
        encoding="utf-8",
    )
    return config_path


def test_check_health_probes_only_this_vms_terminals(vm_grouped_terminals_config, monkeypatch, capsys):
    """The per-VM healthcheck must apply the same group filter as the launcher.

    Probing every port in config.yaml means each VM reports the other VM's
    terminals DOWN and the container is permanently unhealthy, which makes a
    real failure indistinguishable from the standing noise. On 2026-08-04 the
    fast VM had a 25,272-long failing streak listing only bulk-VM ports while
    all 12 of its own terminals were serving.

    This exercises the PRODUCER side of the group-file contract:
    config_helper.py's `vm_group <vm_name>` CLI command, which writes the
    `broker account [instance]` lines that healthcheck.sh's awk program (the
    CONSUMER) filters against. The consumer side is covered behaviorally in
    tests/test_healthcheck_behavior.py, in particular
    test_group_line_without_instance_selects_only_default_instance_terminal,
    which pins that a group line with no instance field maps to the literal
    key "default". Testing both ends closes the same contract.

    Note: check_health.py itself imports `_vm_group_filter`/`_in_group` from
    config_helper.py, but config_helper.py does not define either name — see
    test_config_helper_exposes_check_health_filter_hooks below, which records
    that gap as a strict xfail rather than asserting on names that don't exist.
    """
    helper = _load_config_helper_module()
    monkeypatch.setattr(helper, "CONFIG_PATH", str(vm_grouped_terminals_config))

    monkeypatch.setattr("sys.argv", ["config_helper.py", "vm_group", "fast"])
    helper.main()
    fast_out = capsys.readouterr().out.strip().splitlines()
    # A default-instance terminal emits exactly TWO fields (healthcheck.sh's
    # awk maps a 2-field line to the literal key "default"); the instance="b"
    # terminal emits exactly THREE.
    assert fast_out == ["darwinex live", "darwinex live b"]

    monkeypatch.setattr("sys.argv", ["config_helper.py", "vm_group", "bulk"])
    helper.main()
    bulk_out = capsys.readouterr().out.strip().splitlines()
    # The complement, with zero overlap with fast_out — proves the filter
    # EXCLUDES the other VM's terminals rather than merely emitting something.
    assert bulk_out == ["ictrading demo", "fxcm live"]


def test_config_helper_exposes_check_health_filter_hooks():
    """check_health.py imports _in_group/_vm_group_filter from config_helper and
    degrades to no-op stand-ins on ImportError. Both names must exist so the
    host-side start.bat status loop can apply the same per-VM scope as the
    container healthcheck (see test_config_helper_group_filter_* below for the
    behavioural contract).
    """
    helper = _load_config_helper_module()
    assert hasattr(helper, "_vm_group_filter")
    assert hasattr(helper, "_in_group")


def _write_group_file(tmp_path, content):
    shared = tmp_path / "shared"
    config = shared / "config"
    config.mkdir(parents=True)
    if content is not None:
        (config / "vm-group.txt").write_text(content, encoding="utf-8")
    return shared


def test_config_helper_group_filter_missing_group_file_selects_everything(
    tmp_path, monkeypatch
):
    """No group file (single-VM install) means no filter: every terminal is in
    scope, matching the awk fallback in healthcheck.sh.
    """
    helper = _load_config_helper_module()
    shared = _write_group_file(tmp_path, None)
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    allowed = helper._vm_group_filter()

    assert allowed is None
    assert helper._in_group({"broker": "darwinex", "account": "live"}, allowed)
    assert helper._in_group({"broker": "fxcm", "account": "demo"}, allowed)


def test_config_helper_group_filter_empty_group_file_selects_everything(
    tmp_path, monkeypatch
):
    """An existing-but-empty group file must NOT mean "select nothing" — the
    same trap the awk program documents (an empty group file has no valid
    `broker account` line, so it degrades to no-filter).
    """
    helper = _load_config_helper_module()
    shared = _write_group_file(tmp_path, "")
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    allowed = helper._vm_group_filter()

    assert allowed is None
    assert helper._in_group({"broker": "darwinex", "account": "live"}, allowed)


def test_config_helper_group_filter_ignores_comments_and_blank_lines(
    tmp_path, monkeypatch
):
    """Comment lines and blank lines in the group file must not be
    misinterpreted as broker/account entries.
    """
    helper = _load_config_helper_module()
    shared = _write_group_file(
        tmp_path, "# comment\n\ndarwinex demo\n# another\n\nfxcm live\n"
    )
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    allowed = helper._vm_group_filter()

    assert helper._in_group({"broker": "darwinex", "account": "demo"}, allowed)
    assert helper._in_group({"broker": "fxcm", "account": "live"}, allowed)
    assert not helper._in_group({"broker": "ictrading", "account": "live"}, allowed)


def test_config_helper_group_filter_selects_only_matching_terminals(
    tmp_path, monkeypatch
):
    helper = _load_config_helper_module()
    shared = _write_group_file(tmp_path, "darwinex demo\nfxcm live\n")
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    allowed = helper._vm_group_filter()

    assert helper._in_group({"broker": "darwinex", "account": "demo"}, allowed)
    assert helper._in_group({"broker": "fxcm", "account": "live"}, allowed)
    assert not helper._in_group({"broker": "darwinex", "account": "live"}, allowed)
    assert not helper._in_group({"broker": "ictrading", "account": "demo"}, allowed)


def test_config_helper_group_filter_group_line_without_instance_matches_default(
    tmp_path, monkeypatch
):
    """A group line with no instance field (`broker account`) maps to the
    literal key "default" and selects only the terminal that also has no
    instance set — not a sibling with an explicit instance.
    """
    helper = _load_config_helper_module()
    shared = _write_group_file(tmp_path, "darwinex live\n")
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    allowed = helper._vm_group_filter()

    assert helper._in_group({"broker": "darwinex", "account": "live"}, allowed)
    assert not helper._in_group(
        {"broker": "darwinex", "account": "live", "instance": "a"}, allowed
    )


def test_config_helper_group_filter_group_line_with_instance_matches_only_it(
    tmp_path, monkeypatch
):
    """Two terminals share broker+account and are distinguished only by
    instance; a `broker account instance` group line must select just the one
    whose instance matches.
    """
    helper = _load_config_helper_module()
    shared = _write_group_file(tmp_path, "darwinex live a\n")
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    allowed = helper._vm_group_filter()

    assert helper._in_group(
        {"broker": "darwinex", "account": "live", "instance": "a"}, allowed
    )
    assert not helper._in_group(
        {"broker": "darwinex", "account": "live", "instance": "b"}, allowed
    )
    assert not helper._in_group({"broker": "darwinex", "account": "live"}, allowed)


def test_container_healthcheck_probes_only_this_vms_ports():
    """The container healthcheck must scope its probes to this VM's terminals.

    Docker surfaces this script's verdict as the container health status. Taking
    every port in config.yaml means each VM probes the other VM's terminals and
    reports unhealthy forever, so the status carries no signal and a real outage
    looks exactly like the standing noise.

    This only asserts the loose structural wiring (an awk invocation fed the
    group file) still exists. The actual filtering behavior — including the
    empty-file and missing-file fallback traps — is covered behaviorally in
    tests/test_healthcheck_behavior.py, which runs the real awk program
    against fixture configs instead of grepping for source substrings.
    """
    src = (Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.sh").read_text(
        encoding="utf-8"
    )

    assert "awk" in src and "groupfile=" in src, "healthcheck.sh no longer wires a groupfile into awk"



def test_terminals_command_is_scoped_to_this_vms_group(
    vm_grouped_terminals_config, tmp_path, monkeypatch, capsys
):
    """`terminals` drives what start.bat actually launches, so it must honour
    the group file the same way check_health.py does.

    Unfiltered, every VM in a multi-VM install prepares a data dir and starts an
    API process for ALL terminals — including another VM's live-mode terminal,
    whose portable dir sits on the same shared mount, so the two terminal64.exe
    fight over MT5's single-instance lock and one exits silently with code 0.
    Regression: the filter hooks shipped for check_health.py but this command
    never called them.
    """
    helper = _load_config_helper_module()
    shared = _write_group_file(tmp_path, "darwinex live\ndarwinex live b\n")
    monkeypatch.setattr(helper, "CONFIG_PATH", str(vm_grouped_terminals_config))
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    monkeypatch.setattr("sys.argv", ["config_helper.py", "terminals"])
    helper.main()
    out = capsys.readouterr().out.strip().splitlines()

    # Only the fast VM's two terminals, and specifically NOT the bulk VM's.
    assert out == [
        "darwinex live default 6001 0 live",
        "darwinex live b 6002 0 live",
    ]


def test_terminals_command_unfiltered_without_a_group_file(
    vm_grouped_terminals_config, tmp_path, monkeypatch, capsys
):
    """No group file is a single-VM install: every terminal stays in scope, the
    same no-filter fallback _vm_group_filter() documents."""
    helper = _load_config_helper_module()
    shared = _write_group_file(tmp_path, None)
    monkeypatch.setattr(helper, "CONFIG_PATH", str(vm_grouped_terminals_config))
    monkeypatch.setattr(helper, "_SHARED_DIR", str(shared))

    monkeypatch.setattr("sys.argv", ["config_helper.py", "terminals"])
    helper.main()
    out = capsys.readouterr().out.strip().splitlines()

    assert len(out) == 4
