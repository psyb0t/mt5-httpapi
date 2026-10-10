"""scripts/desktop_heap.py: raise the VM's interactive desktop heap.

At the Windows default (20 MB) a VM running 20+ terminals runs out, Windows
logs Win32k event 243, and each terminal launched after that aborts at
startup with exit code 10053.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "desktop_heap", Path(__file__).resolve().parent.parent / "scripts" / "desktop_heap.py"
)
desktop_heap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(desktop_heap)

#: The value as Windows 11 22H2 ships it.
DEFAULT = (
    r"%SystemRoot%\system32\csrss.exe ObjectDirectory=\Windows "
    r"SharedSection=1024,20480,768 Windows=On SubSystemType=Windows "
    r"ServerDll=basesrv,1 ServerDll=winsrv:UserServerDllInitialization,3 "
    r"ServerDll=sxssrv,4 ProfileControl=Off MaxRequestThreads=16"
)


def test_raises_only_the_interactive_heap():
    raised = desktop_heap.raised_shared_section(DEFAULT, 65536)

    assert "SharedSection=1024,65536,768" in raised
    assert raised.replace("65536", "20480") == DEFAULT


def test_leaves_a_large_enough_heap_alone():
    already = DEFAULT.replace("1024,20480,768", "1024,81920,768")

    assert desktop_heap.raised_shared_section(already, 65536) is None
    assert desktop_heap.raised_shared_section(
        DEFAULT.replace("20480", "65536"), 65536
    ) is None


def test_two_field_shared_section_is_raised_too():
    two = DEFAULT.replace("1024,20480,768", "1024,20480")

    assert "SharedSection=1024,65536 " in desktop_heap.raised_shared_section(two, 65536)


def test_a_value_without_shared_section_is_refused():
    with pytest.raises(ValueError):
        desktop_heap.raised_shared_section("csrss.exe Windows=On", 65536)
