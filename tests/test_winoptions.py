"""The Options-dialog driver's logic (chartctl/winoptions.py), Win32 faked.

What only a real terminal shows (the dialog, the owner-drawn list, MetaTrader's
edit box) is checked live in the VM; see docs/chart-deployments.md. These
tests pin the decisions the driver makes from what it reads.
"""
from __future__ import annotations

import pytest

from mt5api.chartctl import winoptions as w

A, B, C = "https://a.example", "https://b.example", "https://c.example"


def test_the_module_imports_without_windows():
    assert w._user32 is None or w._user32  # loaded lazily, never at import


@pytest.mark.parametrize(
    ("text", "is_url"),
    [(A, True), (" https://a.example/path ", True), ("HTTP://A.EXAMPLE", True),
     ("add new URL like 'https://www.mql5.com'", False), ("https://", False),
     ("https://a b.example", False), ("www.example.com", False), ("", False)],
)
def test_is_url_row(text, is_url):
    assert w.is_url_row(text) is is_url


def test_lvitem_head_is_36_bytes_on_64_bit_and_28_on_32_bit():
    import struct

    x64 = w.pack_lvitem(False, 0x10000, 512, 1024, text=True)
    x86 = w.pack_lvitem(True, 0x10000, 512, 1024, text=True)
    assert len(x64) == 36 + 48 and len(x86) == 28 + 32
    # pszText is 8-aligned at 24 and cchTextMax follows at 32 on x64.
    assert struct.unpack_from("<Q", x64, 24)[0] == 0x10000 + 512
    assert struct.unpack_from("<i", x64, 32)[0] == 1024
    assert struct.unpack_from("<I", x86, 20)[0] == 0x10000 + 512
    assert struct.unpack_from("<i", x86, 24)[0] == 1024


def test_nmitemactivate_is_72_bytes_on_64_bit_and_48_on_32_bit():
    import struct

    x64 = w.pack_nmitemactivate(False, 0x1234, 7, 3, 10, 20)
    assert len(x64) == 72 and len(w.pack_nmitemactivate(True, 0x1234, 7, 3, 10, 20)) == 48
    # NMHDR is 24 bytes (code padded), iItem at 24, ptAction at 44, lParam at 56.
    assert struct.unpack_from("<i", x64, 16)[0] == w.NM_DBLCLK
    assert struct.unpack_from("<i", x64, 24)[0] == 3
    assert struct.unpack_from("<ii", x64, 44) == (10, 20)


def test_the_options_command_is_picked_by_its_untranslated_ctrl_o_shortcut():
    items = [((), "&New Chart\tCtrl+N", 100), (("Tools",), "&Optionen\tCtrl+O", 33133),
             (("Tools",), "Options", 1)]
    assert w.pick_options_command(items) == 33133


def test_the_options_label_is_the_fallback_without_a_shortcut():
    items = [((), "&New Chart\tCtrl+N", 100), (("Tools",), "&Options...", 33133)]
    assert w.pick_options_command(items) == 33133
    assert w.pick_options_command([((), "Exit", 1)]) == 0


# ── plan_changes ──────────────────────────────────────────────────────


def test_exact_removes_unwanted_rows_and_adds_missing_ones():
    plan = w.plan_changes([A, B], [], [], [A, C], enabled_before=True)
    assert plan == {"remove_idx": [1], "add": [C], "want_enabled": None}


def test_exact_on_a_matching_list_with_the_box_unticked_ticks_the_box():
    plan = w.plan_changes([A], [], [], [A], enabled_before=False)
    assert plan == {"remove_idx": [], "add": [], "want_enabled": True}


def test_an_empty_exact_removes_every_row_and_unticks_the_box():
    plan = w.plan_changes([A, B], [], [], [], enabled_before=True)
    assert plan == {"remove_idx": [1, 0], "add": [], "want_enabled": False}


def test_exact_removes_non_url_rows_and_extra_copies():
    rows = [A, "www.typed-by-hand.example", A.upper() + "/", B]
    plan = w.plan_changes(rows, [], [], [A, B], enabled_before=True)
    assert plan["remove_idx"] == [2, 1]
    assert plan["add"] == []


