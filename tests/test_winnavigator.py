"""The Navigator refresh driver's logic (chartctl/winnavigator.py), Win32 faked."""
from __future__ import annotations

import pytest

from mt5api.chartctl import winnavigator as nav
from mt5api.chartctl import winoptions as w


def test_tvitem_is_56_bytes_on_64_bit_and_40_on_32_bit():
    assert len(nav.pack_tvitem(False, 0x1000, 0x2000)) == 56
    assert len(nav.pack_tvitem(True, 0x1000, 0x2000)) == 40


def test_configured_labels_come_first_and_english_is_always_tried():
    assert nav.labels_with_default(["Fake Node", " "], "Expert Advisors") == (
        "Fake Node", "Expert Advisors")
    assert nav.labels_with_default(["expert advisors"], "Expert Advisors") == ("expert advisors",)
    assert nav.labels_with_default(None, "Refresh") == ("Refresh",)


def test_refresh_is_found_by_label_or_id_and_separators_take_no_down_press():
    menu = [("New AI Assistant", 33417), ("", 0), ("Buy from the Market", 33065),
            ("Order your own Program", 33710), ("Create in MetaEditor", 33051),
            ("Set Hotkey", 32972), ("", 0), ("Refresh", 33416)]

    assert nav.refresh_position(menu, ("Refresh",)) == (7, 6)
    renamed = [(t if i != 33416 else "Something else", i) for t, i in menu]
    assert nav.refresh_position(renamed, ("Refresh",)) == (7, 6)  # by id
    assert nav.refresh_position([("Other", 1)], ("Refresh",)) == (-1, 0)


class _FakeTree:
    """A Navigator whose expert shows up after `appears_after` lookups."""

    def __init__(self, listed_from: int):
        self.tree = 900
        self.lookups = 0
        self.listed_from = listed_from

    def find(self, _start, _path):
        self.lookups += 1
        return 1 if self.lookups >= self.listed_from else 0


@pytest.fixture
def driven(monkeypatch):
    posted = []
    state = {"tree": _FakeTree(listed_from=99)}

    class _Remote:
        def __init__(self, pid):
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(w, "_load", lambda: None)
    monkeypatch.setattr(w, "main_window", lambda pid: 500)
    monkeypatch.setattr(w, "_Remote", _Remote)
    monkeypatch.setattr(w, "_post", lambda hwnd, msg, wp=0, lp=0: posted.append((hwnd, msg, wp)))
    monkeypatch.setattr(w, "_wait", lambda pred, timeout, step=0.2: pred())
    monkeypatch.setattr(w, "_class", lambda hwnd: "Afx:ControlBar:test")
    monkeypatch.setattr(w, "_user32", type("U", (), {"GetParent": staticmethod(lambda h: 800)})())
    monkeypatch.setattr(nav, "_navigator", lambda pid, remote, labels: (state["tree"], 1, 500))
    monkeypatch.setattr(nav, "_close_menus", lambda pid: None)
    variant_c = []
    monkeypatch.setattr(nav, "_variant_c", lambda *a, **kw: variant_c.append(a))
    return {"posted": posted, "state": state, "variant_c": variant_c}


def test_an_expert_listed_before_the_refresh_is_reported_already_listed(driven):
    driven["state"]["tree"] = _FakeTree(listed_from=1)

    result = nav.refresh(4242, ["Uploaded", "EA"])

    assert result["found"] is True and result["already_listed"] is True
    assert driven["posted"] == [(800, w.WM_COMMAND, nav.REFRESH_ID)]  # variant a still sent
    assert driven["variant_c"] == []


def test_variant_a_is_reported_when_the_expert_then_shows_up(driven):
    driven["state"]["tree"] = _FakeTree(listed_from=2)

    result = nav.refresh(4242, ["Uploaded", "EA"])

    assert result["variant"] == "a" and result["found"] is True
    assert result["already_listed"] is False
    assert driven["variant_c"] == []


def test_variant_c_runs_when_a_did_not_list_the_expert(driven):
    driven["state"]["tree"] = _FakeTree(listed_from=3)

    result = nav.refresh(4242, ["Uploaded", "EA"])

    assert result["variant"] == "c" and result["tried"] == ["a", "c"]
    assert len(driven["variant_c"]) == 1


def test_an_expert_never_listed_is_not_found(driven):
    result = nav.refresh(4242, ["Uploaded", "EA"])

    assert result["found"] is False and result["tried"] == ["a", "c"]


def test_no_main_window_is_an_error(driven, monkeypatch):
    monkeypatch.setattr(w, "main_window", lambda pid: 0)

    with pytest.raises(w.OptionsError) as err:
        nav.refresh(4242, ["Uploaded", "EA"])
    assert err.value.code == "window_not_found"


# ── chartctl.navigator_labels in config.yaml ──────────────────────────


def test_navigator_labels_are_read_as_lists():
    from mt5api.config import navigator_labels

    assert navigator_labels({"expert_advisors": ["Fake Node"], "refresh": "Fake Refresh"}) == {
        "expert_advisors": ["Fake Node"], "refresh": ["Fake Refresh"]}
    assert navigator_labels(None) == {"expert_advisors": [], "refresh": []}


@pytest.mark.parametrize("raw", ["Fake Node", {"expert_advisors": [1, 2]}, {"refresh": {"a": 1}}])
def test_malformed_navigator_labels_fall_back_to_english(raw, caplog):
    from mt5api.config import navigator_labels

    labels = navigator_labels(raw)

    assert all(value == [] for value in labels.values())
    assert "navigator_labels" in caplog.text
