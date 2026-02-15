#!/usr/bin/env python3
"""
Candlestick chart with TA overlays — outputs a 1920x1080 PNG.

Usage:
    python chart.py ADAUSD
    python chart.py BTCUSD H1 100
    python chart.py EURUSD D1 200 -o eurusd.png

Requirements:
    pip install -r requirements.txt
"""

import argparse
import sys

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from api import get_candles
from indicators import add_all

# chart colors
COLORS = {
    "bg": "#1a1a2e",
    "face": "#16213e",
    "grid": "#2a2a4a",
    "text": "#e0e0e0",
    "up": "#00e676",
    "down": "#ff1744",
    "ema21": "#ffeb3b",
    "sma50": "#2196f3",
    "sma100": "#ff9800",
    "sma200": "#e91e63",
    "bb_edge": "#7c4dff",
    "bb_fill": "#7c4dff",
    "vwap": "#00bcd4",
    "macd": "#2196f3",
    "macd_signal": "#ff9800",
    "macd_hist_up": "#00e676",
    "macd_hist_down": "#ff1744",
    "rsi": "#ffeb3b",
    "rsi_ob": "#ff1744",
    "rsi_os": "#00e676",
    "vol_up": "#00e67640",
    "vol_down": "#ff174440",
}


def build_chart(df, symbol, timeframe, output):
    df_plot = df.copy()
    df_plot.index = pd.DatetimeIndex(df_plot["datetime"])
    df_plot = df_plot.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "tick_volume": "Volume",
    })

    # add padding candles on the right for visual breathing room
    freq = pd.infer_freq(df_plot.index) or df_plot.index[-1] - df_plot.index[-2]
    pad_count = max(3, len(df_plot) // 10)
    pad_idx = pd.date_range(
        start=df_plot.index[-1] + pd.tseries.frequencies.to_offset(freq),
        periods=pad_count,
        freq=freq,
    ) if isinstance(freq, str) else pd.date_range(
        start=df_plot.index[-1] + freq,
        periods=pad_count,
        freq=freq,
    )
    pad_df = pd.DataFrame(index=pad_idx, columns=df_plot.columns, dtype=float)
    df_plot = pd.concat([df_plot, pad_df])
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df_plot.columns:
            df_plot[col] = pd.to_numeric(df_plot[col], errors="coerce")

    # mplfinance style
    mc = mpf.make_marketcolors(
        up=COLORS["up"], down=COLORS["down"],
        edge={"up": COLORS["up"], "down": COLORS["down"]},
        wick={"up": COLORS["up"], "down": COLORS["down"]},
        volume={"up": COLORS["vol_up"], "down": COLORS["vol_down"]},
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        facecolor=COLORS["face"],
        figcolor=COLORS["bg"],
        gridcolor=COLORS["grid"],
        gridstyle="--",
        rc={
            "font.size": 9,
            "axes.labelcolor": COLORS["text"],
            "xtick.color": COLORS["text"],
            "ytick.color": COLORS["text"],
        },
    )

    # helper: only add a plot if the series has actual data
    addplots = []

    def has_data(col):
        return col in df_plot.columns and df_plot[col].notna().any()

    # moving averages
    for col, color in [
        ("ema_21", COLORS["ema21"]), ("sma_50", COLORS["sma50"]),
        ("sma_100", COLORS["sma100"]), ("sma_200", COLORS["sma200"]),
    ]:
        if has_data(col):
            addplots.append(mpf.make_addplot(
                df_plot[col], color=color, width=1,
            ))

    # bollinger bands
    bbu_col = next((c for c in df_plot.columns if c.startswith("BBU_")), None)
    bbl_col = next((c for c in df_plot.columns if c.startswith("BBL_")), None)
    if bbu_col and has_data(bbu_col) and bbl_col and has_data(bbl_col):
        addplots.append(mpf.make_addplot(
            df_plot[bbu_col], color=COLORS["bb_edge"], width=0.8, linestyle="--",
        ))
        addplots.append(mpf.make_addplot(
            df_plot[bbl_col], color=COLORS["bb_edge"], width=0.8, linestyle="--",
        ))

    # vwap
    if has_data("vwap"):
        addplots.append(mpf.make_addplot(
            df_plot["vwap"], color=COLORS["vwap"], width=1, linestyle=":",
        ))

    # RSI panel
    has_rsi = has_data("rsi")
    if has_rsi:
        rsi_mask = df_plot["rsi"].notna()
        ob_line = pd.Series(float("nan"), index=df_plot.index)
        os_line = pd.Series(float("nan"), index=df_plot.index)
        ob_line[rsi_mask] = 70
        os_line[rsi_mask] = 30
        addplots.append(mpf.make_addplot(
            df_plot["rsi"], panel=2, color=COLORS["rsi"], width=1, ylabel="RSI",
        ))
        addplots.append(mpf.make_addplot(
            ob_line, panel=2, color=COLORS["rsi_ob"], width=0.5, linestyle="--",
        ))
        addplots.append(mpf.make_addplot(
            os_line, panel=2, color=COLORS["rsi_os"], width=0.5, linestyle="--",
        ))

    # MACD panel
    macd_col = "MACD_12_26_9"
    signal_col = "MACDs_12_26_9"
    hist_col = "MACDh_12_26_9"
    has_macd = has_data(macd_col)
    if has_macd:
        addplots.append(mpf.make_addplot(
            df_plot[macd_col], panel=3, color=COLORS["macd"], width=1, ylabel="MACD",
        ))
        addplots.append(mpf.make_addplot(
            df_plot[signal_col], panel=3, color=COLORS["macd_signal"], width=1,
        ))
        hist_colors = [
            COLORS["macd_hist_up"] if v >= 0 else COLORS["macd_hist_down"]
            for v in df_plot[hist_col].fillna(0)
        ]
        addplots.append(mpf.make_addplot(
            df_plot[hist_col], panel=3, type="bar", color=hist_colors, width=0.7,
        ))

    # draw it
    fig, axes = mpf.plot(
        df_plot,
        type="candle",
        style=style,
        volume=True,
        volume_panel=1,
        addplot=addplots if addplots else None,
        figsize=(19.2, 10.8),
        tight_layout=True,
        returnfig=True,
        panel_ratios=(
            (5, 1, 1.5, 1.5) if has_rsi and has_macd else
            (5, 1, 1.5) if has_rsi or has_macd else
            (5, 1)
        ),
    )

    # title
    latest = df.iloc[-1]
    price = latest["close"]
    atr = latest.get("atr")
    rsi = latest.get("rsi")

    title_parts = [f"{symbol} {timeframe}  —  {price}"]
    if pd.notna(atr):
        title_parts.append(f"ATR: {atr:.6f}")
    if pd.notna(rsi):
        title_parts.append(f"RSI: {rsi:.1f}")
    title = "    |    ".join(title_parts)

    fig.suptitle(title, color=COLORS["text"], fontsize=14, fontweight="bold", y=0.98)

    # legend on price panel
    ax_price = axes[0]
    legend_items = []
    legend_colors = []
    if "ema_21" in df_plot.columns:
        legend_items.append("EMA 21")
        legend_colors.append(COLORS["ema21"])
    if "sma_50" in df_plot.columns:
        legend_items.append("SMA 50")
        legend_colors.append(COLORS["sma50"])
    if "sma_100" in df_plot.columns:
        legend_items.append("SMA 100")
        legend_colors.append(COLORS["sma100"])
    if "sma_200" in df_plot.columns:
        legend_items.append("SMA 200")
        legend_colors.append(COLORS["sma200"])
    if bbu_col:
        legend_items.append("BB")
        legend_colors.append(COLORS["bb_edge"])
    if "vwap" in df_plot.columns:
        legend_items.append("VWAP")
        legend_colors.append(COLORS["vwap"])

    if legend_items:
        from matplotlib.lines import Line2D
        handles = [Line2D([0], [0], color=c, linewidth=1.5) for c in legend_colors]
        ax_price.legend(handles, legend_items, loc="upper left",
                       fontsize=8, facecolor=COLORS["face"], edgecolor=COLORS["grid"],
                       labelcolor=COLORS["text"])

    fig.savefig(output, dpi=100, facecolor=COLORS["bg"],
                bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print(f"Saved: {output}")


def main():
    parser = argparse.ArgumentParser(description="Candlestick chart with TA overlays")
    parser.add_argument("symbol", nargs="?", default="EURUSD", help="Symbol (default: EURUSD)")
    parser.add_argument("timeframe", nargs="?", default="H4", help="Timeframe (default: H4)")
    parser.add_argument("count", nargs="?", type=int, default=200, help="Candle count (default: 200)")
    parser.add_argument("-o", "--output", default=None, help="Output filename (default: SYMBOL_TF.png)")
    args = parser.parse_args()

    output = args.output or f"{args.symbol}_{args.timeframe}.png"

    print(f"Fetching {args.count} {args.timeframe} candles for {args.symbol}...")
    df = get_candles(args.symbol, args.timeframe, args.count)
    if df is None:
        print(f"No data for {args.symbol}")
        sys.exit(1)

    df = add_all(df)
    build_chart(df, args.symbol, args.timeframe, output)


if __name__ == "__main__":
    main()
