"""The unifier's per-terminal mode, chartctl and files flags, read from config.yaml.

list_terminals reports them so an agent knows which terminals serve the Chart
Deployments and file API tools and which process mode each one runs in, so they
have to agree with what mt5api itself decides for the same config.yaml. These
tests compare them against mt5api's own functions (match_terminal_config,
normalize_mode, chartctl_enabled, files_enabled) rather than a copy of the rule.
"""
import pytest
import yaml

from mcpunifier.config import load_settings
from mcpunifier.errors import ConfigError
from mt5api import config as api_config


def _write_config(tmp_path, terminal_extra, **blocks):
    terminal = {"broker": "ftmo", "account": "live1", "port": 6545, **terminal_extra}
    config = {"terminals": [terminal]}
    config.update({name: block for name, block in blocks.items() if block is not None})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path, config


def _matched(config):
    return api_config.match_terminal_config(
        config["terminals"],
        broker="ftmo",
        account="live1",
    )


def _api_chartctl(config):
    """CHARTCTL_ENABLED as mt5api computes it for the configured terminal."""
    matched = _matched(config)
    return api_config.chartctl_enabled(
        api_config.normalize_mode(matched["mode"]),
        config.get("chartctl"),
        matched["chartctl"],
    )


def _api_files(config):
    """FILES_ENABLED as mt5api computes it for the configured terminal."""
    matched = _matched(config)
    return api_config.files_enabled(config.get("files"), matched["files"])


@pytest.mark.parametrize(
    ("chartctl_block", "terminal_extra", "expected"),
    [
        (None, {"mode": "live"}, False),
        ({"enabled": False}, {"mode": "live"}, False),
        ({"enabled": True}, {"mode": "live"}, True),
        ({"enabled": True}, {"mode": "LIVE"}, True),
        # mt5api runs a terminal with no mode, or an unknown one, as live.
        ({"enabled": True}, {}, True),
        ({"enabled": True}, {"mode": "paper"}, True),
        ({"enabled": True}, {"mode": "backtest"}, False),
        ({"enabled": True}, {"mode": " Backtest "}, False),
        ({"enabled": True}, {"mode": "live", "chartctl": False}, False),
        ({"enabled": True}, {"mode": "live", "chartctl": True}, True),
        ({}, {"mode": "live"}, False),
        # A bare bool is shorthand for {enabled: <bool>}.
        (True, {"mode": "live"}, True),
        (False, {"mode": "live"}, False),
        (True, {"mode": "backtest"}, False),
    ],
)
def test_unifier_and_api_agree_on_whether_a_terminal_runs_chartctl(
    tmp_path,
    chartctl_block,
    terminal_extra,
    expected,
):
    path, config = _write_config(tmp_path, terminal_extra, chartctl=chartctl_block)

    (terminal,) = load_settings(str(path)).terminals.values()

    assert _api_chartctl(config) is expected
    assert terminal.chartctl is expected


@pytest.mark.parametrize(
    ("files_block", "terminal_extra", "expected"),
    [
        (None, {"mode": "live"}, False),
        ({"enabled": False}, {"mode": "live"}, False),
        ({"enabled": True}, {"mode": "live"}, True),
        # Unlike chartctl, the file API serves backtest terminals too.
        ({"enabled": True}, {"mode": "backtest"}, True),
        ({"enabled": True}, {"mode": "live", "files": False}, False),
        ({"enabled": True}, {"mode": "live", "files": True}, True),
        (True, {}, True),
        (False, {}, False),
        ({}, {}, False),
    ],
)
def test_unifier_and_api_agree_on_whether_a_terminal_serves_files(
    tmp_path,
    files_block,
    terminal_extra,
    expected,
):
    path, config = _write_config(tmp_path, terminal_extra, files=files_block)

    (terminal,) = load_settings(str(path)).terminals.values()

    assert _api_files(config) is expected
    assert terminal.files is expected


@pytest.mark.parametrize(
    ("terminal_extra", "expected"),
    [
        # mt5api runs a terminal with no mode, an empty one or an unknown one as live.
        ({}, "live"),
        ({"mode": None}, "live"),
        ({"mode": ""}, "live"),
        ({"mode": "paper"}, "live"),
        ({"mode": "live"}, "live"),
        ({"mode": " LIVE "}, "live"),
        ({"mode": "backtest"}, "backtest"),
        ({"mode": " Backtest "}, "backtest"),
    ],
)
def test_unifier_reports_the_process_mode_mt5api_runs(tmp_path, terminal_extra, expected):
    path, config = _write_config(tmp_path, terminal_extra)

    (terminal,) = load_settings(str(path)).terminals.values()

    assert api_config.normalize_mode(_matched(config)["mode"]) == expected
    assert terminal.mode == expected


@pytest.mark.parametrize("name", ["chartctl", "files"])
def test_a_feature_block_that_is_neither_bool_nor_mapping_is_a_config_error(tmp_path, name):
    path, _ = _write_config(tmp_path, {"mode": "live"}, **{name: "yes"})

    with pytest.raises(ConfigError, match=f"{name} .* must be true, false or a mapping"):
        load_settings(str(path))


def test_the_api_treats_a_malformed_feature_block_as_off():
    """The API is imported by everything, so a bad optional block must not
    stop it starting; it serves the feature as disabled."""
    assert api_config.feature_block("yes") == {}
    assert api_config.feature_block(["enabled"]) == {}
    assert api_config.feature_block(None) == {}
