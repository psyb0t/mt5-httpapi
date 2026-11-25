"""The scheduled VM reboot: the config.yaml reboot: block, the deprecated
top-level keys, `config_helper.py reboot_schedule` and how start.bat turns its
answer into the MT5AutoReboot task.

Nothing in CI boots Windows, so start.bat is read as text. The clock is
injected, so the DST cases do not depend on when the suite runs.
"""
from __future__ import annotations

import datetime
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = _ROOT / "scripts" / "config_helper.py"
START_BAT = _ROOT / "scripts" / "start.bat"

UTC = datetime.timezone.utc
# A fixed instant well away from any DST change: winter in Europe.
WINTER_NOW = datetime.datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
# Summer in Europe.
SUMMER_NOW = datetime.datetime(2030, 7, 15, 12, 0, tzinfo=UTC)
ZONE = "Europe/Bucharest"


@pytest.fixture(scope="module")
def helper():
    spec = importlib.util.spec_from_file_location("config_helper_reboot_test", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _settings(helper, cfg, now=WINTER_NOW):
    return helper.reboot_settings(cfg, now=now)


@pytest.mark.parametrize(
    ("cfg", "schedule"),
    [
        # Nothing configured: every 30 minutes, as before.
        ({}, "interval 30"),
        # The deprecated top-level keys, exactly as today.
        ({"reboot_interval": 45}, "interval 45"),
        ({"reboot_interval": "20"}, "interval 20"),
        ({"reboot_interval": 0}, "off"),
        ({"reboot_max_postpone": 10}, "interval 30"),
        # The block.
        ({"reboot": {}}, "interval 30"),
        ({"reboot": {"interval": 15}}, "interval 15"),
        ({"reboot": {"interval": 0}}, "off"),
        ({"reboot": {"enabled": False}}, "off"),
        ({"reboot": {"enabled": False, "at": "03:00"}}, "off"),
        ({"reboot": {"enabled": True, "interval": 60}}, "interval 60"),
        ({"reboot": False}, "off"),
        ({"reboot": True}, "interval 30"),
        ({"reboot": {"at": "03:00"}}, "daily 03:00"),
        ({"reboot": {"at": "3:05", "timezone": "UTC"}}, "daily 03:05"),
        ({"reboot": {"at": "03:00", "interval": 15}}, "daily 03:00"),
        ({"reboot": {"at": "03:00", "interval": 0}}, "daily 03:00"),
        ({"reboot": {"at": "00:00", "timezone": ZONE}}, "daily 22:00"),
        # The block wins over the old keys.
        ({"reboot": {"interval": 15}, "reboot_interval": 45}, "interval 15"),
        ({"reboot": {}, "reboot_interval": 0}, "interval 30"),
        ({"reboot": {"enabled": False}, "reboot_interval": 45}, "off"),
        # A block that is not a mapping is ignored: the old keys apply.
        ({"reboot": "daily", "reboot_interval": 45}, "interval 45"),
    ],
)
def test_the_schedule_follows_the_precedence_rules(helper, cfg, schedule):
    assert _settings(helper, cfg).schedule == schedule


@pytest.mark.parametrize(
    ("cfg", "max_postpone"),
    [
        ({}, 360),
        ({"reboot_max_postpone": 90}, 90),
        ({"reboot": {"max_postpone": 30}}, 30),
        ({"reboot": {"max_postpone": 0}}, 0),
        ({"reboot": {"max_postpone": 30}, "reboot_max_postpone": 90}, 30),
        # The block wins even without its own max_postpone.
        ({"reboot": {}, "reboot_max_postpone": 90}, 360),
        ({"reboot": {"enabled": False, "max_postpone": 5}}, 5),
    ],
)
def test_max_postpone_comes_from_the_block_or_the_old_key(helper, cfg, max_postpone):
    assert _settings(helper, cfg).max_postpone == max_postpone
    assert helper.reboot_max_postpone(cfg) == max_postpone


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({}, []),
        ({"reboot": {"interval": 15}}, []),
        ({"reboot": {"at": "03:00", "timezone": ZONE, "max_postpone": 60}}, []),
        ({"reboot_interval": 45}, ["reboot_interval in config.yaml is deprecated", "v5"]),
        (
            {"reboot_interval": 45, "reboot_max_postpone": 60},
            ["reboot_interval, reboot_max_postpone in config.yaml is deprecated"],
        ),
        ({"reboot": {}, "reboot_interval": 45}, ["the reboot: block wins"]),
        (
            {"reboot": {}, "reboot_max_postpone": 45},
            ["reboot: block and reboot_max_postpone", "the reboot: block wins"],
        ),
        ({"reboot": {"at": "03:00", "interval": 30}}, ["takes over from reboot.interval"]),
        ({"reboot": "daily"}, ["reboot must be a mapping"]),
        ({"reboot": {"enabled": "no"}}, ["reboot.enabled='no' is not true or false"]),
    ],
)
def test_deprecated_keys_and_overrides_are_warned_about(helper, cfg, expected):
    warnings = _settings(helper, cfg).warnings
    text = " | ".join(warnings)

    if not expected:
        assert warnings == []
    for fragment in expected:
        assert fragment in text


