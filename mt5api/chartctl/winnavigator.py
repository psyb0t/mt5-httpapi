"""MT5's Navigator refresh with window messages.

MT5 loads only experts it saw on disk when it started, or that a Navigator
refresh has picked up since: a template naming any other .ex5 opens the chart
with no expert on it. A user right-clicks "Expert Advisors" in the Navigator
and chooses Refresh. This module does the same without keystrokes to whatever
has focus, and then checks that the expert really is in the tree:

  a. WM_COMMAND <Refresh id> posted to the Navigator tree's parent;
  c. the tree's context menu (WM_CONTEXTMENU, keyboard form), Refresh found in
     its HMENU (MN_GETHMENU) by label or id, chosen with Down/Enter posted to
     the menu window.

(Variant b, the same command posted to the main window, also worked in the
probe but adds nothing over a.) On build 6257 a listed the expert after 31 ms
and c after 203 ms; no variant listed it without a refresh. The Refresh id
33416 is the same on 6230 and 6257, and c reads the real id from the menu in
case a build changes it.

The "Expert Advisors" node and the Refresh item are found by label. English
is built in; a terminal in another language needs its translations in
config.yaml (chartctl.navigator_labels), passed in by the caller.

Ported from the TradeOS agent (commit 1521fe3). Tree texts come from
TVM_GETITEMW with the TVITEMW in the terminal's memory; the tree is not
owner-drawn. Windows only.
"""

from __future__ import annotations

import ctypes
import struct
import time

from mt5api.chartctl import winoptions as w
from mt5api.chartctl.winoptions import OptionsError

TVM_EXPAND = 0x1102
TVM_GETNEXTITEM = 0x110A
TVM_SELECTITEM = 0x110B
TVM_GETITEMW = 0x113E
TVGN_ROOT, TVGN_NEXT, TVGN_CHILD, TVGN_CARET = 0x0, 0x1, 0x4, 0x9
TVE_EXPAND = 0x2
TVIF_TEXT, TVIF_HANDLE = 0x1, 0x10
WM_CONTEXTMENU = 0x007B
MN_GETHMENU = 0x01E1
VK_DOWN = 0x28
REFRESH_ID = 33416
EA_NODE = "Expert Advisors"
REFRESH_LABEL = "Refresh"
TEXT_CHARS = 512
# Seconds to wait for the main window before giving up; the Navigator refresh
# runs on a terminal that is already up, unlike the boot-time allowlist apply.
_WAIT_WINDOW_SECONDS = 10.0


def pack_tvitem(is32: bool, text_ptr: int, hitem: int) -> bytes:
    """TVITEMW: mask, hItem, state, stateMask, pszText, cchTextMax, iImage,
    iSelectedImage, cChildren, lParam. 40 bytes on 32-bit, 56 on 64-bit."""
    if is32:
        return struct.pack(
            "<IIIIIiiiiI",
            TVIF_TEXT | TVIF_HANDLE,
            hitem & 0xFFFFFFFF,
            0,
            0,
            text_ptr & 0xFFFFFFFF,
            TEXT_CHARS,
            0,
            0,
            0,
            0,
        )
    return struct.pack(
        "<I4xQIIQiiiiQ", TVIF_TEXT | TVIF_HANDLE, hitem, 0, 0, text_ptr, TEXT_CHARS, 0, 0, 0, 0
    )


def labels_with_default(configured, default: str) -> tuple[str, ...]:
    """The configured labels in order, then the built-in English one, without repeats."""
    out: list[str] = []
    for label in [*(configured or []), default]:
        if (
            isinstance(label, str)
            and label.strip()
            and label.strip().lower() not in {x.lower() for x in out}
        ):
            out.append(label.strip())
    return tuple(out)


