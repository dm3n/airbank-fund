"""Broker abstraction over account types (contract assertions 30–32).
MockBroker simulates a portfolio inside state.json with fills at real market
prices; AlpacaBroker wraps the REST layer; WalletBroker is watch-only and can
never execute."""
import json
import urllib.request

from . import broker as alpaca
from . import config


class ExecutionRefused(Exception):
    pass


class MockBroker:
    """Simulated account: cash + long positions, marked to market by the loop."""
    name = "mock"
    can_execute = True

    def __init__(self, state):
        state.setdefault("mock", {
            "cash": config.STARTING_CASH,
            "starting_cash": config.STARTING_CASH,
            "realized_pnl": 0.0,
            "positions": {},  # symbol -> {qty, avg_price, last_price}
        })
        self.book = state["mock"]

    def equity(self):
        return self.book["cash"] + sum(
            p["qty"] * p["last_price"] for p in self.book["positions"].values())

    def gross_exposure_usd(self):
        return sum(p["qty"] * p["last_price"] for p in self.book["positions"].values())

    def held_symbols(self):
        return set(self.book["positions"])

    def mark(self, symbol, price):
        pos = self.book["positions"].get(symbol)
        if pos:
            pos["last_price"] = price

    def submit_order(self, order, state, approval=None):
        """Fill at the real market price the loop just observed (order['price'])."""
        price = float(order.get("price") or 0)
        if price <= 0:
            raise ExecutionRefused("mock fill requires a live price")
        symbol = order["symbol"]
        positions = self.book["positions"]
        if order["side"] == "buy":
            notional = order["notional_usd"]
            if notional > self.book["cash"]:
                raise ExecutionRefused("insufficient mock cash")
            qty = notional / price
            pos = positions.get(symbol, {"qty": 0.0, "avg_price": 0.0, "last_price": price})
            total_cost = pos["qty"] * pos["avg_price"] + notional
            pos["qty"] += qty
            pos["avg_price"] = total_cost / pos["qty"]
            pos["last_price"] = price
            positions[symbol] = pos
            self.book["cash"] -= notional
        else:  # long-only v1: sell closes the whole position
            pos = positions.pop(symbol, None)
            if pos is None:
                raise ExecutionRefused(f"no mock position in {symbol}")
            proceeds = pos["qty"] * price
            self.book["cash"] += proceeds
            self.book["realized_pnl"] += proceeds - pos["qty"] * pos["avg_price"]
        return {"status": "filled", "symbol": symbol, "price": price}

    def view(self):
        equity = self.equity()
        start = self.book["starting_cash"]
        return {
            "account": "mock",
            "cash": round(self.book["cash"], 2),
            "equity": round(equity, 2),
            "realized_pnl": round(self.book["realized_pnl"], 2),
            "total_pnl_pct": round((equity / start - 1) * 100, 3) if start else 0.0,
            "positions": [
                {"symbol": s, "qty": round(p["qty"], 8),
                 "value": round(p["qty"] * p["last_price"], 2),
                 "pnl_pct": round((p["last_price"] / p["avg_price"] - 1) * 100, 2)
                 if p["avg_price"] else 0.0}
                for s, p in sorted(self.book["positions"].items())
            ],
        }


class AlpacaBroker:
    name = "alpaca"

    def __init__(self, state):
        self.state = state

    @property
    def can_execute(self):
        return config.HAS_BROKER

    def equity(self):
        return alpaca.equity()

    def gross_exposure_usd(self):
        return alpaca.gross_exposure_usd()

    def held_symbols(self):
        return {p["symbol"] for p in alpaca.positions()}

    def mark(self, symbol, price):
        pass  # Alpaca marks its own book

    def submit_order(self, order, state, approval=None):
        return alpaca.submit_order(order, state, approval)

    def view(self):
        try:
            acct = alpaca.account()
            equity = float(acct["equity"])
            return {
                "account": "alpaca_live" if config.LIVE else "alpaca_paper",
                "cash": round(float(acct.get("cash", 0)), 2),
                "equity": round(equity, 2),
                "total_pnl_pct": round(
                    (equity / float(acct.get("last_equity", equity) or equity) - 1) * 100, 3),
                "positions": [
                    {"symbol": p["symbol"], "qty": float(p["qty"]),
                     "value": round(float(p["market_value"]), 2),
                     "pnl_pct": round(float(p.get("unrealized_plpc", 0)) * 100, 2)}
                    for p in alpaca.positions()
                ],
            }
        except (alpaca.BrokerError, KeyError, ValueError):
            return {"account": "alpaca (unreachable)", "cash": 0, "equity": None,
                    "total_pnl_pct": 0, "positions": []}


class WalletBroker:
    """Watch-only: tracks a public address balance; NEVER executes (assertion 32)."""
    name = "wallet"
    can_execute = False

    def __init__(self, state):
        self.state = state
        state.setdefault("wallet_view", {})

    def equity(self):
        return self.state["wallet_view"].get("value_usd")

    def gross_exposure_usd(self):
        return 0.0

    def held_symbols(self):
        return set()

    def mark(self, symbol, price):
        pass

    def submit_order(self, order, state, approval=None):
        raise ExecutionRefused("watch-only wallet accounts never trade")

    def refresh(self, btc_usd=None, eth_usd=None):
        chain = config.WALLET.get("chain")
        address = config.WALLET.get("address", "")
        try:
            if chain == "btc":
                sats = float(_fetch(
                    f"https://blockchain.info/q/addressbalance/{address}"))
                balance = sats / 1e8
                value = balance * btc_usd if btc_usd else None
            elif chain == "eth":
                info = json.loads(_fetch(
                    f"https://api.ethplorer.io/getAddressInfo/{address}?apiKey=freekey"))
                balance = float(info["ETH"]["balance"])
                value = balance * eth_usd if eth_usd else None
            else:
                return
            self.state["wallet_view"] = {
                "chain": chain, "address": address,
                "balance": round(balance, 8),
                "value_usd": round(value, 2) if value else None,
            }
        except Exception as exc:
            self.state["wallet_view"].setdefault("error", str(exc)[:80])

    def view(self):
        w = self.state["wallet_view"]
        return {"account": f"watch-only {w.get('chain', '?').upper()} wallet",
                "cash": 0, "equity": w.get("value_usd"),
                "total_pnl_pct": 0,
                "positions": [{"symbol": w.get("chain", "?").upper(),
                               "qty": w.get("balance", 0),
                               "value": w.get("value_usd") or 0, "pnl_pct": 0}]
                if w.get("balance") else []}


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 airbank"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode()


def get(state):
    if config.ACCOUNT_TYPE in ("alpaca_paper", "alpaca_live"):
        return AlpacaBroker(state)
    if config.ACCOUNT_TYPE == "wallet":
        return WalletBroker(state)
    return MockBroker(state)
