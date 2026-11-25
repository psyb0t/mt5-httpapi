"""Every .bat and .cmd the Windows VM runs must use CRLF line endings.

psyb0t (2026-11-25): scripts/install.bat had LF line endings. On a fresh
install cmd.exe mis-parsed its labels, jumped into the installer wait loop with
an empty broker name and waited out its 5-minute timeout before carrying on.
start.bat and api_runner.bat were CRLF; the rest were not. .gitattributes now
stores these files with `-text`, so their bytes are never converted, and this
test plus `make lint` refuse an LF-only line.
"""

import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _batch_files():
    try:
        res = subprocess.run(
            ["git", "ls-files", "*.bat", "*.cmd"], cwd=_REPO, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        res = None
    if res is not None and res.returncode == 0 and res.stdout.strip():
        return [_REPO / name for name in res.stdout.split()]
    # The offline test image has no git and no .git; fall back to the tree.
    return sorted(p for ext in ("*.bat", "*.cmd") for p in _REPO.rglob(ext) if ".git" not in p.parts)


BATCH_FILES = _batch_files()


def test_there_are_batch_files_to_check():
    assert any(p.name == "install.bat" for p in BATCH_FILES)


@pytest.mark.parametrize("path", BATCH_FILES, ids=lambda p: str(p.relative_to(_REPO)))
def test_every_line_ends_in_crlf(path):
    data = path.read_bytes()
    assert data.endswith(b"\r\n"), f"{path.name} must end with CRLF"
    lf_only = [n for n, line in enumerate(data.split(b"\n")[:-1], 1) if not line.endswith(b"\r")]
    assert not lf_only, f"{path.name} has LF-only lines: {lf_only[:5]}"


def test_gitattributes_keeps_batch_bytes_unconverted():
    attributes = _REPO / ".gitattributes"
    if not attributes.exists():
        pytest.skip(".gitattributes is not copied into the offline test image; the byte checks above still run")
    text = attributes.read_text(encoding="utf-8")
    for pattern in ("*.bat", "*.cmd"):
        line = next(ln for ln in text.splitlines() if ln.startswith(pattern + " "))
        assert "-text" in line.split(), f"{pattern} must be stored with -text"
