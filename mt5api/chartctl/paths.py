"""Directory layout and filename safety for chartctl.

All chartctl writes are confined to four roots under TERMINAL_DIR:

  MQL5/Experts/Uploaded/     staged .ex5 (shared with the backtest feature)
  chartctl/sets/             staged .set parameter files
  chartctl/registry.json     API-side desired-state registry
  MQL5/Files/chartctl/       the EA-visible protocol directory
  templates/chartctl/        generated per-deployment .tpl files

Every externally supplied filename passes safe_name() — same contract as
the backtest handler's _safe_basename, factored here so both artifact
endpoints and the tpl builder share one guard with one test matrix.
"""
import os
import re

from mt5api.config import TERMINAL_DIR, ASSETS_DIR

EXPERTS_DIR = os.path.join(TERMINAL_DIR, "MQL5", "Experts", "Uploaded")
SETS_DIR = os.path.join(TERMINAL_DIR, "chartctl", "sets")
REGISTRY_PATH = os.path.join(TERMINAL_DIR, "chartctl", "registry.json")
PROTOCOL_DIR = os.path.join(TERMINAL_DIR, "MQL5", "Files", "chartctl")
TEMPLATES_DIR = os.path.join(TERMINAL_DIR, "templates", "chartctl")
SCREENSHOTS_DIR = os.path.join(PROTOCOL_DIR, "shots")

HOST_EXPERTS_DIR = os.path.join(ASSETS_DIR, "experts")
HOST_SETS_DIR = os.path.join(ASSETS_DIR, "sets")

DESIRED_PATH = os.path.join(PROTOCOL_DIR, "desired.json")
OBSERVED_PATH = os.path.join(PROTOCOL_DIR, "observed.json")
COMMAND_PATH = os.path.join(PROTOCOL_DIR, "command.json")
COMMAND_RESULT_PATH = os.path.join(PROTOCOL_DIR, "command_result.json")

# Windows-reserved characters plus anything that could smuggle a path.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def ensure_dirs() -> None:
    for d in (EXPERTS_DIR, SETS_DIR, PROTOCOL_DIR, TEMPLATES_DIR,
              SCREENSHOTS_DIR, os.path.dirname(REGISTRY_PATH)):
        os.makedirs(d, exist_ok=True)


def safe_name(name: str, field: str, required_ext: str | None = None) -> str:
    """Validate an externally supplied filename. Returns the bare name.

    Rejects: empty, path separators, traversal, drive prefixes, UNC,
    Windows-reserved characters, hidden dotfiles, and (optionally) a
    wrong extension. Raises ValueError with a client-facing message.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError(f"{field}: filename is required")
    if name != os.path.basename(name) or name in (".", ".."):
        raise ValueError(f"{field}: path components are not allowed")
    if _BAD_CHARS.search(name):
        raise ValueError(f"{field}: illegal characters in filename")
    if name.startswith("."):
        raise ValueError(f"{field}: hidden files are not allowed")
    if ".." in name:
        raise ValueError(f"{field}: traversal sequences are not allowed")
    if required_ext and not name.lower().endswith(required_ext):
        raise ValueError(f"{field}: must end in {required_ext}")
    return name


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding=encoding, newline="\r\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def atomic_write_bytes(path: str, data: bytes) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)
