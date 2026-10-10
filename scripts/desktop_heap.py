"""Raise the interactive desktop heap of a Windows VM that runs many terminals.

Every window, console and terminal in the logged-on session draws on that
session's desktop heap, sized by the second field of
``SharedSection=1024,20480,768`` (KB) in the registry value
``HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\SubSystems\\Windows``.
At the 20 MB default a VM running ~20+ terminals plus an API console each
runs out: Windows logs Win32k event 243 ("A desktop heap allocation
failed") and every terminal launched after that exits about a second after
"started for" with code 10053, before reaching the broker or the tester.
On a two-VM install running ~50 terminals each, every burst of 10053 aborts
over a week followed a 243 event on the same VM.

Run by start.bat at every boot. Exit codes:
    0  already at or above the target, nothing changed
    3  raised; takes effect on the next boot, so the caller reboots
    1  could not read or write the value
"""
from __future__ import annotations

import re
import sys

KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\SubSystems"
VALUE = "Windows"
#: 64 MB for the interactive desktop. The session's desktop heaps share its
#: view space (104 MB on the oldest x64 limit, larger since), so this fits.
TARGET_KB = 65536

_SHARED_SECTION = re.compile(r"(SharedSection=)(\d+),(\d+)", re.IGNORECASE)


def raised_shared_section(windows_value, target_kb=TARGET_KB):
    """``windows_value`` with the interactive heap raised to ``target_kb``.

    None when it is already at least that large. Every other field, and the
    rest of the value, is kept as it was. Raises ValueError when the value
    carries no SharedSection with at least two fields.
    """
    match = _SHARED_SECTION.search(windows_value)
    if match is None:
        raise ValueError("no SharedSection=<a>,<b> in the Windows subsystem value")
    if int(match.group(3)) >= target_kb:
        return None
    return (
        windows_value[: match.start(3)]
        + str(target_kb)
        + windows_value[match.end(3) :]
    )


def main():
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            KEY,
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, VALUE)
            new_value = raised_shared_section(value)
            if new_value is None:
                print(f"desktop heap ok (>= {TARGET_KB} KB)")
                return 0
            winreg.SetValueEx(key, VALUE, 0, value_type, new_value)
            written, _ = winreg.QueryValueEx(key, VALUE)
    except (OSError, ValueError) as exc:
        print(f"desktop heap check failed: {exc}")
        return 1
    if raised_shared_section(written) is not None:
        print("desktop heap write did not stick")
        return 1
    print(f"desktop heap raised to {TARGET_KB} KB; reboot to apply")
    return 3


if __name__ == "__main__":
    sys.exit(main())