def test_exact_matches_ignoring_case_and_a_trailing_slash():
    plan = w.plan_changes([A + "/"], [], [], [A.upper()], enabled_before=True)
    assert plan == {"remove_idx": [], "add": [], "want_enabled": None}


def test_a_patch_keeps_every_row_it_was_not_asked_to_change():
    rows = [A, "www.typed-by-hand.example", B]
    plan = w.plan_changes(rows, [C], [B], None, enabled_before=True)
    assert plan == {"remove_idx": [2], "add": [C], "want_enabled": None}


def test_adding_through_a_patch_ticks_the_box():
    plan = w.plan_changes([], [A], [], None, enabled_before=False)
    assert plan["want_enabled"] is True


def test_removing_through_a_patch_leaves_the_box_alone():
    plan = w.plan_changes([A], [], [A], None, enabled_before=False)
    assert plan == {"remove_idx": [0], "add": [], "want_enabled": None}


def test_a_read_changes_nothing():
    assert w.plan_changes([A, "junk"], [], [], None, enabled_before=False) == {
        "remove_idx": [], "add": [], "want_enabled": None}


def test_removals_run_from_the_bottom_so_indexes_stay_valid():
    plan = w.plan_changes([A, B, C, "x"], [], [], [B], enabled_before=True)
    assert plan["remove_idx"] == [3, 2, 0]


# ── one dialog session, with a fake list ──────────────────────────────


class _FakeList:
    """The dialog's list: rows as the driver reads them, plus the add row."""

    def __init__(self, rows, refuse=(), lose_dialog_on_add=False):
        self.rows_now = list(rows)
        self.refuse = set(refuse)
        self.lose_dialog_on_add = lose_dialog_on_add
        self.reads = 0
        self.seen = ["Edit"]

    def rows(self):
        self.reads += 1
        return list(self.rows_now)

    def remove(self, idx):
        del self.rows_now[idx]

    def add(self, url):
        if self.lose_dialog_on_add:
            raise w.OptionsError("options_closed_unexpectedly", "gone")
        if url in self.refuse:
            raise w.OptionsError("list_not_changed", "the WebRequest list did not grow")
        self.rows_now.append(url)


@pytest.fixture
def session(monkeypatch):
    state = {"box": True, "pressed": [], "list": None}

    class _Remote:
        def __init__(self, pid):
            pass

        def close(self):
            pass

    def fake_send(hwnd, msg, wparam=0, lparam=0, timeout_ms=0):
        if msg == w.BM_GETCHECK:
            return w.BST_CHECKED if state["box"] else w.BST_UNCHECKED
        return 0

    def fake_set_box(page, box, checked):
        state["box"] = checked

    def fake_press(sheet, ok):
        state["pressed"].append("OK" if ok else "Cancel")
        return True

    monkeypatch.setattr(w, "_find_page", lambda sheet: (11, 12, 13))
    monkeypatch.setattr(w, "_Remote", _Remote)
    monkeypatch.setattr(w, "_UrlList", lambda lv, sheet, pid, remote: state["list"])
    monkeypatch.setattr(w, "_send", fake_send)
    monkeypatch.setattr(w, "_set_box", fake_set_box)
    monkeypatch.setattr(w, "_press", fake_press)

    def run(rows, add=(), remove=(), exact=None, partial=False, box=True, **list_kw):
        state["box"] = box
        state["list"] = _FakeList(rows, **list_kw)
        result = w._session(4242, 10, list(add), list(remove), exact, partial)
        return result, state

    return run


def test_ok_is_pressed_only_when_every_change_took(session):
    result, state = session([A, B], exact=[A, C])

    assert result["urls"] == [A, C]
    assert result["removed"] == [B] and result["added"] == [C]
    assert state["pressed"] == ["OK"]
    assert state["list"].reads == 2  # one read, one verifying read


