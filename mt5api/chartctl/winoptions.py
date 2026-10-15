"""MT5's Tools > Options > Expert Advisors WebRequest list, set the way a user sets it.

A terminal started with ``/config:<file>`` (every terminal in the VM) never
writes its settings back, so it forgets the list on every restart, and the
common.ini codec does not decode the lists builds 6238 and later write. So
the API fills in the Options dialog of the running terminal, as a user
would: open it with the menu command behind Tools > Options, find the page
holding the "Allow WebRequest for listed URL" box, tick it, change the URLs
in the list below it and press OK, then read the list back to confirm.

Everything goes through window messages to the dialog's own controls
(WM_COMMAND, BM_*, PSM_*, LVM_*), never through keystrokes typed into
whatever has focus: no window has to be in front, nothing reaches another
terminal, and it works with no RDP or VNC viewer attached. The terminals run
elevated (``-Verb RunAs``) and so does the API process, so UIPI lets the
messages through.

MT5's list is a virtual, owner-drawn list view (LVS_OWNERDATA |
LVS_OWNERDRAWFIXED, no LVS_EDITLABELS): its rows give no text through
LVM_GETITEMTEXTW and LVM_EDITLABELW does nothing. A row is read, added or
removed through MetaTrader's own edit box, opened by a double-click posted to
the row; the last row is the greyed "add new URL" row. Structs the list needs
(LVITEMW, RECT, NMITEMACTIVATE) live in a scratch buffer inside the
terminal's process (VirtualAllocEx), freed after.

Ported from the TradeOS agent (commit 889698a), proven live on builds 6230
and 6257. Changes for mt5-httpapi: the DLLs load lazily so the module imports
on Linux (the tests monkeypatch the Win32 layer); ``exact`` (full replace)
and ``partial`` modes; the list is read once per session and rows are removed
by descending index; a dialog that closes in the middle of an edit is
reported as such. See docs/spec/mt5-httpapi-no-autoit in mt-backtest-manager.

Windows only.
"""

from __future__ import annotations

import ctypes
import re
import struct
import threading
import time

_user32 = None
_kernel32 = None
_WNDENUMPROC = None

WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_CLOSE = 0x0010
WM_NOTIFY = 0x004E
WM_KILLFOCUS = 0x0008
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
MK_LBUTTON = 0x0001
VK_ESCAPE = 0x1B
NM_DBLCLK = -3
WM_COMMAND = 0x0111
VK_RETURN = 0x0D
VK_DELETE = 0x2E
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BST_UNCHECKED, BST_CHECKED = 0, 1
BN_CLICKED = 0
IDOK, IDCANCEL = 1, 2
GW_OWNER = 4
GWL_STYLE = -16
MF_BYPOSITION = 0x0400
SMTO_ABORTIFHUNG = 0x0002
TCM_GETITEMCOUNT = 0x1304
PSM_SETCURSEL = 0x0400 + 101
PSM_PRESSBUTTON = 0x0400 + 112
PSM_GETTABCONTROL = 0x0400 + 116
PSM_GETCURRENTPAGEHWND = 0x0400 + 118
PSBTN_OK, PSBTN_CANCEL = 3, 5
LVM_GETITEMCOUNT = 0x1004
LVM_GETITEMRECT = 0x100E
LVM_ENSUREVISIBLE = 0x1013
LVIR_BOUNDS = 0
LVM_SETITEMSTATE = 0x102B
LVM_GETITEMTEXTW = 0x1073
LVIS_FOCUSED, LVIS_SELECTED = 0x1, 0x2
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT, MEM_RESERVE, MEM_RELEASE = 0x1000, 0x2000, 0x8000
PAGE_READWRITE = 0x04

_TIMEOUT_MS = 5000
# One terminal per API process, so a process-local lock is enough: it keeps
# the re-apply thread, PUT, verify and the Navigator refresh from driving the
# same terminal's windows at once. Terminals of other processes need no
# exclusion, since nothing here depends on focus.
_LOCK = threading.Lock()
_MARK = "WebRequest"  # in the checkbox's text in every language the terminals ship
# A list longer than this is not a WebRequest allowlist anyone maintains by hand.
_MAX_ROWS = 200


