"""Unified MCP front end for every configured MT5 terminal.

Runs as its own Linux container beside the Windows VM that hosts the terminals.
Each tool takes broker/account naming which terminal to act on, and dispatches
over HTTP to that terminal's own mt5api process. The per-terminal
``/<broker>/<account>/mcp`` endpoints are unaffected.
"""