def test_a_change_that_did_not_take_cancels_the_whole_session(session):
    with pytest.raises(w.OptionsError) as err:
        session([A], exact=[A, B], refuse={B})

    assert err.value.code == "list_not_changed"


def test_cancel_is_pressed_when_a_change_fails(session, monkeypatch):
    pressed = []
    monkeypatch.setattr(w, "_press", lambda sheet, ok: pressed.append(ok) or True)

    with pytest.raises(w.OptionsError):
        session([A], exact=[A, B], refuse={B})

    assert pressed == [False]


def test_partial_skips_a_refused_url_and_still_saves_the_rest(session):
    result, state = session([A], exact=[B, C], partial=True, refuse={B})

    assert result["urls"] == [C]
    assert result["rejected"] == [B]
    assert state["pressed"] == ["OK"]


def test_partial_never_swallows_a_dialog_that_closed_mid_edit(session):
    with pytest.raises(w.OptionsError) as err:
        session([], exact=[A], partial=True, lose_dialog_on_add=True)

    assert err.value.code == "options_closed_unexpectedly"


def test_a_matching_list_with_the_box_unticked_is_ticked_and_saved(session):
    result, state = session([A], exact=[A], box=False)

    assert result["changed"] is True and result["enabled"] is True
    assert state["box"] is True
    assert state["pressed"] == ["OK"]


def test_a_read_presses_cancel_and_reports_only_url_rows(session):
    result, state = session([A, "junk"])

    assert result == {"enabled": True, "urls": [A], "added": [], "removed": [],
                      "rejected": [], "changed": False}
    assert state["pressed"] == ["Cancel"]


def test_a_box_that_will_not_tick_fails_the_session(session, monkeypatch):
    monkeypatch.setattr(w, "_set_box", lambda page, box, checked: None)

    with pytest.raises(w.OptionsError) as err:
        session([], exact=[A], box=False)

    assert err.value.diag["enabled"] is False


# ── the dialog vanishing in the middle of an edit ─────────────────────


class _User32:
    def __init__(self, alive):
        self.alive = alive

    def IsWindow(self, hwnd):  # noqa: N802 - mirrors the Win32 name
        return self.alive


def test_a_dialog_gone_after_an_edit_is_reported_as_such(monkeypatch):
    monkeypatch.setattr(w, "_user32", _User32(alive=False))
    lst = w._UrlList(lv=13, sheet=10, pid=4242, remote=None)

    with pytest.raises(w.OptionsError) as err:
        lst._alive()
    assert err.value.code == "options_closed_unexpectedly"


def test_a_dialog_still_open_passes(monkeypatch):
    monkeypatch.setattr(w, "_user32", _User32(alive=True))

    w._UrlList(lv=13, sheet=10, pid=4242, remote=None)._alive()


def test_unticking_happens_after_the_rows_are_removed(session, monkeypatch):
    order = []
    monkeypatch.setattr(w, "_set_box", lambda page, box, checked: order.append(("box", checked)))

    class _Recording(_FakeList):
        def remove(self, idx):
            order.append(("remove", idx))
            super().remove(idx)

    def run():
        state = {"box": True}
        monkeypatch.setattr(w, "_UrlList", lambda lv, sheet, pid, remote: lst)
        monkeypatch.setattr(w, "_send", lambda h, m, wp=0, lp=0, timeout_ms=0:
                            (w.BST_UNCHECKED if order and order[-1] == ("box", False)
                             else w.BST_CHECKED) if m == w.BM_GETCHECK else 0)
        return w._session(4242, 10, [], [], [], False), state

    lst = _Recording([A, B])
    run()

    assert order == [("remove", 1), ("remove", 0), ("box", False)]


def test_a_list_longer_than_the_cap_is_refused_not_read_in_part(monkeypatch):
    lst = w._UrlList(lv=13, sheet=10, pid=4242, remote=None)
    monkeypatch.setattr(lst, "count", lambda: w._MAX_ROWS + 5)

    with pytest.raises(w.OptionsError) as err:
        lst.rows()
    assert err.value.code == "list_too_long"
