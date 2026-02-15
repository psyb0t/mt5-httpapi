"""
Indicator functions. Each one takes a DataFrame with OHLCV columns
and returns it with the indicator columns added.
"""

import pandas as pd
import pandas_ta as ta
from smartmoneyconcepts import smc


def add_moving_averages(df, lengths=None):
    """EMA 21 + SMA 50/100/200 (or custom lengths)."""
    if lengths is None:
        lengths = [50, 100, 200]
    df["ema_21"] = ta.ema(df["close"], length=21)
    for length in lengths:
        df[f"sma_{length}"] = ta.sma(df["close"], length=length)
    return df


def add_atr(df, length=14):
    """Average True Range."""
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=length)
    return df


def add_rsi(df, length=14):
    """Relative Strength Index."""
    df["rsi"] = ta.rsi(df["close"], length=length)
    return df


def add_macd(df, fast=12, slow=26, signal=9):
    """MACD, signal line, and histogram."""
    macd = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
    return pd.concat([df, macd], axis=1)


def add_bollinger_bands(df, length=20, std=2):
    """Bollinger Bands (upper, middle, lower)."""
    bbands = ta.bbands(df["close"], length=length, std=std)
    return pd.concat([df, bbands], axis=1)


def add_mfi(df, length=14):
    """Money Flow Index. Uses tick_volume as volume proxy."""
    df["mfi"] = ta.mfi(df["high"], df["low"], df["close"], df["tick_volume"], length=length)
    return df


def add_stochastic(df, k=14, d=3, smooth_k=3):
    """Stochastic oscillator (%K and %D)."""
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=k, d=d, smooth_k=smooth_k)
    return pd.concat([df, stoch], axis=1)


def add_adx(df, length=14):
    """Average Directional Index."""
    adx = ta.adx(df["high"], df["low"], df["close"], length=length)
    return pd.concat([df, adx], axis=1)


def add_vwap(df):
    """Volume Weighted Average Price. Uses tick_volume as volume proxy."""
    df_idx = df.set_index(pd.to_datetime(df["time"], unit="s"))
    df["vwap"] = ta.vwap(df_idx["high"], df_idx["low"], df_idx["close"], df_idx["tick_volume"]).values
    return df


def add_smc(df, swing_length=10):
    """Smart Money Concepts: order blocks, FVGs, BOS/CHoCH, liquidity."""
    df["volume"] = df["tick_volume"]
    swing_hl = smc.swing_highs_lows(df, swing_length=swing_length)
    df["swing_hl"] = swing_hl["HighLow"]
    df["swing_level"] = swing_hl["Level"]

    ob = smc.ob(df, swing_hl)
    df["ob"] = ob["OB"]
    df["ob_top"] = ob["Top"]
    df["ob_bottom"] = ob["Bottom"]
    df["ob_volume"] = ob["OBVolume"]
    df["ob_mitigated"] = ob["MitigatedIndex"]

    fvg = smc.fvg(df)
    df["fvg"] = fvg["FVG"]
    df["fvg_top"] = fvg["Top"]
    df["fvg_bottom"] = fvg["Bottom"]
    df["fvg_mitigated"] = fvg["MitigatedIndex"]

    bos_choch = smc.bos_choch(df, swing_hl)
    df["bos"] = bos_choch["BOS"]
    df["choch"] = bos_choch["CHOCH"]
    df["bos_choch_level"] = bos_choch["Level"]
    df["bos_choch_broken_idx"] = bos_choch["BrokenIndex"]

    liq = smc.liquidity(df, swing_hl)
    df["liquidity"] = liq["Liquidity"]
    df["liquidity_level"] = liq["Level"]
    df["liquidity_end"] = liq["End"]
    df["liquidity_swept"] = liq["Swept"]

    return df


def add_all(df):
    """Slap every indicator on the DataFrame."""
    df = add_moving_averages(df)
    df = add_atr(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_bollinger_bands(df)
    df = add_mfi(df)
    df = add_stochastic(df)
    df = add_adx(df)
    df = add_vwap(df)
    df = add_smc(df)
    return df
