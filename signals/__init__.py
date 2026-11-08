"""Signal copier: turns trade signals from Telegram channels and other feeds
into orders on the configured MT5 terminals.

Runs as its own Linux container beside the Windows VM and reaches the
terminals over HTTP through nginx. Like mcpunifier, it never imports mt5api.
"""