class OptionsError(Exception):
    """A failure driving the terminal's windows.

    ``code`` is stable and machine-readable, ``diag`` holds what was seen
    (menu items, controls, rows, new windows) for the log and the caller."""

    def __init__(self, code: str, message: str, diag: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.diag = diag or {}


def _load() -> None:
    """Bind user32 and kernel32 on first use, so the module imports on Linux."""
    global _user32, _kernel32, _WNDENUMPROC
    if _user32 is not None:
        return
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    lresult = ctypes.c_ssize_t
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = lresult
    user32.GetMenu.argtypes = [wintypes.HWND]
    user32.GetMenu.restype = wintypes.HMENU
    user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
    user32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
    user32.GetSubMenu.restype = wintypes.HMENU
    user32.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
    user32.GetMenuItemID.restype = wintypes.UINT
    user32.GetMenuStringW.argtypes = [
        wintypes.HMENU,
        wintypes.UINT,
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.UINT,
    ]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.VirtualAllocEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualFreeEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel32.WriteProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _user32, _kernel32, _WNDENUMPROC = user32, kernel32, enum_proc


# --- small Win32 helpers ---------------------------------------------------------------------
def _send(hwnd, msg, wparam=0, lparam=0, timeout_ms=_TIMEOUT_MS) -> int:
    out = ctypes.c_size_t(0)
    if not _user32.SendMessageTimeoutW(
        hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(out)
    ):
        raise OptionsError(
            "terminal_not_responding",
            f"the terminal did not answer message 0x{msg:04x} in {timeout_ms} ms",
        )
    return out.value


def _post(hwnd, msg, wparam=0, lparam=0) -> bool:
    return bool(_user32.PostMessageW(hwnd, msg, wparam, lparam))


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _text(hwnd) -> str:
    """A control's text, also in another process.

    WM_GETTEXT is marshalled across processes; GetWindowText is not."""
    try:
        n = _send(hwnd, WM_GETTEXTLENGTH, timeout_ms=2000)
    except OptionsError:
        return ""
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(min(n, 4096) + 1)
    out = ctypes.c_size_t(0)
    _user32.SendMessageTimeoutW(
        hwnd,
        WM_GETTEXT,
        len(buf),
        ctypes.cast(buf, ctypes.c_void_p).value,
        SMTO_ABORTIFHUNG,
        2000,
        ctypes.byref(out),
    )
    return buf.value


def _pid_of(hwnd) -> int:
    from ctypes import wintypes

    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _top_windows(pid: int) -> list[int]:
    out: list[int] = []

    def cb(hwnd, _l):
        if _pid_of(hwnd) == pid:
            out.append(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def _children(hwnd) -> list[int]:
    out: list[int] = []

    def cb(h, _l):
        out.append(h)
        return True

    _user32.EnumChildWindows(hwnd, _WNDENUMPROC(cb), 0)
    return out


def _style(hwnd) -> int:
    return _user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF


def _describe(hwnd, limit: int = 80) -> list[dict]:
    """The visible controls under a window, for the diagnostics when something is not found."""
    rows = []
    for h in _children(hwnd)[:limit]:
        if not _user32.IsWindowVisible(h):
            continue
        rows.append(
            {
                "class": _class(h),
                "id": _user32.GetDlgCtrlID(h),
                "text": _text(h)[:60],
                "style": f"0x{_style(h):08x}",
            }
        )
    return rows


def _wait(pred, timeout: float, step: float = 0.2):
    end = time.monotonic() + timeout
    while True:
        v = pred()
        if v or time.monotonic() >= end:
            return v
        time.sleep(step)


def norm_url(u: str) -> str:
    return u.strip().rstrip("/").lower()


def is_url_row(text: str) -> bool:
    """A real entry, not the greyed "add new URL like 'https://www.mql5.com'" row at the end.

    Also the rule a URL must pass before it is stored (webrequest.clean_urls):
    a URL the driver would not count as a row after adding it would read as
    missing, and the whole apply would be cancelled."""
    return bool(re.match(r"(?i)^https?://\S+$", text.strip()))


# --- the terminal's process memory (list rows) -----------------------------------------------
class _Remote:
    """A scratch buffer inside the terminal's process for LVITEMW and its text."""

    SIZE = 4096
    TEXT_AT = 512  # row text, up to 1024 characters (to 2560)
    TEXT_CHARS = 1024  # a notification struct goes at 3072
    NOTIFY_AT = 3072

    def __init__(self, pid: int):
        from ctypes import wintypes

        self.h = _kernel32.OpenProcess(
            PROCESS_VM_OPERATION
            | PROCESS_VM_READ
            | PROCESS_VM_WRITE
            | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not self.h:
            raise OptionsError(
                "process_access_denied",
                f"cannot open the terminal process (error {ctypes.get_last_error()})",
            )
        wow = wintypes.BOOL(False)
        _kernel32.IsWow64Process(self.h, ctypes.byref(wow))
        self.is32 = bool(wow.value)
        self.base = _kernel32.VirtualAllocEx(
            self.h, None, self.SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not self.base:
            _kernel32.CloseHandle(self.h)
            raise OptionsError(
                "process_access_denied",
                f"cannot allocate in the terminal process (error {ctypes.get_last_error()})",
            )

    def close(self):
        _kernel32.VirtualFreeEx(self.h, self.base, 0, MEM_RELEASE)
        _kernel32.CloseHandle(self.h)

    def write(self, offset: int, data: bytes):
        n = ctypes.c_size_t(0)
        buf = ctypes.create_string_buffer(data, len(data))
        if not _kernel32.WriteProcessMemory(
            self.h, self.base + offset, buf, len(data), ctypes.byref(n)
        ):
            raise OptionsError("process_access_denied", "cannot write to the terminal process")

    def read(self, offset: int, size: int) -> bytes:
        n = ctypes.c_size_t(0)
        buf = ctypes.create_string_buffer(size)
        if not _kernel32.ReadProcessMemory(self.h, self.base + offset, buf, size, ctypes.byref(n)):
            raise OptionsError("process_access_denied", "cannot read from the terminal process")
        return buf.raw[: n.value]

    def lvitem(
        self, *, sub: int = 0, state: int = 0, mask_state: int = 0, text: bool = False
    ) -> bytes:
        return pack_lvitem(
            self.is32,
            self.base,
            self.TEXT_AT,
            self.TEXT_CHARS,
            sub=sub,
            state=state,
            mask_state=mask_state,
            text=text,
        )


def pack_lvitem(
    is32: bool,
    base: int,
    text_at: int,
    text_chars: int,
    *,
    sub: int = 0,
    state: int = 0,
    mask_state: int = 0,
    text: bool = False,
) -> bytes:
    """LVITEMW up to cchTextMax (mask, iItem, iSubItem, state, stateMask, pszText,
    cchTextMax), padded: the head is 28 bytes on 32-bit and 36 on 64-bit, where
    pszText is 8-aligned."""
    mask = 0x1 if text else 0x8  # LVIF_TEXT / LVIF_STATE
    ptr = base + text_at if text else 0
    cch = text_chars if text else 0
    if is32:
        return (
            struct.pack("<IiiIIIi", mask, 0, sub, state, mask_state, ptr & 0xFFFFFFFF, cch)
            + b"\0" * 32
        )
    return struct.pack("<IiiIIIQi", mask, 0, sub, state, mask_state, 0, ptr, cch) + b"\0" * 48


def pack_nmitemactivate(is32: bool, hwnd: int, ctrl_id: int, idx: int, x: int, y: int) -> bytes:
    """NMITEMACTIVATE for NM_DBLCLK: NMHDR, iItem, iSubItem, uNewState, uOldState,
    uChanged, ptAction, lParam, uKeyFlags. 48 bytes on 32-bit, 72 on 64-bit."""
    if is32:
        return (
            struct.pack("<IIi", hwnd & 0xFFFFFFFF, ctrl_id, NM_DBLCLK)
            + struct.pack("<iiIII", idx, 0, 0, 0, 0)
            + struct.pack("<ii", x, y)
            + struct.pack("<II", 0, 0)
        )
    return (
        struct.pack("<QQi4x", hwnd, ctrl_id, NM_DBLCLK)
        + struct.pack("<iiIII", idx, 0, 0, 0, 0)
        + struct.pack("<ii4x", x, y)
        + struct.pack("<QI4x", 0, 0)
    )


def _key(hwnd, vk: int, scan: int, char: int | None = None, extended: bool = False) -> None:
    """A keystroke as the window would get it: WM_KEYDOWN, WM_CHAR (when the key
    types one), WM_KEYUP."""
    flags = (scan << 16) | (0x01000000 if extended else 0) | 1
    _post(hwnd, WM_KEYDOWN, vk, flags)
    if char is not None:
        _post(hwnd, WM_CHAR, char, flags)
    _post(hwnd, WM_KEYUP, vk, flags | 0xC0000000)


def _visible_under(sheet: int, pid: int) -> set[int]:
    return {h for h in _children(sheet) + _top_windows(pid) if _user32.IsWindowVisible(h)}


class _UrlList:
    """The WebRequest list of the Options page. The last row is the greyed "add new URL" row."""

    def __init__(self, lv: int, sheet: int, pid: int, remote: _Remote):
        self.lv, self.sheet, self.pid, self.remote = lv, sheet, pid, remote
        self.seen: list[str] = []  # classes of the edit boxes that opened (diagnostics)

    def _alive(self) -> None:
        """Enter could press the dialog's default button (OK) if MT5's edit box ever stopped
        claiming it. Never seen on 6230 or 6257, but if the dialog is gone, say so instead of
        failing later as terminal_not_responding."""
        if not _user32.IsWindow(self.sheet):
            raise OptionsError(
                "options_closed_unexpectedly",
                "the Options dialog closed in the middle of an edit; "
                "the list may hold part of the change until the next apply",
            )

    def count(self) -> int:
        return int(_send(self.lv, LVM_GETITEMCOUNT))

    def _item_texts(self) -> list[str]:
        n = self.count()
        if n > _MAX_ROWS + 1:  # + the add row
            # Reading part of the list would verify, and report OK, on part of it.
            raise OptionsError(
                "list_too_long", f"the WebRequest list has more than {_MAX_ROWS} rows", {"rows": n}
            )
        rows = []
        for i in range(n):
            self.remote.write(0, self.remote.lvitem(text=True))
            self.remote.write(self.remote.TEXT_AT, b"\0\0")
            length = _send(self.lv, LVM_GETITEMTEXTW, i, self.remote.base)
            raw = (
                self.remote.read(
                    self.remote.TEXT_AT, min(int(length), self.remote.TEXT_CHARS - 1) * 2
                )
                if length
                else b""
            )
            rows.append(raw.decode("utf-16-le", "replace"))
        return rows

    def _select(self, idx: int) -> None:
        self.remote.write(0, self.remote.lvitem(state=0, mask_state=LVIS_SELECTED | LVIS_FOCUSED))
        _send(self.lv, LVM_SETITEMSTATE, 0xFFFFFFFFFFFFFFFF, self.remote.base)  # -1: every row
        self.remote.write(
            0,
            self.remote.lvitem(
                state=LVIS_SELECTED | LVIS_FOCUSED, mask_state=LVIS_SELECTED | LVIS_FOCUSED
            ),
        )
        _send(self.lv, LVM_SETITEMSTATE, idx, self.remote.base)
        _send(self.lv, LVM_ENSUREVISIBLE, idx, 0)

    def _row_point(self, idx: int) -> int:
        self.remote.write(0, struct.pack("<iiii", LVIR_BOUNDS, 0, 0, 0))
        if not _send(self.lv, LVM_GETITEMRECT, idx, self.remote.base):
            raise OptionsError(
                "list_not_editable", f"row {idx} of the WebRequest list has no position"
            )
        left, top, right, bottom = struct.unpack("<iiii", self.remote.read(0, 16))
        x, y = left + max(4, min(40, (right - left) // 2)), (top + bottom) // 2
        return ((y & 0xFFFF) << 16) | (x & 0xFFFF)

    def _notify_dblclk(self, idx: int, point: int) -> None:
        """NM_DBLCLK to the list's parent, as the list itself sends it.

        The struct lives in the terminal's memory."""
        parent = _user32.GetParent(self.lv)
        cid = _user32.GetDlgCtrlID(self.lv)
        x, y = point & 0xFFFF, (point >> 16) & 0xFFFF
        self.remote.write(
            self.remote.NOTIFY_AT, pack_nmitemactivate(self.remote.is32, self.lv, cid, idx, x, y)
        )
        _send(parent, WM_NOTIFY, cid, self.remote.base + self.remote.NOTIFY_AT)

    def open_editor(self, idx: int) -> int:
        """Open MetaTrader's edit box on row `idx` and return it."""
        self._select(idx)
        point = self._row_point(idx)
        before = _visible_under(self.sheet, self.pid)

        def editor():
            new = [h for h in _visible_under(self.sheet, self.pid) - before if _user32.IsWindow(h)]
            edits = [h for h in new if "edit" in _class(h).lower()]
            return (edits or [0])[0]

        attempts = (
            lambda: (
                _post(self.lv, WM_LBUTTONDBLCLK, MK_LBUTTON, point),
                _post(self.lv, WM_LBUTTONUP, 0, point),
            ),
            lambda: [
                _post(self.lv, m, w, point)
                for m, w in (
                    (WM_LBUTTONDOWN, MK_LBUTTON),
                    (WM_LBUTTONUP, 0),
                    (WM_LBUTTONDBLCLK, MK_LBUTTON),
                    (WM_LBUTTONUP, 0),
                )
            ],
            lambda: self._notify_dblclk(idx, point),
        )
        for attempt in attempts:
            attempt()
            edit = _wait(editor, 2.0, 0.1)
            if edit:
                self.seen.append(_class(edit))
                return edit
        new = _visible_under(self.sheet, self.pid) - before
        raise OptionsError(
            "list_not_editable",
            "the WebRequest list did not open a row for editing",
            {
                "row": idx,
                "rows": self.count(),
                "style": f"0x{_style(self.lv):08x}",
                "newWindows": [{"class": _class(h), "text": _text(h)[:60]} for h in list(new)[:10]],
            },
        )

    def close_editor(self, edit: int, commit: bool) -> bool:
        """End the edit with Enter (commit) or Escape. If the box ignores both,
        losing the focus ends it as MetaTrader does when you click elsewhere
        (with the text it holds)."""
        if commit:
            _key(edit, VK_RETURN, 0x1C, VK_RETURN)
        else:
            _key(edit, VK_ESCAPE, 0x01, VK_ESCAPE)
        closed = _wait(
            lambda: not _user32.IsWindow(edit) or not _user32.IsWindowVisible(edit), 1.5, 0.1
        )
        if not closed:
            _send(edit, WM_KILLFOCUS, 0, 0)
            closed = _wait(
                lambda: not _user32.IsWindow(edit) or not _user32.IsWindowVisible(edit), 1.5, 0.1
            )
        self._alive()
        return bool(closed)

    def rows(self) -> list[str]:
        """The text of every row except the last (the add row), in order."""
        texts = self._item_texts()
        if any(t.strip() for t in texts):
            return [t.strip() for t in texts[:-1]]
        out = []
        for i in range(len(texts) - 1):  # read each row through the edit box, then Escape
            edit = self.open_editor(i)
            text = _text(edit).strip()
            if not self.close_editor(edit, commit=False):
                raise OptionsError(
                    "list_not_editable",
                    "a row of the WebRequest list stayed open for editing",
                    {"row": i},
                )
            out.append(text)
        return out

    def add(self, url: str) -> None:
        n = self.count()
        if n < 1:
            raise OptionsError(
                "list_not_editable", "the WebRequest list has no row to add to", {"rows": n}
            )
        edit = self.open_editor(n - 1)
        try:
            buf = ctypes.create_unicode_buffer(url)
            _send(edit, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value)
            typed = _text(edit)
            if typed != url:
                raise OptionsError(
                    "list_not_changed", "the edit box did not take the URL", {"typed": typed[:120]}
                )
            if not self.close_editor(edit, commit=True):
                raise OptionsError(
                    "list_not_changed",
                    "the new row stayed open for editing",
                    {"rows": self.count()},
                )
        except OptionsError as err:
            if (
                err.code != "options_closed_unexpectedly"
                and _user32.IsWindow(edit)
                and _user32.IsWindowVisible(edit)
            ):
                self.close_editor(edit, commit=False)  # leave the add row empty for the next URL
            raise
        if not _wait(lambda: self.count() > n, 1.5, 0.1):
            raise OptionsError(
                "list_not_changed",
                "the WebRequest list did not grow",
                {"rows": self.count(), "before": n},
            )

    def remove(self, idx: int) -> None:
        n = self.count()
        self._select(idx)
        _key(self.lv, VK_DELETE, 0x53, None, extended=True)  # the list tells its page (LVN_KEYDOWN)
        if not _wait(lambda: self.count() < n, 1.5, 0.1):
            self._alive()
            raise OptionsError(
                "list_not_changed",
                "the WebRequest list did not remove the row",
                {"row": idx, "rows": n},
            )
        self._alive()


# --- the plan: what one session changes, pure logic -----------------------------------------
def plan_changes(
    rows: list[str],
    add: list[str],
    remove: list[str],
    exact: list[str] | None,
    enabled_before: bool,
) -> dict:
    """What one dialog session must do, from the rows it read.

    Returns {"remove_idx": [...] (descending), "add": [...], "want_enabled": bool | None}.

    - ``exact`` (full replace): every row not wanted goes, URL or not, and so do
      extra copies of a wanted URL; the missing URLs are added. A non-empty
      ``exact`` ticks the box whatever the diff (a terminal whose list already
      matches but whose box is unticked refuses every WebRequest call); an
      empty one unticks it.
    - patch (``add``/``remove``): only the named URLs change, every other row
      stays. Adding ticks the box; removing leaves it as it is.
    - neither: a read; nothing changes.
    """
    remove_idx: list[int] = []
    want_add: list[str] = []
    want_enabled: bool | None = None
    if exact is not None:
        wanted = {norm_url(u) for u in exact}
        kept: set[str] = set()
        for i, row in enumerate(rows):
            key = norm_url(row)
            if is_url_row(row) and key in wanted and key not in kept:
                kept.add(key)
            else:
                remove_idx.append(i)
        for u in exact:
            if norm_url(u) not in kept:
                kept.add(norm_url(u))
                want_add.append(u)
        want_enabled = bool(exact)
    else:
        have = {norm_url(r) for r in rows if is_url_row(r)}
        gone = {norm_url(u) for u in remove}
        remove_idx = [i for i, r in enumerate(rows) if is_url_row(r) and norm_url(r) in gone]
        for u in add:
            key = norm_url(u)
            if key not in have and key not in gone:
                have.add(key)
                want_add.append(u)
        if add:
            want_enabled = True
    if want_enabled is not None and want_enabled == enabled_before:
        want_enabled = None
    return {
        "remove_idx": sorted(remove_idx, reverse=True),
        "add": want_add,
        "want_enabled": want_enabled,
    }


# --- the dialog ------------------------------------------------------------------------------
def main_window(pid: int) -> int:
    for h in _top_windows(pid):
        if (
            _user32.IsWindowVisible(h)
            and not _user32.GetWindow(h, GW_OWNER)
            and _class(h).startswith("MetaQuotes::MetaTrader")
        ):
            return h
    for h in _top_windows(pid):  # an unusual class name: any visible, unowned window with a menu
        if _user32.IsWindowVisible(h) and not _user32.GetWindow(h, GW_OWNER) and _user32.GetMenu(h):
            return h
    return 0


def _menu_items(hmenu, path=(), depth=0, out=None) -> list[tuple[tuple, str, int]]:
    out = [] if out is None else out
    if not hmenu or depth > 3:
        return out
    for i in range(max(_user32.GetMenuItemCount(hmenu), 0)):
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetMenuStringW(hmenu, i, buf, 256, MF_BYPOSITION)
        text = buf.value
        sub = _user32.GetSubMenu(hmenu, i)
        if sub:
            _menu_items(sub, path + (text.replace("&", ""),), depth + 1, out)
        else:
            item_id = _user32.GetMenuItemID(hmenu, i)
            if item_id not in (0, 0xFFFFFFFF):
                out.append((path, text, item_id))
    return out


def pick_options_command(items: list[tuple[tuple, str, int]]) -> int:
    """The menu command id of Tools > Options: its shortcut is Ctrl+O (the label is translated,
    the shortcut key is not), else an item labelled Options. 0 when neither is there."""
    for _path, text, item_id in items:
        if "\t" in text and re.search(r"\+\s*O$", text.split("\t", 1)[1].strip(), re.I):
            return item_id
    for _path, text, item_id in items:
        if text.split("\t", 1)[0].replace("&", "").strip().rstrip(".").lower() == "options":
            return item_id
    return 0


def options_command(main: int) -> tuple[int, list]:
    items = _menu_items(_user32.GetMenu(main))
    return pick_options_command(items), items


def _sheets(pid: int) -> list[int]:
    """The terminal's open property-sheet dialogs (Options is one)."""
    out = []
    for h in _top_windows(pid):
        if _class(h) == "#32770" and _user32.IsWindowVisible(h):
            if any(
                _class(c) == "SysTabControl32" and _user32.GetParent(c) == h for c in _children(h)
            ):
                out.append(h)
    return out


def _find_page(sheet: int) -> tuple[int, int, int]:
    """(page, checkbox, list) of the page with the WebRequest box, switching pages as needed."""

    def on_current():
        page = _send(sheet, PSM_GETCURRENTPAGEHWND) or sheet
        box = lst = 0
        for c in _children(page):
            cls = _class(c)
            if not box and cls == "Button" and _MARK.lower() in _text(c).lower():
                box = c
            elif not lst and cls == "SysListView32":
                lst = c
        return (page, box, lst) if box else None

    hit = on_current()
    if hit:
        return hit
    tab = _send(sheet, PSM_GETTABCONTROL)
    count = _send(tab, TCM_GETITEMCOUNT) if tab else 0
    for i in range(int(count)):
        _send(sheet, PSM_SETCURSEL, i, 0)
        hit = _wait(on_current, 1.5, 0.15)
        if hit:
            return hit
    raise OptionsError(
        "webrequest_box_not_found",
        "no page of the Options dialog has the WebRequest box",
        {"pages": int(count), "controls": _describe(sheet)},
    )


def _press(sheet: int, ok: bool) -> bool:
    _post(sheet, PSM_PRESSBUTTON, PSBTN_OK if ok else PSBTN_CANCEL, 0)
    if _wait(lambda: not _user32.IsWindow(sheet), 5.0):
        return True
    _post(sheet, WM_COMMAND, IDOK if ok else IDCANCEL, 0)
    if _wait(lambda: not _user32.IsWindow(sheet), 5.0):
        return True
    if not ok:
        _post(sheet, WM_CLOSE)
        return bool(_wait(lambda: not _user32.IsWindow(sheet), 5.0))
    return False


def _set_box(page: int, box: int, checked: bool) -> None:
    _send(box, BM_SETCHECK, BST_CHECKED if checked else BST_UNCHECKED, 0)
    _send(page, WM_COMMAND, (BN_CLICKED << 16) | (_user32.GetDlgCtrlID(box) & 0xFFFF), box)


def apply(
    pid: int,
    add: list[str],
    remove: list[str],
    *,
    exact: list[str] | None = None,
    partial: bool = False,
    wait_window: float = 30.0,
) -> dict:
    """Read, and change when asked, the terminal's WebRequest list through its Options dialog.

    ``exact`` replaces the whole list; otherwise ``add``/``remove`` patch it and
    every other row stays; with neither it only reads. With ``partial`` an add
    the list does not take is skipped and listed under ``rejected`` instead of
    cancelling the session.

    Returns {"enabled", "urls" (the URL rows after), "added", "removed",
    "rejected", "changed", "editor"}. Raises OptionsError; the dialog is never
    left open, and nothing is saved unless every change went through (OK is
    pressed only then)."""
    _load()
    # Waiting for the window happens before the lock, so a terminal still
    # starting does not hold up this process's other GUI calls.
    main = _wait(lambda: main_window(pid), wait_window, 0.5)
    if not main:
        raise OptionsError(
            "window_not_found", "the terminal has no main window in this Windows session"
        )
    with _LOCK:
        if not _user32.IsWindow(main):
            raise OptionsError("window_not_found", "the terminal's main window closed")
        if not _user32.IsWindowEnabled(main) or _sheets(pid):
            raise OptionsError(
                "terminal_busy",
                "the terminal is showing a dialog (Options, login or another): "
                "close it in the VM and try again",
            )
        cmd, items = options_command(main)
        if not cmd:
            raise OptionsError(
                "options_command_not_found",
                "the terminal's menu has no Options command",
                {"menu": [{"path": "/".join(p), "text": t, "id": i} for p, t, i in items[:150]]},
            )
        _post(main, WM_COMMAND, cmd, 0)
        sheet = _wait(lambda: (_sheets(pid) or [0])[0], 10.0)
        if not sheet:
            # A dialog that opens just after the wait would stay open and make
            # every later call fail as terminal_busy: look once more, cancel it.
            for late in _wait(lambda: _sheets(pid), 2.0):
                _press(late, False)
            raise OptionsError(
                "options_not_opened", "the Options dialog did not open", {"command": cmd}
            )
        return _session(pid, sheet, add, remove, exact, partial)


def _session(pid: int, sheet: int, add, remove, exact, partial) -> dict:
    ok = False
    remote = None
    try:
        page, box, lv = _find_page(sheet)
        if not lv:
            raise OptionsError(
                "webrequest_list_not_found",
                "the WebRequest list was not found",
                {"controls": _describe(page)},
            )
        remote = _Remote(pid)
        lst = _UrlList(lv, sheet, pid, remote)
        before = lst.rows()
        enabled_before = _send(box, BM_GETCHECK) == BST_CHECKED
        plan = plan_changes(before, add, remove, exact, enabled_before)
        before_urls = [r for r in before if is_url_row(r)]
        if not (plan["remove_idx"] or plan["add"] or plan["want_enabled"] is not None):
            return {
                "enabled": enabled_before,
                "urls": before_urls,
                "added": [],
                "removed": [],
                "rejected": [],
                "changed": False,
            }
        # Tick before adding, untick after removing: MT5 might grey the list out
        # while the box is unticked.
        if plan["want_enabled"] is True:
            _set_box(page, box, True)
        removed_rows = [before[i] for i in plan["remove_idx"]]
        for idx in plan["remove_idx"]:  # descending, so the rows still to go keep their index
            lst.remove(idx)
        if plan["want_enabled"] is False:
            _set_box(page, box, False)
        rejected: list[str] = []
        for u in plan["add"]:
            try:
                lst.add(u)
            except OptionsError as err:
                if not partial or err.code == "options_closed_unexpectedly":
                    raise
                rejected.append(u)
        after = lst.rows()
        enabled = _send(box, BM_GETCHECK) == BST_CHECKED
        expect_enabled = (
            plan["want_enabled"] if plan["want_enabled"] is not None else enabled_before
        )
        if exact is not None:
            expect_enabled = bool(exact)
        check = _verify(after, before, plan, exact, rejected)
        if check["missing"] or check["not_removed"] or enabled != expect_enabled:
            raise OptionsError(
                "list_not_changed",
                "the WebRequest list did not take the change",
                {
                    "rows": after,
                    "missing": check["missing"],
                    "not_removed": check["not_removed"],
                    "enabled": enabled,
                    "rejected": rejected,
                },
            )
        ok = True
        return {
            "enabled": enabled,
            "urls": [r for r in after if is_url_row(r)],
            "added": [u for u in plan["add"] if u not in rejected],
            "removed": removed_rows,
            "rejected": rejected,
            "changed": True,
            "editor": sorted(set(lst.seen)),
        }
    finally:
        if remote is not None:
            remote.close()
        closed = _press(sheet, ok)
        if ok and not closed:
            # OK did not close it (a validation message?): cancel, so the
            # terminal is not left blocked.
            _press(sheet, False)
            raise OptionsError(
                "options_not_saved", "the Options dialog did not close on OK; nothing was saved"
            )


def _verify(after: list[str], before: list[str], plan: dict, exact, rejected: list[str]) -> dict:
    """What did not take, comparing the rows read after the changes with what was wanted."""
    if exact is not None:
        want = [u for u in exact if norm_url(u) not in {norm_url(r) for r in rejected}]
        want_keys = [norm_url(u) for u in want]
        missing = [u for u in want if norm_url(u) not in {norm_url(r) for r in after}]
        not_removed = [r for r in after if norm_url(r) not in want_keys or not is_url_row(r)]
        dupes = [r for i, r in enumerate(after) if norm_url(r) in [norm_url(x) for x in after[:i]]]
        return {"missing": missing, "not_removed": not_removed + dupes}
    now = {norm_url(r) for r in after if is_url_row(r)}
    skip = {norm_url(u) for u in rejected}
    missing = [u for u in plan["add"] if norm_url(u) not in now and norm_url(u) not in skip]
    gone = {norm_url(before[i]) for i in plan["remove_idx"]}
    not_removed = [r for r in after if norm_url(r) in gone]
    return {"missing": missing, "not_removed": not_removed}
