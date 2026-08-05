"""Parse MT5 .set parameter files into structured inputs.

The mirror of backtest/set_builder.py (JSON -> .set); this goes .set ->
structured list. MT5 saves .set as UTF-16-LE with BOM; hand-written ones
are often UTF-8 or ASCII. Grammar per line:

    Name=value                          plain input
    Name=value||start||step||stop||Y    input with optimization metadata
    ; comment

The optimization tail is irrelevant for live deployment — the leading
value is what the terminal applies — but we keep it so clients can
round-trip and diff files losslessly.
"""


def _decode(data: bytes) -> str:
    # Never blind-try utf-16: the codec "succeeds" on any even-length
    # ASCII input by pairing bytes into CJK garbage, which then parses to
    # zero inputs — and the deployment silently runs on EA defaults.
    # Decide by BOM (MT5 exports carry one), then by embedded NULs
    # (BOM-less UTF-16), then plain 8-bit.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    if b"\x00" in data:
        try:
            return data.decode("utf-16-le")
        except (UnicodeDecodeError, UnicodeError):
            pass
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def parse_set_bytes(data: bytes) -> list[dict]:
    return parse_set_text(_decode(data))


def parse_set_text(text: str) -> list[dict]:
    """Return [{name, value, optimize?, start?, step?, stop?}, ...]."""
    inputs: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, rhs = line.partition("=")
        name = name.strip()
        if not name:
            continue
        parts = rhs.split("||")
        entry: dict = {"name": name, "value": parts[0].strip()}
        if len(parts) >= 5:
            entry["start"] = parts[1].strip()
            entry["step"] = parts[2].strip()
            entry["stop"] = parts[3].strip()
            entry["optimize"] = parts[4].strip().upper() == "Y"
        inputs.append(entry)
    return inputs
