"""Generator, systematic half: momentum, mean-reversion, volatility filter.
Pure functions over close-price lists. Contract assertions 8–10."""
import statistics

from .config import STRATEGY_PARAMS


def sma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def zscore(values, window):
    if len(values) < window:
        return None
    tail = values[-window:]
    mean = sum(tail) / window
    stdev = statistics.pstdev(tail)
    if stdev == 0:
        return 0.0
    return (values[-1] - mean) / stdev


def realized_vol(closes, window):
    if len(closes) < window + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - window, len(closes))]
    return statistics.pstdev(rets)


def vol_filter_ok(closes):
    """False when current vol > max_ratio × trailing median vol — assertion 10."""
    p = STRATEGY_PARAMS["vol_filter"]
    current = realized_vol(closes, p["window"])
    if current is None:
        return False
    history = []
    span = min(len(closes) - 1, p["median_window"])
    for end in range(len(closes) - span + p["window"], len(closes) + 1, p["window"]):
        v = realized_vol(closes[:end], p["window"])
        if v is not None:
            history.append(v)
    if not history:
        return True
    return current <= p["max_ratio"] * statistics.median(history)


def momentum_signal(closes):
    """Long when fast MA > slow MA and lookback return > 0 — assertion 8."""
    p = STRATEGY_PARAMS["momentum"]
    fast, slow = sma(closes, p["fast"]), sma(closes, p["slow"])
    if fast is None or slow is None or len(closes) <= p["lookback"]:
        return None
    lookback_ret = closes[-1] / closes[-1 - p["lookback"]] - 1
    if fast > slow and lookback_ret > 0:
        return {"strategy": "momentum", "side": "buy",
                "why": f"fast_ma>{'slow_ma'}, {p['lookback']}d ret {lookback_ret:+.2%}"}
    return None


def meanrev_signal(closes, holding=False):
    """Entry z < entry_z; exit z >= exit_z — assertion 9."""
    p = STRATEGY_PARAMS["meanrev"]
    z = zscore(closes, p["window"])
    if z is None:
        return None
    if holding and z >= p["exit_z"]:
        return {"strategy": "meanrev", "side": "sell", "why": f"z={z:.2f} >= exit"}
    if not holding and z < p["entry_z"]:
        return {"strategy": "meanrev", "side": "buy", "why": f"z={z:.2f} < {p['entry_z']}"}
    return None


def generate_candidates(symbol, asset_class, closes, holding=False):
    """All raw candidates for one symbol. Entries respect the vol filter."""
    candidates = []
    entries_allowed = vol_filter_ok(closes)
    for sig in (momentum_signal(closes), meanrev_signal(closes, holding)):
        if sig is None:
            continue
        if sig["side"] == "buy" and not entries_allowed:
            continue
        sig.update({"symbol": symbol, "asset_class": asset_class, "price": closes[-1]})
        candidates.append(sig)
    return candidates
