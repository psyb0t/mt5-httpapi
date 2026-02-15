"""
Signal detection. Takes a DataFrame with indicators already added
and returns a list of signal strings.
"""

import pandas as pd


def detect_signals(df):
    """Check the last two candles for common signals."""
    signals = []
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # RSI
    if pd.notna(latest.get("rsi")):
        if latest["rsi"] > 70:
            signals.append("RSI overbought (>70)")
        elif latest["rsi"] < 30:
            signals.append("RSI oversold (<30)")

    # MFI
    if pd.notna(latest.get("mfi")):
        if latest["mfi"] > 80:
            signals.append("MFI overbought (>80)")
        elif latest["mfi"] < 20:
            signals.append("MFI oversold (<20)")

    # MACD histogram crossover
    hist_key = "MACDh_12_26_9"
    if pd.notna(latest.get(hist_key)) and pd.notna(prev.get(hist_key)):
        if prev[hist_key] < 0 and latest[hist_key] > 0:
            signals.append("MACD histogram crossed above zero (bullish)")
        elif prev[hist_key] > 0 and latest[hist_key] < 0:
            signals.append("MACD histogram crossed below zero (bearish)")

    # EMA/SMA crossover
    if pd.notna(latest.get("ema_21")) and pd.notna(latest.get("sma_50")):
        if pd.notna(prev.get("ema_21")) and pd.notna(prev.get("sma_50")):
            if prev["ema_21"] <= prev["sma_50"] and latest["ema_21"] > latest["sma_50"]:
                signals.append("EMA 21 crossed above SMA 50 (golden cross)")
            elif prev["ema_21"] >= prev["sma_50"] and latest["ema_21"] < latest["sma_50"]:
                signals.append("EMA 21 crossed below SMA 50 (death cross)")
        if latest["ema_21"] > latest["sma_50"]:
            signals.append("EMA 21 above SMA 50 (bullish trend)")
        else:
            signals.append("EMA 21 below SMA 50 (bearish trend)")

    # Bollinger Bands
    bbu = latest.get("BBU_20_2.0")
    bbl = latest.get("BBL_20_2.0")
    if pd.notna(bbu):
        if latest["close"] > bbu:
            signals.append("Price above upper Bollinger Band")
        elif latest["close"] < bbl:
            signals.append("Price below lower Bollinger Band")

    # Stochastic
    stoch_k = latest.get("STOCHk_14_3_3")
    stoch_d = latest.get("STOCHd_14_3_3")
    if pd.notna(stoch_k):
        if stoch_k > 80 and stoch_d > 80:
            signals.append("Stochastic overbought (>80)")
        elif stoch_k < 20 and stoch_d < 20:
            signals.append("Stochastic oversold (<20)")

    # ADX trend strength
    adx_val = latest.get("ADX_14")
    if pd.notna(adx_val):
        if adx_val > 25:
            signals.append(f"Strong trend (ADX {adx_val:.1f})")
        else:
            signals.append(f"Weak/no trend (ADX {adx_val:.1f})")

    return signals
