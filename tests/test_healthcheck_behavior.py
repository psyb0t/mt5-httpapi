"""Behavioral coverage for the awk port-filter embedded in scripts/healthcheck.sh.

The previous coverage in tests/test_terminal_instances.py only asserted that
certain SOURCE SUBSTRINGS (e.g. `'if (!have_group) { print port; return }'`)
appeared in healthcheck.sh. That kind of assertion passes even when the awk
program is present but wrong, and breaks on a harmless whitespace reformat —
it proves the text exists, not that the filter behaves correctly. These tests
extract the actual awk program and RUN it against fixture config.yaml files,
asserting on the emitted port list.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

HEALTHCHECK_PATH = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.sh"

# These exact strings bound the awk program inside healthcheck.sh (see the
# module docstring there). If either marker goes missing the script's shape
# changed enough that this whole suite needs a look, hence the assert below
# instead of a silent empty-program run.
_AWK_START_MARKER = 'PORTS=$(awk -v groupfile="$VM_GROUP" \''
_AWK_END_MARKER = '\' "$CONFIG")'

pytestmark = pytest.mark.skipif(
    shutil.which("awk") is None,
    reason="awk not available in this environment (Dockerfile.test is debian-based and ships it)",
)


def _extract_awk_program():
    src = HEALTHCHECK_PATH.read_text(encoding="utf-8")
    _before, sep, rest = src.partition(_AWK_START_MARKER)
    assert sep, "awk program start marker not found in healthcheck.sh"
    program, sep2, _after = rest.partition(_AWK_END_MARKER)
    assert sep2, "awk program end marker not found in healthcheck.sh"
    return program


@pytest.fixture
def awk_prog(tmp_path):
    """Write the awk program extracted from healthcheck.sh to a real file so it
    can be run standalone via `awk -f`, decoupled from the rest of the script.
    """
    prog_path = tmp_path / "healthcheck_ports.awk"
    prog_path.write_text(_extract_awk_program(), encoding="utf-8")
    return prog_path


def _write_config(tmp_path, terminals, name="config.yaml"):
    """Render a minimal config.yaml terminals: list matching the real file's
    indentation (two spaces then `- broker:`, four spaces for the rest) —
    the awk program's regexes anchor on exactly that shape.
    """
    lines = ["terminals:"]
    for t in terminals:
        lines.append(f"  - broker: {t['broker']}")
        lines.append(f"    account: {t['account']}")
        if t.get("instance") is not None:
            lines.append(f"    instance: {t['instance']}")
        lines.append(f"    port: {t['port']}")
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run_awk(prog_path, groupfile_arg, config_path):
    result = subprocess.run(
        ["awk", "-v", f"groupfile={groupfile_arg}", "-f", str(prog_path), str(config_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


SIX_TERMINALS = [
    {"broker": "darwinex", "account": "live", "port": 6001},
    {"broker": "darwinex", "account": "demo", "port": 6002},
    {"broker": "ictrading", "account": "live", "port": 6003},
    {"broker": "ictrading", "account": "demo", "port": 6004},
    {"broker": "fxcm", "account": "live", "port": 6005},
    {"broker": "fxcm", "account": "demo", "port": 6006},
]
ALL_SIX_PORTS = [str(t["port"]) for t in SIX_TERMINALS]


def test_empty_groupfile_arg_emits_every_port(awk_prog, tmp_path):
    """groupfile="" is what a single-VM install passes (VM_GROUP unset/blank
    resolves the same way as a literal empty string). No group means no filter.
    """
    config = _write_config(tmp_path, SIX_TERMINALS)

    ports = _run_awk(awk_prog, "", config)

    assert ports == ALL_SIX_PORTS


def test_group_file_selects_only_matching_broker_account(awk_prog, tmp_path):
    """A group file listing `broker account` picks exactly those terminals,
    in the order they appear in config.yaml — not group-file order.
    """
    config = _write_config(tmp_path, SIX_TERMINALS)
    groupfile = tmp_path / "vm-group.txt"
    groupfile.write_text("darwinex demo\nfxcm live\n", encoding="utf-8")

    ports = _run_awk(awk_prog, str(groupfile), config)

    assert ports == ["6002", "6005"]


def test_group_file_that_exists_but_is_empty_falls_back_to_every_port(awk_prog, tmp_path):
    """An existing-but-empty group file must NOT mean "select nothing" — that
    is the exact trap the PR comment documents: an empty group file resulted
    in have_group staying unset (no valid `broker account` line was ever
    parsed), so the filter degrades to no-filter, same as no group file.
    """
    config = _write_config(tmp_path, SIX_TERMINALS)
    groupfile = tmp_path / "vm-group.txt"
    groupfile.write_text("", encoding="utf-8")

    ports = _run_awk(awk_prog, str(groupfile), config)

    assert ports == ALL_SIX_PORTS


def test_missing_groupfile_path_falls_back_to_every_port(awk_prog, tmp_path):
    """A groupfile path that does not exist on disk (e.g. single-VM install
    where the bind mount was never created) must behave like no filter, not
    zero ports.
    """
    config = _write_config(tmp_path, SIX_TERMINALS)
    groupfile = tmp_path / "does-not-exist.txt"

    ports = _run_awk(awk_prog, str(groupfile), config)

    assert ports == ALL_SIX_PORTS


def test_group_file_with_instance_selects_only_matching_instance(awk_prog, tmp_path):
    """Two terminals share broker+account and are distinguished only by
    instance. A `broker account instance` group line must select just the
    one whose instance matches.
    """
    terminals = [
        {"broker": "darwinex", "account": "live", "instance": "a", "port": 7001},
        {"broker": "darwinex", "account": "live", "instance": "b", "port": 7002},
    ]
    config = _write_config(tmp_path, terminals)
    groupfile = tmp_path / "vm-group.txt"
    groupfile.write_text("darwinex live a\n", encoding="utf-8")

    ports = _run_awk(awk_prog, str(groupfile), config)

    assert ports == ["7001"]


def test_group_line_without_instance_selects_only_default_instance_terminal(awk_prog, tmp_path):
    """A group line with no instance field (`broker account`) must map to the
    literal key "default" and select only the terminal that also has no
    instance set — not a sibling terminal that has an explicit instance.
    """
    terminals = [
        {"broker": "darwinex", "account": "live", "port": 8001},
        {"broker": "darwinex", "account": "live", "instance": "a", "port": 8002},
    ]
    config = _write_config(tmp_path, terminals)
    groupfile = tmp_path / "vm-group.txt"
    groupfile.write_text("darwinex live\n", encoding="utf-8")

    ports = _run_awk(awk_prog, str(groupfile), config)

    assert ports == ["8001"]


def test_group_file_ignores_comments_and_blank_lines(awk_prog, tmp_path):
    """Comment lines and blank lines in the group file must not be
    misinterpreted as broker/account entries or otherwise disturb the
    surrounding real entries.
    """
    config = _write_config(tmp_path, SIX_TERMINALS)
    groupfile = tmp_path / "vm-group.txt"
    groupfile.write_text(
        "# comment\n\ndarwinex demo\n# another comment\n\nfxcm live\n",
        encoding="utf-8",
    )

    ports = _run_awk(awk_prog, str(groupfile), config)

    assert ports == ["6002", "6005"]
