"""WebRequest allowlist manager for a single terminal.

MT5's WebRequest allowed-URL list lives ONLY in ``<terminal>/Config/common.ini``
(``[Experts] WebRequest=1`` + ``WebRequestUrl=<blob>``), a UTF-16LE file that MT5
reads at terminal startup. There is no live-reload, so applying a change requires a
terminal restart, and ``start.bat`` deletes ``common.ini`` on every boot — so the
authoritative desired state is kept in a sibling ``webrequest.json`` that survives
reboots and is re-applied to ``common.ini`` at two points:

  * boot     — ``scripts/config_helper.py`` write_ini, right after start.bat deletes
               common.ini and before the terminal launches;
  * runtime  — ``mt5client.restart_terminal``, after the terminal is killed and
               before it is relaunched (MT5 rewrites common.ini on exit, so the
               write must happen while the terminal is down).

First adoption migrates from whatever the terminal currently has (decode the
existing ``WebRequestUrl=`` blob) so manually-configured URLs are preserved.

The blob codec (fully reverse-engineered) lives in
``scripts/webrequest_allowlist_codec.py`` and is loaded here by path so it stays a
single dependency-free source of truth, importable from both the app and the boot
helper.
"""
from __future__ import annotations

import importlib.util
import json
import os

DESIRED_FILENAME = "webrequest.json"
_BOM = b"\xff\xfe"

# --- load the standalone codec by path (no package coupling) ---
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CODEC_PATH = os.path.join(_REPO_ROOT, "scripts", "webrequest_allowlist_codec.py")
_spec = importlib.util.spec_from_file_location("webrequest_allowlist_codec", _CODEC_PATH)
_codec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_codec)
encode_urls = _codec.encode_urls
decode_blob = _codec.decode_blob


# ── paths ────────────────────────────────────────────────────────────
def config_dir(terminal_dir: str) -> str:
    return os.path.join(terminal_dir, "Config")


def common_ini_path(cfg_dir: str) -> str:
    return os.path.join(cfg_dir, "common.ini")


def desired_path(cfg_dir: str) -> str:
    return os.path.join(cfg_dir, DESIRED_FILENAME)


# ── url hygiene ──────────────────────────────────────────────────────
def clean_urls(urls) -> list[str]:
    """Trim, drop blanks/dupes, keep only http(s) URLs without the ';' delimiter."""
    out: list[str] = []
    if not isinstance(urls, (list, tuple)):
        return out
    for u in urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u or ";" in u or any(ord(c) < 0x20 for c in u):
            continue
        low = u.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            continue
        if u not in out:
            out.append(u)
    return out


# ── desired-state store (survives reboots) ───────────────────────────
def load_desired(cfg_dir: str) -> list[str] | None:
    """Return the persisted URL list, or None if this terminal has none set."""
    path = desired_path(cfg_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    urls = data.get("urls") if isinstance(data, dict) else data
    return clean_urls(urls) if isinstance(urls, list) else None


def save_desired(cfg_dir: str, urls: list[str]) -> None:
    os.makedirs(cfg_dir, exist_ok=True)
    path = desired_path(cfg_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"urls": clean_urls(urls)}, f, indent=2)
    os.replace(tmp, path)


# ── common.ini (UTF-16LE + BOM + CRLF) ───────────────────────────────
def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    text = raw.decode("utf-16", errors="ignore")  # strips BOM
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _write_lines(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = "\r\n".join(lines)
    data = _BOM + text.encode("utf-16-le")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def read_current_urls(cfg_dir: str) -> list[str]:
    """Decode the URL list from an existing common.ini (migrate-from-current)."""
    blob = ""
    in_experts = False
    for line in _read_lines(common_ini_path(cfg_dir)):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_experts = s.lower() == "[experts]"
            continue
        if in_experts and "=" in s and s.split("=", 1)[0].strip().lower() == "webrequesturl":
            blob = s.split("=", 1)[1].strip()
            break
    if not blob:
        return []
    try:
        return clean_urls(decode_blob(blob))
    except Exception:
        return []


def effective_urls(cfg_dir: str) -> list[str]:
    """Desired state if set, else whatever the terminal currently holds."""
    desired = load_desired(cfg_dir)
    return desired if desired is not None else read_current_urls(cfg_dir)


def write_common_ini(cfg_dir: str, urls: list[str]) -> None:
    """Set ``[Experts] WebRequest=1`` + ``WebRequestUrl=<blob>`` in common.ini,
    preserving every other line/section. Creates the file (and [Experts]) if
    absent. Encoding stays UTF-16LE+BOM+CRLF, as MT5 writes it."""
    urls = clean_urls(urls)
    kv = {"WebRequest": "1", "WebRequestUrl": encode_urls(urls)}
    lines = _read_lines(common_ini_path(cfg_dir))
    out: list[str] = []
    in_experts = False
    written: set[str] = set()
    has_experts = any(l.strip().lower() == "[experts]" for l in lines)

    def flush_missing():
        for key, val in kv.items():
            if key not in written:
                out.append(f"{key}={val}")
                written.add(key)

    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_experts:
                flush_missing()
            in_experts = s.lower() == "[experts]"
            out.append(line)
            continue
        if in_experts and "=" in s:
            key = s.split("=", 1)[0].strip()
            for real in kv:
                if key.lower() == real.lower():
                    if real not in written:
                        out.append(f"{real}={kv[real]}")
                        written.add(real)
                    break
            else:
                out.append(line)
            continue
        out.append(line)

    if in_experts:
        flush_missing()
    if not has_experts:
        out.append("[Experts]")
        flush_missing()
    # drop trailing blank lines then keep exactly one terminator via join
    while out and out[-1] == "":
        out.pop()
    _write_lines(common_ini_path(cfg_dir), out)


def apply_from_desired(terminal_dir: str) -> int | None:
    """Regenerate common.ini from the desired file. Returns URL count, or None
    if there is no desired state (in which case common.ini is left untouched).
    Safe to call while the terminal is stopped."""
    cfg_dir = config_dir(terminal_dir)
    desired = load_desired(cfg_dir)
    if desired is None:
        return None
    write_common_ini(cfg_dir, desired)
    return len(desired)
