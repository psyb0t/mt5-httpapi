"""The signal copier services exist in both compose files, behind the
profiles run.sh turns on from config.yaml, and config_helper.py picks those
profiles."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]


def _helper():
    spec = importlib.util.spec_from_file_location("config_helper_signals", _REPO / "scripts" / "config_helper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_template():
    from jinja2 import Template

    source = (_REPO / "docker-compose.yml.j2").read_text(encoding="utf-8")
    vms = [{"name": "default", "service": "mt5", "ram": "4G", "cpu_cores": 2, "disk_size": "32G"}]
    return yaml.safe_load(Template(source).render(vms=vms, enable_mcpunifier=True))


def _example():
    return yaml.safe_load((_REPO / "docker-compose.yml.example").read_text(encoding="utf-8"))


@pytest.mark.parametrize("compose", [_example, _render_template], ids=["example", "template"])
def test_both_compose_files_gate_the_copier_behind_profiles(compose):
    services = compose()["services"]
    assert services["signals"]["profiles"] == ["signals"]
    assert services["telethon-plus"]["profiles"] == ["telegram"]
    assert "ports" not in services["telethon-plus"]
    assert services["telethon-plus"]["environment"]["TELETHON_READ_ONLY"] == "true"
    assert "./config/signals.yml:/app/config/signals.yml:ro" in services["signals"]["volumes"]
    assert services["signals"]["read_only"] is True
    assert "signals-state" in compose()["volumes"]


def test_the_telethon_image_is_pinned_and_the_same_everywhere():
    makefile = (_REPO / "Makefile").read_text(encoding="utf-8")
    make_image = re.search(r"^TELETHON_IMAGE := (\S+)$", makefile, re.MULTILINE).group(1)
    assert re.fullmatch(r"psyb0t/telethon-plus:v[\d.]+@sha256:[0-9a-f]{64}", make_image)
    assert _example()["services"]["telethon-plus"]["image"] == make_image
    assert _render_template()["services"]["telethon-plus"]["image"] == make_image


def test_the_copier_image_pins_its_dependencies_like_the_unifier():
    def pins(name):
        lines = (_REPO / name).read_text(encoding="utf-8").splitlines()
        return dict(line.split("==") for line in lines if line and not line.startswith("#"))

    signals = pins("requirements-signals.txt")
    unifier = pins("requirements-mcpunifier.txt")
    for package in ("httpx", "pyyaml", "python-json-logger"):
        assert signals[package] == unifier[package]
    assert set(signals) == {"httpx", "pyyaml", "python-json-logger", "websockets"}


@pytest.mark.parametrize(
    ("config", "rules", "profiles"),
    [
        ({}, "sources: {a: {type: telegram}}", []),
        ({"signals": {"enabled": False}}, "sources: {a: {type: telegram}}", []),
        ({"signals": True}, "sources: {a: {type: websocket}}", ["signals"]),
        (
            {"signals": {"enabled": True}},
            "sources: {a: {type: websocket}, b: {type: Telegram}}",
            ["signals", "telegram"],
        ),
        ({"signals": {"enabled": True}}, None, ["signals"]),
        ({"signals": {"enabled": True}}, "sources: [not, a, mapping]", ["signals"]),
    ],
)
def test_compose_profiles_follow_config_yaml_and_the_sources(tmp_path, config, rules, profiles):
    path = tmp_path / "signals.yml"
    if rules is not None:
        path.write_text(rules, encoding="utf-8")
    assert _helper()._compose_profiles(config, str(path)) == profiles


def test_telegram_setting_prints_only_known_keys(tmp_path, monkeypatch, capsys):
    helper = _helper()
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"telegram": {"api_id": 123456, "api_hash": " fakehash "}}), encoding="utf-8")
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config))

    monkeypatch.setattr(sys, "argv", ["config_helper.py", "telegram_setting", "api_id"])
    helper.main()
    monkeypatch.setattr(sys, "argv", ["config_helper.py", "telegram_setting", "api_hash"])
    helper.main()
    monkeypatch.setattr(sys, "argv", ["config_helper.py", "telegram_setting", "session"])
    helper.main()
    assert capsys.readouterr().out.splitlines() == ["123456", "fakehash", ""]

    monkeypatch.setattr(sys, "argv", ["config_helper.py", "telegram_setting", "password"])
    with pytest.raises(SystemExit):
        helper.main()
