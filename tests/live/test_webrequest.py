"""The WebRequest allowlist tools on the running stack.

Reading is always safe. Changing the list drives the terminal's Options
dialog (or restarts a bare-metal terminal), so it only runs with
MT5_LIVE_WEBREQUEST=1, on a demo account unless MT5_LIVE_ALLOW_REAL=1, and
puts the original list back.
"""
from __future__ import annotations

import pytest

from tests.live.conftest import flag

TEST_URL = "https://livetest.example.com"


@pytest.fixture(scope="module")
def webrequest(chartctl_enabled, mcp_unified):
    if not chartctl_enabled:
        pytest.skip("the WebRequest tools need Chart Deployments on the target terminal")
    return mcp_unified


def test_the_allowlist_reads_as_a_list(webrequest):
    assert isinstance(webrequest.call_json("get_webrequest")["urls"], list)


def test_adding_and_removing_a_url_round_trips(webrequest, state_changes_allowed):
    if not flag("MT5_LIVE_WEBREQUEST"):
        pytest.skip("set MT5_LIVE_WEBREQUEST=1 to change the allowlist")
    # Patches only: a patch keeps every other entry the terminal holds, and a
    # full replace with the list stored before would delete URLs added by hand
    # in the VM, which a patch adopts into the stored list.
    try:
        added = webrequest.call_json("set_webrequest", add=[TEST_URL])
        assert added["success"] is True
        assert TEST_URL in added["urls"]

        removed = webrequest.call_json("set_webrequest", remove=[TEST_URL])
        assert TEST_URL not in removed["urls"]
    finally:
        if TEST_URL in webrequest.call_json("get_webrequest")["urls"]:
            webrequest.call_json("set_webrequest", remove=[TEST_URL])

    assert TEST_URL not in webrequest.call_json("get_webrequest")["urls"]
