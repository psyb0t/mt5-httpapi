"""signals.yml is checked at startup: every mistake stops the copier with a
ConfigError that names it, before anything connects or trades."""

from pathlib import Path

import pytest
import yaml

from signals.config import load_rules, load_settings, terminal_keys
from signals.errors import ConfigError
from tests.signals_helpers import RULES_YAML, TERMINALS, rules_from

_REPO = Path(__file__).resolve().parents[1]


def test_the_shipped_example_loads_against_the_config_template(monkeypatch):
    monkeypatch.setenv("MYBOT_TOKEN", "fake-token")
    config = yaml.safe_load((_REPO / "config" / "config.yaml.example").read_text(encoding="utf-8"))
    rules = load_rules(str(_REPO / "config" / "signals.yml.example"), terminal_keys(config))
    assert set(rules.sources) == {"goldsignals", "mybot"}
    assert rules.sources["mybot"].headers == {"Authorization": "Bearer fake-token"}
    assert all(target.dry_run for target in rules.targets)


def test_each_source_keeps_its_own_poll_interval(tmp_path):
    rules = rules_from(tmp_path)
    assert rules.sources["testchan"].poll_seconds == 60
    assert rules.sources["otherchan"].poll_seconds == 600


def _broken(tmp_path, old, new):
    assert old in RULES_YAML
    return lambda: rules_from(tmp_path, RULES_YAML.replace(old, new, 1))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("parser: classic\n    magic: 900001", "parser: nope\n    magic: 900001", "parsers does not define"),
        ("magic: 900002", "magic: 900001", "already used"),
        ("type: telegram\n    chat: \"@test", "type: carrier-pigeon\n    chat: \"@test", "type must be one of"),
        ("testchan:\n    type", "a-source-name-way-too-long:\n    type", "1 to 16"),
        ("poll_seconds: 600", "poll_seconds: 5", "at least 10"),
        ("(?P<side>buy|sell|long|short)", "(?P<direction>buy|sell|long|short)", "side"),
        ("account: demo\n    sizing", "account: missing\n    sizing", "config.yaml does not define"),
        ("mode: multiplier, factor: 0.1", "mode: martingale, factor: 0.1", "mode must be one of"),
        ("factor: 0.1", "factor: -1", "above 0"),
        ("tp_split: [50, 30, 20]", "tp_split: [10, 10, 10, 10, 10, 10, 10, 10, 10, 10]", "more than 9"),
        ("long: buy", "long: hold", "buy or sell"),
    ],
)
def test_a_broken_rule_is_refused_with_its_location(tmp_path, old, new, message):
    with pytest.raises(ConfigError, match=message):
        _broken(tmp_path, old, new)()


def test_a_level_regex_without_a_price_group_is_refused(tmp_path):
    lines = RULES_YAML.splitlines()
    broken = [line.replace("(?P<price>", "(?P<level>") if line.startswith("    sl:") else line for line in lines]
    with pytest.raises(ConfigError, match="parsers.classic.sl needs the named group"):
        rules_from(tmp_path, "\n".join(broken))


def test_an_invalid_regex_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="not a valid regex"):
        rules_from(tmp_path, RULES_YAML.replace("(?i)(?P<symbol>", "(?i)((?P<symbol>", 1))


def test_risk_sizing_needs_an_sl(tmp_path):
    text = RULES_YAML.replace(
        "sizing: {mode: multiplier, factor: 0.1, default_lots: 0.1}",
        "sizing: {mode: risk_percent, percent: 1}\n    require_sl: false",
    )
    with pytest.raises(ConfigError, match="risk_percent needs require_sl"):
        rules_from(tmp_path, text)


def test_a_websocket_source_needs_a_websocket_url(tmp_path):
    text = RULES_YAML.replace(
        "type: telegram\n    chat: \"@other_signal_channel\"",
        "type: websocket\n    url: https://feed.example.invalid",
    )
    with pytest.raises(ConfigError, match="ws:// or wss://"):
        rules_from(tmp_path, text)


def test_a_header_naming_an_unset_variable_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_FEED_TOKEN", raising=False)
    text = RULES_YAML.replace(
        "type: telegram\n    chat: \"@other_signal_channel\"",
        "type: websocket\n    url: wss://feed.example.invalid\n    headers: {X-Key: '${FAKE_FEED_TOKEN}'}",
    )
    with pytest.raises(ConfigError, match="FAKE_FEED_TOKEN"):
        rules_from(tmp_path, text)


def test_no_targets_is_refused(tmp_path):
    text = RULES_YAML.split("targets:")[0] + "targets: []\n"
    with pytest.raises(ConfigError, match="no targets"):
        rules_from(tmp_path, text)


def test_instance_defaults_to_default(tmp_path):
    rules = rules_from(tmp_path)
    assert rules.targets[0].terminal == "testbroker/demo/default"


def test_settings_take_the_token_and_telethon_key_from_config_yaml(tmp_path, monkeypatch):
    for name in ("API_TOKEN", "TELETHON_AUTH_KEY", "MT5_URL"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "api_token": "fake-api-token",
                "telegram": {"auth_key": "fake-telethon-key"},
                "terminals": [{"broker": "testbroker", "account": "demo", "port": 5001}],
            }
        ),
        encoding="utf-8",
    )
    signals = tmp_path / "signals.yml"
    signals.write_text(RULES_YAML, encoding="utf-8")
    settings = load_settings(str(config), str(signals))
    assert settings.api_token == "fake-api-token"
    assert settings.telethon_auth_key == "fake-telethon-key"
    assert settings.mt5_url == "http://nginx"
    assert terminal_keys(yaml.safe_load(config.read_text())) <= TERMINALS


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("10m", 600), ("10m30s", 630), ("1h35m40s", 5740), ("4h", 14400), ("1d", 86400), ("45s", 45)],
)
def test_max_signal_age_takes_a_duration(tmp_path, value, seconds):
    rules = rules_from(tmp_path, f"max_signal_age: {value}\n" + RULES_YAML)
    assert rules.max_signal_age_seconds == seconds
    assert rules.max_age_for("testchan") == seconds


@pytest.mark.parametrize("value", ["10", "600", "m", "10x", "0m", "30s10m", "-10m", "'1h 30m'"])
def test_a_max_signal_age_that_is_not_a_duration_is_refused(tmp_path, value):
    with pytest.raises(ConfigError, match="max_signal_age"):
        rules_from(tmp_path, f"max_signal_age: {value}\n" + RULES_YAML)


def test_a_source_can_set_its_own_max_signal_age(tmp_path):
    text = RULES_YAML.replace(
        "    magic: 900002\n", "    magic: 900002\n    max_signal_age: 1h35m40s\n", 1
    )
    rules = rules_from(tmp_path, "max_signal_age: 10m\n" + text)
    assert rules.max_age_for("testchan") == 600
    assert rules.max_age_for("otherchan") == 5740


def test_max_signal_age_defaults_to_five_minutes(tmp_path):
    assert rules_from(tmp_path).max_age_for("testchan") == 300


def test_the_old_max_signal_age_seconds_still_works(tmp_path):
    rules = rules_from(tmp_path, "max_signal_age_seconds: 900\n" + RULES_YAML)
    assert rules.max_age_for("testchan") == 900


def test_max_signal_age_and_max_signal_age_seconds_together_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="both set"):
        rules_from(tmp_path, "max_signal_age: 10m\nmax_signal_age_seconds: 600\n" + RULES_YAML)