class _Tree:
    def __init__(self, tree: int, remote: w._Remote):
        self.tree, self.remote = tree, remote

    def next(self, code: int, item: int = 0) -> int:
        return int(w._send(self.tree, TVM_GETNEXTITEM, code, item))

    def children(self, item: int, expand: bool = False) -> list[int]:
        if expand:
            w._send(self.tree, TVM_EXPAND, TVE_EXPAND, item)
        out, c = [], self.next(TVGN_CHILD, item)
        while c and len(out) < 2000:
            out.append(c)
            c = self.next(TVGN_NEXT, c)
        return out

    def text(self, item: int) -> str:
        self.remote.write(
            0, pack_tvitem(self.remote.is32, self.remote.base + self.remote.TEXT_AT, item)
        )
        self.remote.write(self.remote.TEXT_AT, b"\0\0")
        if not w._send(self.tree, TVM_GETITEMW, 0, self.remote.base):
            return ""
        raw = self.remote.read(self.remote.TEXT_AT, TEXT_CHARS * 2)
        return raw.decode("utf-16-le", "replace").split("\0", 1)[0]

    def roots(self) -> list[int]:
        out, r = [], self.next(TVGN_ROOT)
        while r and len(out) < 50:
            out.append(r)
            r = self.next(TVGN_NEXT, r)
        return out

    def ea_node(self, labels: tuple[str, ...]) -> int:
        """ "Expert Advisors": a root, or a child of a root (MT5 nests it under "MetaTrader 5")."""
        wanted = {label.lower() for label in labels}
        for r in self.roots():
            if self.text(r).lower() in wanted:
                return r
            for c in self.children(r):
                if self.text(c).lower() in wanted:
                    return c
        return 0

    def find(self, start: int, path: list[str]) -> int:
        """The item at `path` (folder names, then the expert's name without .ex5) under `start`."""
        item = start
        for seg in path:
            item = next(
                (
                    c
                    for c in self.children(item, expand=True)
                    if self.text(c).lower() == seg.lower()
                ),
                0,
            )
            if not item:
                return 0
        return item


def _navigator(pid: int, remote: w._Remote, ea_labels: tuple[str, ...]) -> tuple[_Tree, int, int]:
    main = w.main_window(pid)
    if not main:
        raise OptionsError(
            "window_not_found", "the terminal has no main window in this Windows session"
        )
    seen = []
    for h in w._children(main):
        if w._class(h) != "SysTreeView32":
            continue
        t = _Tree(h, remote)
        ea = t.ea_node(ea_labels)
        seen.append({"roots": [t.text(r) for r in t.roots()][:10]})
        if ea:
            return t, ea, main
    raise OptionsError(
        "navigator_not_found",
        "no tree view of the terminal has an Expert Advisors node; a terminal in "
        "another language needs chartctl.navigator_labels in config.yaml",
        {"trees": seen, "labels": list(ea_labels)},
    )


def _menus(pid: int) -> list[int]:
    return [
        h for h in w._top_windows(pid) if w._class(h) == "#32768" and w._user32.IsWindowVisible(h)
    ]


def _menu_items(hmenu) -> list[tuple[str, int]]:
    out = []
    for i in range(max(w._user32.GetMenuItemCount(hmenu), 0)):
        buf = ctypes.create_unicode_buffer(256)
        w._user32.GetMenuStringW(hmenu, i, buf, 256, w.MF_BYPOSITION)
        out.append(
            (buf.value.replace("&", "").split("\t", 1)[0], int(w._user32.GetMenuItemID(hmenu, i)))
        )
    return out


def refresh_position(items: list[tuple[str, int]], labels: tuple[str, ...]) -> tuple[int, int]:
    """(index of Refresh in the menu or -1, Down presses to reach it from a fresh menu).
    Down selects the first selectable item; separators (no text) are skipped."""
    wanted = {label.lower() for label in labels}
    pos = next(
        (k for k, (tx, i) in enumerate(items) if tx.strip().lower() in wanted or i == REFRESH_ID),
        -1,
    )
    if pos < 0:
        return -1, 0
    return pos, sum(1 for tx, _ in items[: pos + 1] if tx)


def _close_menus(pid: int) -> None:
    for m in _menus(pid):
        w._key(m, w.VK_ESCAPE, 0x01)
    w._wait(lambda: not _menus(pid), 2.0, 0.1)


