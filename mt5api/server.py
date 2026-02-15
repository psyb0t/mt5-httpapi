from flask import Flask

from mt5api.handlers import terminal, account, symbols, positions, orders, history

app = Flask(__name__)

# ── Health / System ──────────────────────────────────────────────
app.get("/ping")(terminal.ping)
app.get("/error")(terminal.last_error)

# ── Terminal ─────────────────────────────────────────────────────
app.get("/terminal")(terminal.get_terminal)
app.post("/terminal/init")(terminal.init)
app.post("/terminal/shutdown")(terminal.shutdown)

# ── Account ──────────────────────────────────────────────────────
app.get("/account")(account.get_account)
app.get("/account/list")(account.list_accounts)
app.post("/account/login")(account.login)
app.post("/account/login/<name>")(account.login)

# ── Symbols ──────────────────────────────────────────────────────
app.get("/symbols")(symbols.list_symbols)
app.get("/symbols/<symbol>")(symbols.get_symbol)
app.get("/symbols/<symbol>/tick")(symbols.get_tick)
app.get("/symbols/<symbol>/rates")(symbols.get_rates)
app.get("/symbols/<symbol>/ticks")(symbols.get_ticks)

# ── Positions ────────────────────────────────────────────────────
app.get("/positions")(positions.list_positions)
app.get("/positions/<int:ticket>")(positions.get_position)
app.put("/positions/<int:ticket>")(positions.update_position)
app.delete("/positions/<int:ticket>")(positions.close_position)

# ── Orders ───────────────────────────────────────────────────────
app.get("/orders")(orders.list_orders)
app.post("/orders")(orders.create_order)
app.get("/orders/<int:ticket>")(orders.get_order)
app.put("/orders/<int:ticket>")(orders.update_order)
app.delete("/orders/<int:ticket>")(orders.cancel_order)

# ── History ──────────────────────────────────────────────────────
app.get("/history/orders")(history.get_orders)
app.get("/history/deals")(history.get_deals)
