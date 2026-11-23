"""The MT5Start logon task must fire on every boot.

install.bat created it with `schtasks /create /sc onlogon`, which gives the
logon trigger a StartBoundary of "now" in the guest's local time, refuses to
start on batteries and stops the task after 72 hours. A guest whose clock ran
hours fast during the first install got a boundary in its own future once the
clock corrected itself; a logon trigger never fires before its boundary, so
every reboot came up without start.bat, the VM stayed unhealthy and vm-watchdog
recreated it into the same state. scripts/ensure_start_task.ps1 re-registers the
task without those defaults.

Nothing in CI runs Windows or Task Scheduler, so these read the scripts as text
and pin what the fix depends on. The behaviour itself was checked on a live
guest: a task registered with a StartBoundary years ahead was re-registered
with an empty <LogonTrigger />, a second run reported it ok, and the start.bat
instance the task had launched kept running.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
ENSURE = _ROOT / "scripts" / "ensure_start_task.ps1"
INSTALL_BAT = _ROOT / "scripts" / "install.bat"
START_BAT = _ROOT / "scripts" / "start.bat"
RUN_SH = _ROOT / "run.sh"

_CALL = 'ensure_start_task.ps1" -StartBat "%SCRIPTS%\\start.bat"'


def _text(path):
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _code(path):
    """The script without its comment block and line comments."""
    text = re.sub(r"<#.*?#>", "", _text(path), flags=re.S)
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("#"))


def test_the_logon_trigger_is_registered_without_a_start_boundary():
    code = _code(ENSURE)

    assert "New-ScheduledTaskTrigger -AtLogOn" in code
    # -At would make a time-based trigger, which carries a StartBoundary.
    assert not re.search(r"New-ScheduledTaskTrigger\b[^\n]*\s-At\s", code)
    assert "StartBoundary =" not in code


def test_the_task_starts_on_batteries_and_has_no_time_limit():
    code = _code(ENSURE)

    assert "-AllowStartIfOnBatteries" in code
    assert "-DontStopIfGoingOnBatteries" in code
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in code


def test_the_task_runs_start_bat_elevated_as_the_docker_user():
    code = _code(ENSURE)

    assert "$UserName = 'Docker'" in code
    assert "-RunLevel Highest" in code
    assert "('/c \"{0}\"' -f $StartBat)" in code


def test_a_correct_task_is_left_alone_and_a_fix_is_checked_again():
    code = _code(ENSURE)

    check = code.index("$problem = Get-TaskProblem")
    early_exit = code.index("exit 0", check)
    register = code.index("Register-ScheduledTask")
    recheck = code.index("$after = Get-TaskProblem")

    assert check < early_exit < register < recheck
    assert "exit 1" in code[recheck:]


def test_the_check_finds_every_default_that_stops_a_boot():
    code = _code(ENSURE)

    for needle in (
        "GetElementsByTagName('LogonTrigger')",
        "GetElementsByTagName('StartBoundary')",
        "DisallowStartIfOnBatteries -ne 'false'",
        "StopIfGoingOnBatteries -ne 'false'",
        "ExecutionTimeLimit -ne 'PT0S'",
    ):
        assert needle in code, needle


def test_install_bat_fixes_the_task_right_after_creating_it():
    text = _text(INSTALL_BAT)

    create = text.index('schtasks /create /tn "MT5Start"')
    ensure = text.index("call :ensure_start_task", create)
    reboot = text.index("call :do_reboot", create)

    assert create < ensure < reboot
    assert text.count(_CALL) == 1


def test_a_failed_fix_in_install_bat_only_warns():
    text = _text(INSTALL_BAT)
    label = text.index("\n:ensure_start_task\n")
    body = text[label:text.index("\n:log\n", label)]

    assert "WARN ensure_start_task.ps1 failed" in body
    assert "exit /b 0" in body
    assert "exit /b 1" not in body


def test_start_bat_fixes_the_task_on_every_boot_after_install():
    text = _text(START_BAT)

    install = text.index('call "%SCRIPTS%\\install.bat"')
    install_done = text.index('"install.bat done."')
    ensure = text.index(_CALL)
    launch = text.index("call :launch_terminal")

    # Before install.bat would make stage 1 think the first boot already ran.
    assert install < install_done < ensure < launch
    assert text.count(_CALL) == 1


def test_run_sh_copies_the_script_into_the_vm():
    assert (
        'cp "${DIR}/scripts/ensure_start_task.ps1" "${DIR}/data/shared/scripts/ensure_start_task.ps1"'
        in _text(RUN_SH)
    )


def test_the_script_is_ascii_for_windows_powershell():
    ENSURE.read_bytes().decode("ascii")