def test_at_alone_does_not_warn_about_the_interval(helper):
    assert _settings(helper, {"reboot": {"at": "03:00"}}).warnings == []


@pytest.mark.parametrize(
    ("raw", "interval"),
    [(-1, 30), (1440, 30), (2.5, 30), ("soon", 30), (True, 30), (1439, 1439), (1, 1)],
)
def test_an_interval_schtasks_cannot_take_falls_back_to_30(helper, raw, interval):
    for cfg, key in (({"reboot": {"interval": raw}}, "reboot.interval"),
                     ({"reboot_interval": raw}, "reboot_interval")):
        settings = _settings(helper, cfg)

        assert settings.interval == interval
        assert settings.kind == "interval"
        warned = any(w.startswith(f"{key}=") for w in settings.warnings)
        assert warned == (interval != raw)


@pytest.mark.parametrize("raw", ["24:00", "12:60", "noon", "1230", "12:5", "-1:00", 1440, -5, 2.5])
def test_an_invalid_at_falls_back_to_the_interval_with_a_warning(helper, raw):
    settings = _settings(helper, {"reboot": {"at": raw, "interval": 20}})

    assert settings.schedule == "interval 20"
    assert any("reboot.at=" in w and "using the interval" in w for w in settings.warnings)


@pytest.mark.parametrize(("raw", "daily"), [(None, "interval 30"), ("", "interval 30")])
def test_an_empty_at_is_unset(helper, raw, daily):
    settings = _settings(helper, {"reboot": {"at": raw}})

    assert settings.schedule == daily
    assert settings.warnings == []


@pytest.mark.parametrize(("text", "daily"), [("12:30", "daily 12:30"), ("22:00", "daily 22:00"),
                                             ("1:30", "daily 01:30"), ("00:00", "daily 00:00")])
def test_an_unquoted_at_read_as_a_base_60_number_still_works(helper, text, daily):
    """YAML 1.1 reads `at: 12:30` as 750 and `at: 00:00` as a string."""
    cfg = yaml.safe_load(f"reboot:\n  at: {text}\n  timezone: UTC\n")

    settings = _settings(helper, cfg)

    assert settings.schedule == daily
    assert settings.warnings == []


@pytest.mark.parametrize("zone", ["Not/AZone", "../etc/passwd", "Europe", 5])
def test_a_zone_that_cannot_be_loaded_falls_back_to_utc_with_a_warning(helper, zone):
    settings = _settings(helper, {"reboot": {"at": "03:00", "timezone": zone}})

    assert settings.schedule == "daily 03:00"
    assert any("reboot.timezone=" in w and "using UTC" in w for w in settings.warnings)


