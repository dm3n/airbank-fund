"""Gather: market data. Alpaca when keys exist; keyless fallbacks otherwise
(Coinbase public candles for crypto, Yahoo chart API for equities) so research
mode works with zero setup — contract assertion 11."""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) airbank-fund/1.0"}


def _get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def _alpaca_headers():
    return {
        "APCA-API-KEY-ID": config.ALPACA_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET,
    }


# ---------------------------------------------------------------- daily bars

def daily_closes(symbol, asset_class, days=400):
    """Return (dates, closes) oldest-first for ~`days` calendar days."""
    if asset_class == "crypto":
        return _coinbase_daily(symbol, days)
    return _yahoo_daily(symbol, days)


def _coinbase_daily(symbol, days):
    product = symbol.replace("/", "-")  # BTC/USD -> BTC-USD
    end = datetime.now(timezone.utc)
    dates, closes = [], []
    remaining = days
    while remaining > 0:
        chunk = min(remaining, 300)
        start = end - timedelta(days=chunk)
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity=86400&start={start.isoformat()}&end={end.isoformat()}"
        )
        candles = json.loads(_get(url))  # [time, low, high, open, close, vol]
        for c in sorted(candles, key=lambda c: c[0]):
            dates.append(datetime.fromtimestamp(c[0], timezone.utc).strftime("%Y-%m-%d"))
            closes.append(float(c[4]))
        end = start
        remaining -= chunk
    pairs = sorted(set(zip(dates, closes)))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _yahoo_chart(symbol, range_str="2y"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={range_str}&interval=1d")
    return json.loads(_get(url))["chart"]["result"][0]


def _yahoo_daily(symbol, days):
    chart = _yahoo_chart(symbol, "2y" if days > 365 else "1y")
    closes_raw = chart["indicators"]["quote"][0]["close"]
    dates, closes = [], []
    for ts, close in zip(chart["timestamp"], closes_raw):
        if close is None:
            continue
        dates.append(datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"))
        closes.append(float(close))
    return dates[-days:], closes[-days:]


# ------------------------------------------------------------- latest price

def latest_price(symbol, asset_class):
    """Return (price, age_minutes)."""
    if config.HAS_BROKER:
        return _alpaca_latest(symbol, asset_class)
    if asset_class == "crypto":
        product = symbol.replace("/", "-")
        tick = json.loads(_get(f"https://api.exchange.coinbase.com/products/{product}/ticker"))
        age = _age_minutes(tick.get("time"))
        return float(tick["price"]), age
    meta = _yahoo_chart(symbol, "5d")["meta"]
    age = (datetime.now(timezone.utc)
           - datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc)
           ).total_seconds() / 60.0
    return float(meta["regularMarketPrice"]), age


def _alpaca_latest(symbol, asset_class):
    if asset_class == "crypto":
        url = (f"{config.ALPACA_DATA_URL}/v1beta3/crypto/us/latest/trades?"
               f"symbols={urllib.parse.quote(symbol)}")
        trade = json.loads(_get(url, _alpaca_headers()))["trades"][symbol]
    else:
        url = f"{config.ALPACA_DATA_URL}/v2/stocks/{symbol}/trades/latest"
        trade = json.loads(_get(url, _alpaca_headers()))["trade"]
    return float(trade["p"]), _age_minutes(trade["t"])


def _age_minutes(ts):
    if not ts:
        return 0.0
    ts = ts.replace("Z", "+00:00")
    if "." in ts:  # drop sub-second precision; fromisoformat needs 3/6 digits
        head, _, tail = ts.partition(".")
        offset = tail[tail.find("+"):] if "+" in tail else "+00:00"
        ts = head + offset
    then = datetime.fromisoformat(ts)
    return (datetime.now(timezone.utc) - then).total_seconds() / 60.0


# ------------------------------------------------------------------- news

def headlines(symbols, limit=10):
    """Recent headlines from Alpaca news API; [] when keyless."""
    if not config.HAS_BROKER:
        return []
    try:
        url = (f"{config.ALPACA_DATA_URL}/v1beta1/news?limit={limit}"
               f"&symbols={urllib.parse.quote(','.join(s.replace('/', '') for s in symbols))}")
        news = json.loads(_get(url, _alpaca_headers())).get("news", [])
        return [n.get("headline", "") for n in news]
    except Exception:
        return []


# ----------------------------------------------------------- market hours

def us_market_open(now=None):
    """Regular hours approximation: Mon–Fri 13:30–20:00 UTC (EDT). Alpaca
    clock is authoritative when keys exist."""
    if config.HAS_BROKER:
        try:
            clock = json.loads(_get(f"{config.ALPACA_TRADE_URL}/v2/clock", _alpaca_headers()))
            return bool(clock["is_open"])
        except Exception:
            pass
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= minutes < 20 * 60
