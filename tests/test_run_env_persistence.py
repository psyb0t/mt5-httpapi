"""run.sh must persist MT5_PROJECT_DIR to .env, not only export it.

psyb0t (2026-09-04): run.sh exported MT5_PROJECT_DIR to its own shell and
truncated/rebuilt .env without writing it, so `make down`, `make logs`, a
manual `docker compose` and - through recreate-vm.sh - the watchdog's own
recovery all failed at `${MT5_PROJECT_DIR:?}` interpolation once run.sh had
exited.

These run the ACTUAL .env-generation block lifted out of run.sh (bounded by
two comment lines that already exist there) under bash with a stub
config_helper, and assert on the .env it writes. A source-substring assertion
would pass with the line in the wrong place or behind a failing command; this
does not.
"""

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
RUN_SH = _REPO / "run.sh"

# The block starts at the truncation and ends where nginx generation begins.
_START = "# Generate fresh .env each run."
_END = "# Generate nginx.conf from config.yaml terminals."


def _env_block():
    src = RUN_SH.read_text(encoding="utf-8")
    _before, sep, rest = src.partition(_START)
    assert sep, "start marker missing from run.sh - the .env block moved"
    block, sep2, _after = rest.partition(_END)
    assert sep2, "end marker missing from run.sh - the .env block moved"
    return block


def _run_env_block(
    tmp_path,
    *,
    token="tok-123",
    ts_key="",
    ts_server="",
    fail_on=None,
    profiles="",
    telegram=None,
):
    """Execute the block with DIR=tmp_path and a config_helper stub on PATH.

    `fail_on` names a config_helper subcommand the stub should fail on, to show
    what .env looks like when the script dies partway through.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "python3"
    stub.write_text(
        "#!/bin/sh\n"
        f"[ \"$2\" = \"{fail_on or ''}\" ] && [ -n \"{fail_on or ''}\" ] && exit 1\n"
        'case "$2" in\n'
        f"  api_token) printf '%s' '{token}' ;;\n"
        f"  ts_auth_key) printf '%s' '{ts_key}' ;;\n"
        f"  ts_login_server) printf '%s' '{ts_server}' ;;\n"
        f"  compose_profiles) printf '%s' '{profiles}' ;;\n"
        '  telegram_setting) case "$3" in\n'
        + "".join(
            f"    {key}) printf '%s' '{value}' ;;\n"
            for key, value in (telegram or {}).items()
        )
        + "    *) ;;\n  esac ;;\n"
        '  *) echo "stub config_helper: unexpected $*" >&2; exit 1 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    script = (
        "set -eo pipefail\n"
        f'DIR="{tmp_path}"\n'
        'CFG="${DIR}/scripts/config_helper.py"\n'
        + _env_block()
    )
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        "MT5_PROJECT_DIR": str(tmp_path),
    }
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60, check=False,
    )


def _env_lines(tmp_path):
    return (tmp_path / ".env").read_text(encoding="utf-8").splitlines()


def _project_dir_line(path):
    # Single-quoted on purpose: unquoted, `$` or ` #` in a path is mangled by
    # compose's dotenv parser.
    return f"MT5_PROJECT_DIR='{path}'"


def test_env_carries_the_project_dir_first(tmp_path):
    res = _run_env_block(tmp_path)
    assert res.returncode == 0, res.stderr
    lines = _env_lines(tmp_path)
    assert lines[0] == _project_dir_line(tmp_path)
    assert "API_TOKEN=tok-123" in lines


def test_env_is_regenerated_not_appended_to(tmp_path):
    """The truncation is load-bearing: a stale value must not survive a re-run."""
    (tmp_path / ".env").write_text("MT5_PROJECT_DIR=/old/path\nSTALE=1\n", encoding="utf-8")
    res = _run_env_block(tmp_path)
    assert res.returncode == 0, res.stderr
    lines = _env_lines(tmp_path)
    assert lines.count(_project_dir_line(tmp_path)) == 1
    assert "MT5_PROJECT_DIR=/old/path" not in lines
    assert "STALE=1" not in lines


def test_the_project_dir_survives_a_failure_later_in_the_block(tmp_path):
    """Written before anything that can fail. If reading the API token blows
    up, .env must still make compose usable rather than be left half-built
    without the one variable every compose command needs."""
    res = _run_env_block(tmp_path, fail_on="api_token")
    assert res.returncode != 0
    assert _env_lines(tmp_path) == [_project_dir_line(tmp_path)]


def test_optional_tailscale_values_are_still_written_when_present(tmp_path):
    """The block's existing behaviour is unchanged around the new line."""
    res = _run_env_block(tmp_path, ts_key="tskey-abc", ts_server="https://hs.example")
    assert res.returncode == 0, res.stderr
    lines = _env_lines(tmp_path)
    assert lines[0] == _project_dir_line(tmp_path)
    assert "TS_AUTHKEY=tskey-abc" in lines
    assert any(line.startswith("TS_EXTRA_ARGS=") and "hs.example" in line for line in lines)