def test_without_zoneinfo_the_zone_falls_back_to_utc(helper, monkeypatch):
    """A host Python without zoneinfo (or without tz data) must not crash."""
    import builtins

    real_import = builtins.__import__

    def no_zoneinfo(name, *args, **kwargs):
        if name == "zoneinfo":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_zoneinfo)

    settings = _settings(helper, {"reboot": {"at": "03:00", "timezone": ZONE}})

    assert settings.schedule == "daily 03:00"
    assert any("using UTC" in w for w in settings.warnings)


def test_utc_needs_no_time_zone_database(helper, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_zoneinfo(name, *args, **kwargs):
        if name == "zoneinfo":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_zoneinfo)

    settings = _settings(helper, {"reboot": {"at": "03:00", "timezone": "UTC"}})

    assert settings.schedule == "daily 03:00"
    assert settings.warnings == []


@pytest.mark.parametrize(
    ("now", "utc"),
    [
        # Midnight in Bucharest is 22:00 UTC in winter (UTC+2) ...
        (WINTER_NOW, "22:00"),
        # ... and 21:00 UTC in summer (UTC+3).
        (SUMMER_NOW, "21:00"),
    ],
)
def test_midnight_in_bucharest_is_converted_with_the_offset_of_the_day(helper, now, utc):
    settings = _settings(helper, {"reboot": {"at": "00:00", "timezone": ZONE}}, now=now)

    assert settings.schedule == f"daily {utc}"


def _next(helper, hour, minute, zone, now):
    from zoneinfo import ZoneInfo

    return helper.next_daily_utc(hour, minute, ZoneInfo(zone), now)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Before today's occurrence: today.
        (datetime.datetime(2030, 1, 15, 21, 59, tzinfo=UTC),
         datetime.datetime(2030, 1, 15, 22, 0, tzinfo=UTC)),
        # Exactly at it, as when the reboot it caused boots again: tomorrow.
        (datetime.datetime(2030, 1, 15, 22, 0, tzinfo=UTC),
         datetime.datetime(2030, 1, 16, 22, 0, tzinfo=UTC)),
        (datetime.datetime(2030, 1, 15, 22, 3, tzinfo=UTC),
         datetime.datetime(2030, 1, 16, 22, 0, tzinfo=UTC)),
        # Summer.
        (datetime.datetime(2030, 7, 15, 21, 3, tzinfo=UTC),
         datetime.datetime(2030, 7, 16, 21, 0, tzinfo=UTC)),
        # The boot after the last midnight before the spring change (Sunday
        # 2030-03-31, 03:00 local): the next midnight is already summer time.
        (datetime.datetime(2030, 3, 30, 22, 3, tzinfo=UTC),
         datetime.datetime(2030, 3, 31, 21, 0, tzinfo=UTC)),
        # The boot after the last summer midnight before the autumn change
        # (Sunday 2030-10-27, 04:00 local): the next midnight is winter time.
        (datetime.datetime(2030, 10, 26, 21, 3, tzinfo=UTC),
         datetime.datetime(2030, 10, 27, 22, 0, tzinfo=UTC)),
    ],
)
def test_the_next_occurrence_is_computed_in_the_zone(helper, now, expected):
    assert _next(helper, 0, 0, ZONE, now) == expected


