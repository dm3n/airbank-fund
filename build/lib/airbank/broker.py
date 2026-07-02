"""Broker layer: Alpaca REST via urllib. Second half of the belt-and-suspenders
live gate (assertion 18): this layer independently refuses live orders without
an approved, unexpired approval id."""
import json
import urllib.request

from . import config
from .risk import live_trading_armed

APPROVED_MARKER = "approval_id"


class BrokerError(Exception):
    pass


def _request(method, path, body=None):
    if not config.HAS_BROKER:
        raise BrokerError("no Alpaca keys configured")
    req = urllib.request.Request(
        config.ALPACA_TRADE_URL + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={
            "APCA-API-KEY-ID": config.ALPACA_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_SECRET,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def account():
    return _request("GET", "/v2/account")


def equity():
    try:
        return float(account()["equity"])
    except (BrokerError, KeyError, ValueError):
        return None


def positions():
    try:
        return _request("GET", "/v2/positions")
    except BrokerError:
        return []


def gross_exposure_usd():
    return sum(abs(float(p["market_value"])) for p in positions())


def submit_order(order, state, approval=None):
    """order: {symbol, side, notional_usd, asset_class}. In live mode an
    approved, unexpired approval dict is mandatory — refuse otherwise."""
    if config.LIVE:
        if not live_trading_armed(state):
            raise BrokerError("live mode not armed (live_ack missing)")
        if not approval or approval.get("status") != "approved":
            raise BrokerError("live order without approval refused")
    payload = {
        "symbol": order["symbol"],
        "side": order["side"],
        "type": "market",
        "notional": str(round(order["notional_usd"], 2)),
        "time_in_force": "gtc" if order["asset_class"] == "crypto" else "day",
    }
    return _request("POST", "/v2/orders", payload)