def _variant_c(t: _Tree, ea: int, pid: int, diag: dict, refresh_labels: tuple[str, ...]) -> None:
    w._send(t.tree, TVM_SELECTITEM, TVGN_CARET, ea)
    w._post(
        t.tree, WM_CONTEXTMENU, t.tree, -1
    )  # lParam -1: invoked from the keyboard, at the selection
    menu = w._wait(lambda: (_menus(pid) or [0])[0], 3.0, 0.1)
    if not menu:
        diag["c"] = "no context menu opened"
        return
    hmenu = w._send(menu, MN_GETHMENU)
    items = _menu_items(hmenu)
    diag["menu"] = [{"text": tx, "id": i} for tx, i in items]
    pos, steps = refresh_position(items, refresh_labels)
    if pos < 0:
        _close_menus(pid)
        diag["c"] = "no Refresh item in the menu"
        return
    for _ in range(steps):
        w._key(menu, VK_DOWN, 0x50, None, extended=True)
    w._key(menu, w.VK_RETURN, 0x1C, w.VK_RETURN)
    if not w._wait(
        lambda: not w._user32.IsWindow(menu) or not w._user32.IsWindowVisible(menu), 2.0, 0.1
    ):
        _close_menus(pid)
        diag["c"] = "the menu did not close on Enter"


def refresh(
    pid: int,
    path: list[str] | None,
    variants: tuple[str, ...] = ("a", "c"),
    verify_sec: float = 5.0,
    *,
    ea_labels: tuple[str, ...] = (EA_NODE,),
    refresh_labels: tuple[str, ...] = (REFRESH_LABEL,),
) -> dict:
    """Refresh the Navigator, trying `variants` in order until the expert at `path` shows up.

    An expert already listed before the refresh proves nothing about the
    refresh: variant a is still sent and the result says ``already_listed``.
    Without `path` the first variant is sent and nothing is verified. Returns
    {"variant", "found", "already_listed", "tried", "diag"}; raises OptionsError
    when the Navigator cannot be found."""
    w._load()
    if not w._wait(lambda: w.main_window(pid), _WAIT_WINDOW_SECONDS, 0.5):
        raise OptionsError(
            "window_not_found", "the terminal has no main window in this Windows session"
        )
    with w._LOCK:
        remote = w._Remote(pid)
        diag: dict = {}
        tried: list[str] = []
        try:
            t, ea, _main = _navigator(pid, remote, ea_labels)
            parent = w._user32.GetParent(t.tree)
            diag["treeParentClass"] = w._class(parent) if parent else None
            if path and t.find(ea, path):
                w._post(parent, w.WM_COMMAND, REFRESH_ID, 0)
                return {
                    "variant": "a",
                    "found": True,
                    "already_listed": True,
                    "tried": ["a"],
                    "diag": diag,
                }
            for v in variants:
                tried.append(v)
                started = time.monotonic()
                if v == "a":
                    w._post(parent, w.WM_COMMAND, REFRESH_ID, 0)
                elif v == "c":
                    _variant_c(t, ea, pid, diag, refresh_labels)
                else:
                    continue
                if path is None:
                    return {
                        "variant": v,
                        "found": None,
                        "already_listed": False,
                        "tried": tried,
                        "diag": diag,
                    }

                def present():
                    try:
                        t2, ea2, _ = _navigator(
                            pid, remote, ea_labels
                        )  # a refresh may rebuild the tree
                        return bool(t2.find(ea2, path))
                    except OptionsError:
                        return False

                if w._wait(present, verify_sec, 0.25):
                    diag[f"{v}Ms"] = int((time.monotonic() - started) * 1000)
                    return {
                        "variant": v,
                        "found": True,
                        "already_listed": False,
                        "tried": tried,
                        "diag": diag,
                    }
            return {
                "variant": None,
                "found": False,
                "already_listed": False,
                "tried": tried,
                "diag": diag,
            }
        finally:
            _close_menus(pid)
            remote.close()