def test_each_boot_after_a_daily_reboot_lands_on_the_next_local_midnight(helper):
    """Simulate a year of daily reboots: every registration made by the boot
    the previous one caused fires at local midnight, across both DST changes."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(ZONE)
    boot = datetime.datetime(2030, 1, 1, 0, 0, tzinfo=UTC)
    for _ in range(366):
        fire = helper.next_daily_utc(0, 0, zone, boot)
        local = fire.astimezone(zone)
        assert (local.hour, local.minute) == (0, 0), fire
        # The reboot and the next start.bat take a few minutes.
        boot = fire + datetime.timedelta(minutes=4)


def _run_subcommand(helper, tmp_path, monkeypatch, capsys, cfg):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "reboot_schedule"])

    helper.main()

    return capsys.readouterr()


@pytest.mark.parametrize(
    ("cfg", "stdout"),
    [
        ({}, "interval 30\n"),
        ({"reboot": {"enabled": False}}, "off\n"),
        ({"reboot": {"interval": 0}}, "off\n"),
        ({"reboot": {"interval": 90}}, "interval 90\n"),
        ({"reboot": {"at": "04:15", "timezone": "UTC"}}, "daily 04:15\n"),
        ({"reboot_interval": 45}, "interval 45\n"),
    ],
)
def test_reboot_schedule_prints_exactly_one_line(
    helper, tmp_path, monkeypatch, capsys, cfg, stdout
):
    out = _run_subcommand(helper, tmp_path, monkeypatch, capsys, cfg).out

    assert out == stdout
    assert re.fullmatch(r"(off|interval [0-9]+|daily [0-2][0-9]:[0-5][0-9])\n", out)


def test_reboot_schedule_warnings_go_to_stderr_and_are_safe_for_cmd(
    helper, tmp_path, monkeypatch, capsys
):
    cfg = {"reboot": {"at": "25:00\"&!%<>|^", "interval": 20}, "reboot_interval": 5}

    captured = _run_subcommand(helper, tmp_path, monkeypatch, capsys, cfg)

    assert captured.out == "interval 20\n"
    lines = captured.err.splitlines()
    assert len(lines) == 2
    for line in lines:
        assert line.startswith("WARN: reboot config: ")
        assert not set(line) & set("\"&!%<>|^")


def test_the_reboot_interval_subcommand_is_gone(helper, tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(helper, "CONFIG_PATH", str(config))
    monkeypatch.setattr("sys.argv", ["config_helper.py", "reboot_interval"])

    with pytest.raises(SystemExit):
        helper.main()


def _start_bat():
    return START_BAT.read_bytes().decode("ascii").replace("\r\n", "\n")


def test_start_bat_reads_the_schedule_from_reboot_schedule():
    text = _start_bat()

    assert "config_helper.py\" reboot_schedule >" in text
    assert "reboot_interval >" not in text
    assert 'set "REBOOT_SCHEDULE=interval 30"' in text


def test_start_bat_registers_each_kind_of_schedule():
    text = _start_bat()

    assert 'if "!REBOOT_KIND!"=="off" (' in text
    assert 'schtasks /delete /tn "MT5AutoReboot" /f' in text
    assert ') else if "!REBOOT_KIND!"=="daily" (' in text
    assert "/sc daily /st !REBOOT_ARG! " in text
    assert "/sc minute /mo !REBOOT_ARG! " in text
    assert "daily at !REBOOT_ARG! UTC" in text
    assert "every !REBOOT_ARG! min" in text
    # Every schedule still goes through reboot.bat, never shutdown directly.
    assert text.count("/tr \"cmd.exe /c %SCRIPTS%\\reboot.bat scheduled\"") == 2


def test_start_bat_logs_the_helper_warnings_without_reparsing_them():
    text = _start_bat()
    loop = text[text.index('for /f "usebackq delims=" %%W in ("%RS_ERR%") do ('):]
    loop = loop[:loop.index("\n)\n")]

    assert '>> "%START_LOG%"' in loop
    assert '>> "%FULL_LOG%"' in loop
    assert "call :log" not in loop


def test_the_example_config_uses_the_block_without_warnings(helper):
    example = yaml.safe_load((_ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8"))

    settings = _settings(helper, example)

    assert settings.schedule == "interval 30"
    assert settings.max_postpone == 360
    assert settings.warnings == []
    assert not any(key in example for key in helper.LEGACY_REBOOT_KEYS)
