"""scripts/start.bat runs the operator's config\\setup.bat on every boot.

run.sh has always copied config/setup.bat into the VM's shared config directory
and the docs said it ran before MT5 starts, but nothing called it. Nothing in CI
boots Windows, so these read start.bat as text and pin the three things that
matter: where in the boot the hook sits, that it only runs when the file exists,
and that a failing setup.bat is logged without ever stopping the boot.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
START_BAT = _ROOT / "scripts" / "start.bat"
RUN_SH = _ROOT / "run.sh"

_HOOK_OPEN = 'if exist "%CONFIG%\\setup.bat" ('
_HOOK_CALL = 'call "%CONFIG%\\setup.bat"'


def _start_bat():
    return START_BAT.read_bytes().decode("ascii").replace("\r\n", "\n")


def _hook_block(text):
    """The lines inside the `if exist ... setup.bat (` block, up to its closing
    paren (the first line that is exactly `)`, since nested ones are indented)."""
    lines = text.split("\n")
    opens = [index for index, line in enumerate(lines) if line == _HOOK_OPEN]
    assert len(opens) == 1, f"expected one setup.bat hook in start.bat, found {len(opens)}"
    block = []
    for line in lines[opens[0] + 1:]:
        if line == ")":
            return block
        block.append(line)
    raise AssertionError("the setup.bat hook block never closes")


def test_the_setup_hook_runs_after_install_and_pip_and_before_terminals_launch():
    text = _start_bat()

    install = text.index('call "%SCRIPTS%\\install.bat"')
    pip = text.index('-m pip install "pyyaml==')
    lingering = text.index("Killing lingering MT5 terminals")
    hook = text.index(_HOOK_OPEN)
    launch_log = text.index('"Launching MT5 terminals..."')
    launch = text.index("call :launch_terminal")
    api = text.index("call :launch_api_bg")

    assert install < pip < lingering < hook < launch_log < launch < api


def test_the_setup_hook_runs_only_when_the_file_exists():
    text = _start_bat()

    assert text.count(_HOOK_CALL) == 1
    assert any(line.strip().startswith(_HOOK_CALL) for line in _hook_block(text))


def test_the_setup_hook_logs_its_start_and_finish_to_the_start_log():
    block = _hook_block(_start_bat())
    call_line = next(line for line in block if line.strip().startswith(_HOOK_CALL))

    # Its own output lands in the log boot already writes, not on a console
    # nobody is watching.
    assert call_line.strip().endswith('>> "%START_LOG%" 2>&1')
    logged = [line for line in block if 'call :log "%START_LOG%"' in line]
    assert any("Running config\\setup.bat" in line for line in logged)
    assert any("config\\setup.bat finished" in line for line in logged)


def test_a_failing_setup_hook_is_logged_and_never_stops_the_boot():
    steps = [line.strip() for line in _hook_block(_start_bat())]

    # The errorlevel is captured straight after the call, before anything else
    # can overwrite it.
    call = next(i for i, step in enumerate(steps) if step.startswith(_HOOK_CALL))
    assert steps[call + 1] == 'set "SETUP_EC=!errorlevel!"'
    warn = [step for step in steps if "WARN" in step]
    assert len(warn) == 1
    assert "!SETUP_EC!" in warn[0]
    assert "continuing boot" in warn[0]
    # How start.bat gives up elsewhere: exit, goto, and the lock release that
    # precedes each of its early exits.
    for step in steps:
        assert not step.lower().startswith(("exit", "goto")), step
        assert "release_lock" not in step, step


def test_the_setup_hook_reads_the_directory_run_sh_copies_it_into():
    text = _start_bat()
    run_sh = RUN_SH.read_text(encoding="utf-8")

    assert "set CONFIG=%SHARED%\\config\n" in text
    assert '"${DIR}/config/setup.bat"' in run_sh
    assert 'cp -a "$f" "${DIR}/data/shared/config/"' in run_sh


def test_start_bat_stays_ascii_with_consistent_crlf_endings():
    """cmd.exe misreads labels in a batch file that mixes LF and CRLF, and
    start.bat is full of them. ASCII is also what `make lint` gates."""
    raw = START_BAT.read_bytes()

    raw.decode("ascii")
    assert raw.count(b"\n") == raw.count(b"\r\n")