def test_no_compose_profiles_are_written_while_signals_are_off(tmp_path):
    res = _run_env_block(tmp_path)
    assert res.returncode == 0, res.stderr
    assert not any(line.startswith(("COMPOSE_PROFILES=", "TELEGRAM_")) for line in _env_lines(tmp_path))


def test_signals_profile_needs_a_signals_file(tmp_path):
    res = _run_env_block(tmp_path, profiles="signals")
    assert res.returncode != 0
    assert "config/signals.yml does not exist" in res.stdout
    assert not any(line.startswith("COMPOSE_PROFILES=") for line in _env_lines(tmp_path))


def test_telegram_profile_writes_the_telethon_settings_quoted(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "signals.yml").write_text("sources: {}\n", encoding="utf-8")
    res = _run_env_block(
        tmp_path,
        profiles="signals,telegram",
        telegram={"api_id": "123456", "api_hash": "fakehash", "session": "1Afake=", "auth_key": "k"},
    )
    assert res.returncode == 0, res.stderr
    lines = _env_lines(tmp_path)
    assert "COMPOSE_PROFILES=signals,telegram" in lines
    assert "TELEGRAM_API_ID='123456'" in lines
    assert "TELEGRAM_API_HASH='fakehash'" in lines
    assert "TELEGRAM_SESSION='1Afake='" in lines
    assert "TELEGRAM_AUTH_KEY='k'" in lines


def test_an_empty_telegram_session_warns(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "signals.yml").write_text("sources: {}\n", encoding="utf-8")
    res = _run_env_block(tmp_path, profiles="signals,telegram", telegram={"api_id": "1"})
    assert res.returncode == 0, res.stderr
    assert "make telegram-login" in res.stdout
    assert "TELEGRAM_SESSION=''" in _env_lines(tmp_path)


# ── run.sh refuses a MT5_PROJECT_DIR that is not this checkout ───────────────
#
# Counter-review finding: `${MT5_PROJECT_DIR:-$DIR}` accepted any pre-exported
# value. With two clones and a stale export from the other one, .env would
# record the wrong path and the watchdog would recreate THIS project's VMs from
# the OTHER clone's compose file. These run a copy of the real run.sh from a
# temp dir; the check sits before anything the script creates or downloads.


def _run_copy_of_run_sh(tmp_path, project_dir_value):
    import shutil

    script = tmp_path / "run.sh"
    shutil.copy(RUN_SH, script)
    script.chmod(0o755)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "SKIP_KVM_CHECK": "1"}
    if project_dir_value is not None:
        env["MT5_PROJECT_DIR"] = project_dir_value
    return subprocess.run([str(script)], capture_output=True, text=True, env=env, timeout=60, check=False)


def test_run_sh_refuses_a_project_dir_that_is_another_checkout(tmp_path):
    other = tmp_path / "other-clone"
    other.mkdir()
    res = _run_copy_of_run_sh(tmp_path, str(other))
    assert res.returncode == 1
    assert "MT5_PROJECT_DIR is" in res.stdout + res.stderr
    # It stopped before doing any of run.sh's work in the directory.
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / ".env").exists()


def test_run_sh_accepts_the_same_checkout_through_a_symlink(tmp_path):
    """Equality is by resolved path, so a symlinked checkout is not rejected."""
    link = tmp_path.parent / (tmp_path.name + "-link")
    link.symlink_to(tmp_path, target_is_directory=True)
    try:
        res = _run_copy_of_run_sh(tmp_path, str(link))
    finally:
        link.unlink()
    # Past the guard it fails later, on the missing compose sources - the
    # point is only that the guard did not fire.
    assert "MT5_PROJECT_DIR is" not in res.stdout + res.stderr
    assert "neither vms.yaml" in res.stdout + res.stderr


def test_run_sh_derives_the_project_dir_when_unset(tmp_path):
    res = _run_copy_of_run_sh(tmp_path, None)
    assert "MT5_PROJECT_DIR is" not in res.stdout + res.stderr
    assert "neither vms.yaml" in res.stdout + res.stderr


@pytest.mark.skipif(
    subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0
    if __import__("shutil").which("docker") else True,
    reason="needs the docker compose CLI (the offline test image has none; runs on the host)",
)
def test_the_written_env_serves_compose_outside_run_sh(tmp_path):
    """The property psyb0t named: a compose command in a FRESH shell, with no
    MT5_PROJECT_DIR exported, must interpolate the shipped compose file from
    .env alone - and fail without that line, so this is testing the line."""
    import shutil

    shutil.copy(_REPO / "docker-compose.yml.example", tmp_path / "docker-compose.yml")
    assert _run_env_block(tmp_path).returncode == 0

    def compose_config(env_extra=None):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
        env.update(env_extra or {})
        return subprocess.run(
            ["docker", "compose", "-f", str(tmp_path / "docker-compose.yml"), "config", "--quiet"],
            capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120, check=False,
        )

    ok = compose_config()
    assert ok.returncode == 0, ok.stderr

    (tmp_path / ".env").write_text("API_TOKEN=tok-123\n", encoding="utf-8")
    broken = compose_config()
    assert broken.returncode != 0
    assert "MT5_PROJECT_DIR" in broken.stderr
