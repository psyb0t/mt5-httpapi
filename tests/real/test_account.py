"""Account state — balance/equity/margin must be readable + positive on a demo."""


def test_get_account(client):
    acct = client.get("/account")
    assert isinstance(acct, dict)
    for key in ("login", "server", "currency", "balance", "equity", "margin_free"):
        assert key in acct, f"/account missing key: {key}"
    assert acct["balance"] > 0, f"demo account drained: balance={acct['balance']}"
    assert acct["equity"] > 0
    assert acct["margin_free"] >= 0
