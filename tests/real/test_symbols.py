"""Symbol discovery + per-symbol info + tick."""


def test_list_symbols(client, config):
    syms = client.get("/symbols")
    assert isinstance(syms, list) and len(syms) > 0
    assert config["symbol"] in syms, f"{config['symbol']} not in symbol list"


def test_symbol_info(symbol_info, config):
    assert symbol_info["name"] == config["symbol"]
    assert symbol_info["volume_min"] > 0
    assert symbol_info["volume_step"] > 0
    assert symbol_info["digits"] >= 0
    assert symbol_info["ask"] > 0 and symbol_info["bid"] > 0
    assert symbol_info["ask"] >= symbol_info["bid"], "ask must be >= bid"


def test_volume_min_meets_config(symbol_info, config):
    """If the broker raises volume_min above our configured volume, fail fast
    so the user knows to bump MT5_TEST_VOLUME — otherwise every order test
    will error with 'invalid volume' and the failure will be obscure."""
    assert config["volume"] >= symbol_info["volume_min"], (
        f"MT5_TEST_VOLUME={config['volume']} below broker minimum "
        f"{symbol_info['volume_min']} for {config['symbol']}"
    )


def test_current_tick(current_tick):
    assert current_tick["ask"] > 0
    assert current_tick["bid"] > 0
    assert current_tick["time"] > 0
