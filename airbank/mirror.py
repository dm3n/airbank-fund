"""Mirror accounts: point Airbank at an external portfolio — a public crypto
wallet, an Alpaca brokerage, or any holdings file — and the fund replicates
it into its own book at real market prices, scaled to your bankroll, then
manages it: marks, P&L, kill switch, analysts, chat (contract section J).

Sources:
  wallet — BTC/ETH public address(es), balances from chain explorers
  alpaca — the connected Alpaca account's positions
  file   — ~/.airbank/mirror.json: {"holdings": [{"symbol": "BTC/USD",
           "asset_class": "crypto", "qty": 0.5} | {"symbol": "AAPL",
           "asset_class": "equity", "weight": 0.25}, …]}
           The universal escape hatch: export ANY account to this file.
"""
import json
import urllib.request

from . import config, data
from .state import log

MIRROR_FILE = config.HOME_DIR / "mirror.json"
REBALANCE_THRESHOLD = 0.01     # ignore drifts under 1% of equity
MIN_TRADE_USD = 10.0

FILE_TEMPLATE = {"holdings": [
    {"symbol": "BTC/USD", "asset_class": "crypto", "weight": 0.5},
    {"symbol": "AAPL", "asset_class": "equity", "weight": 0.3},
]}


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 airbank"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode()


def _wallet_holdings(spec):
    chain = spec.get("chain")
    addresses = spec.get("addresses") or [spec.get("address")]
    total = 0.0
    for address in filter(None, addresses):
        if chain == "btc":
            total += float(_fetch(
                f"https://blockchain.info/q/addressbalance/{address}")) / 1e8
        elif chain == "eth":
            info = json.loads(_fetch(
                f"https://api.ethplorer.io/getAddressInfo/{address}?apiKey=freekey"))
            total += float(info["ETH"]["balance"])
    symbol = "BTC/USD" if chain == "btc" else "ETH/USD"
    return ([{"symbol": symbol, "asset_class": "crypto", "qty": total}],
            f"{chain.upper()} wallet {addresses[0][:10]}…")


def _alpaca_holdings():
    from . import broker as alpaca
    holds = []
    for p in alpaca.positions():
        crypto = p.get("asset_class") == "crypto"
        symbol = p["symbol"]
        if crypto and "/" not in symbol and symbol.endswith("USD"):
            symbol = symbol[:-3] + "/USD"
        holds.append({"symbol": symbol,
                      "asset_class": "crypto" if crypto else "equity",
                      "qty": float(p["qty"])})
    return holds, "Alpaca account"


def _file_holdings():
    raw = json.loads(MIRROR_FILE.read_text())
    return list(raw.get("holdings", [])), f"holdings file {MIRROR_FILE.name}"


def write_template():
    MIRROR_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MIRROR_FILE.exists():
        MIRROR_FILE.write_text(json.dumps(FILE_TEMPLATE, indent=2) + "\n")
    return MIRROR_FILE


def holdings():
    """-> (holdings list, human description of the source)."""
    spec = config.ACCOUNT.get("mirror", {})
    source = spec.get("source")
    if source == "wallet":
        return _wallet_holdings(spec)
    if source == "alpaca":
        return _alpaca_holdings()
    if source == "file":
        return _file_holdings()
    raise RuntimeError(f"unknown mirror source {source!r}")


def target_weights(holds, price_fn):
    """Value-weight the source portfolio. Explicit `weight` entries win;
    `qty` entries are priced live. Normalized down if they exceed 1.0."""
    priced = []
    for h in holds:
        if h.get("weight") is not None:
            priced.append((h, float(h["weight"]), None))
        elif h.get("qty"):
            price = price_fn(h["symbol"], h["asset_class"])
            priced.append((h, float(h["qty"]) * price, price))
    qty_total = sum(v for h, v, p in priced if p is not None)
    explicit = sum(v for h, v, p in priced if p is None)
    scale = max(1.0 - explicit, 0.0) / qty_total if qty_total else 0.0
    weights = {}
    prices = {}
    for h, v, price in priced:
        key = (h["symbol"], h["asset_class"])
        weights[key] = weights.get(key, 0.0) + (v if price is None else v * scale)
        if price is not None:
            prices[key] = price
    total = sum(weights.values())
    if total > 1.0:
        weights = {k: w / total for k, w in weights.items()}
    return weights, prices


def sync(broker, cycle, price_fn=None):
    """Rebalance the mirror book toward the source weights. Mechanical —
    replication trades bypass the analyst gate but stay long-only, unlevered,
    inside the bankroll. Returns number of rebalance trades."""
    price_fn = price_fn or (lambda s, a: data.latest_price(s, a)[0])
    holds, desc = holdings()
    weights, prices = target_weights(holds, price_fn)
    cycle["mirror_source"] = desc

    def price_of(symbol, asset_class):
        key = (symbol, asset_class)
        if key not in prices:
            prices[key] = price_fn(symbol, asset_class)
        return prices[key]

    # mark every held position so equity is honest before rebalancing
    book_keys = {(s, a): s.replace("/", "") for s, a in weights}
    for key, book_key in book_keys.items():
        broker.mark(book_key, price_of(*key))
    held = dict(broker.book["positions"])
    equity = broker.equity()
    trades = 0

    # close anything the source no longer holds
    target_book_keys = set(book_keys.values())
    for book_key, pos in held.items():
        if book_key not in target_book_keys:
            broker.submit_order({"symbol": book_key, "side": "sell",
                                 "notional_usd": pos["qty"] * pos["last_price"],
                                 "asset_class": "crypto", "price": pos["last_price"]},
                                None)
            log("mirror", f"closed {book_key} (left the source portfolio)")
            trades += 1

    # rebalance toward target weights
    for key, weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        symbol, asset_class = key
        book_key = book_keys[key]
        price = price_of(symbol, asset_class)
        current = broker.book["positions"].get(book_key)
        current_value = current["qty"] * price if current else 0.0
        diff = weight * equity - current_value
        if abs(diff) < max(MIN_TRADE_USD, REBALANCE_THRESHOLD * equity):
            continue
        if diff > 0:
            notional = min(diff, broker.book["cash"])
            if notional < MIN_TRADE_USD:
                continue
            broker.submit_order({"symbol": book_key, "side": "buy",
                                 "notional_usd": round(notional, 2),
                                 "asset_class": asset_class, "price": price}, None)
            log("mirror", f"buy ${notional:,.0f} {book_key} → {weight:.0%} of book ({desc})")
        else:
            sell_qty = min(-diff / price, current["qty"])
            broker.submit_order({"symbol": book_key, "side": "sell", "qty": sell_qty,
                                 "notional_usd": round(-diff, 2),
                                 "asset_class": asset_class, "price": price}, None)
            log("mirror", f"trim {book_key} back to {weight:.0%} ({desc})")
        trades += 1
    cycle["mirror_trades"] = trades
    return trades
