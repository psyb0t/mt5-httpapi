"""Generate per-deployment MT5 chart templates (.tpl).

A minimal template whose <expert> block carries the expert path, flags,
and the full <inputs> list translated from a parsed .set file. Applying
the template to a chart attaches the expert with those inputs — the only
programmatic attach path MT5 offers (via ChartApplyTemplate from MQL5).

Attribution: the loader sets a chart comment `chartctl:<id>` at attach
time — that's the authoritative cross-restart marker (MT5 persists the
comment with the saved chart). We ALSO stamp the id into the template's
`description` field and a reserved __chartctl_id input purely for human
forensics / grepping raw .tpl files; the loader does not depend on being
able to read another expert's inputs (MQL5 can't), so the comment is the
real mechanism.

Encoding: modern terminal builds save templates as UTF-16-LE with BOM
and CRLF line endings; older builds accept the same. We always emit
UTF-16-LE + BOM. Every template carries a comment header with the
generator version and the terminal build it was generated for, so a
misbehaving attach can be forensically matched to an encoding profile.

Known-risk note (spec §8): <inputs> value encoding for enums/booleans has
build-specific quirks. Values are passed through verbatim from the .set
file, which is the safest policy: the .set was produced by the same
terminal family that will consume the template. Golden-file tests pin
the exact bytes per build.
"""
from mt5api.chartctl.paths import TEMPLATES_DIR, atomic_write_bytes

import os

GENERATOR_VERSION = "1.0.0"

# EA flags observed in terminal-saved templates: allow live trading +
# allow DLL confirmations off + enabled. 343 = common "enabled, algo
# trading allowed" profile seen across builds; kept as a constant so a
# build-specific override is one line.
EXPERT_FLAGS = 343

# Reserved input name the loader EA reads for attribution. Harmless for
# the target expert: MT5 ignores unknown inputs in templates.
ID_INPUT = "__chartctl_id"


def build_tpl_text(*, deployment_id: str, expert_name: str,
                   expert_rel_path: str, inputs: list[dict],
                   terminal_build: int | None = None) -> str:
    """Compose template text. expert_rel_path like 'Experts\\Uploaded\\X.ex5'."""
    lines = [
        "<chart>",
        f"description=chartctl:{deployment_id} gen={GENERATOR_VERSION}"
        + (f" build={terminal_build}" if terminal_build else ""),
        "shift=1",
        "autoscroll=1",
        "ohlc=1",
        "one_click=0",
        "<expert>",
        f"name={expert_name}",
        f"path={expert_rel_path}",
        f"flags={EXPERT_FLAGS}",
        "expertmode=1",
        "<inputs>",
        f"{ID_INPUT}={deployment_id}",
    ]
    for entry in inputs:
        lines.append(f"{entry['name']}={entry['value']}")
    lines += [
        "</inputs>",
        "</expert>",
        "</chart>",
        "",
    ]
    return "\r\n".join(lines)


def tpl_filename(deployment_id: str) -> str:
    return f"{deployment_id}.tpl"


def tpl_relative_name(deployment_id: str) -> str:
    """The name the loader passes to ChartApplyTemplate. The leading
    backslash makes MT5 resolve it against <data>\\MQL5 — the only
    host-EA-independent root ChartApplyTemplate searches (without it the
    path is relative to the calling EX5's folder, which varies per host
    EA and yields err 5019 file-not-found)."""
    return f"\\Files\\chartctl\\{tpl_filename(deployment_id)}"


def write_tpl(*, deployment_id: str, expert_name: str, expert_rel_path: str,
              inputs: list[dict], terminal_build: int | None = None) -> str:
    text = build_tpl_text(
        deployment_id=deployment_id,
        expert_name=expert_name,
        expert_rel_path=expert_rel_path,
        inputs=inputs,
        terminal_build=terminal_build,
    )
    path = os.path.join(TEMPLATES_DIR, tpl_filename(deployment_id))
    atomic_write_bytes(path, b"\xff\xfe" + text.encode("utf-16-le"))
    return path
