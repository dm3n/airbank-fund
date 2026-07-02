"""Risk layer: hard caps and kill switch. Contract assertions 17–19.
Every check returns (ok, reason) so refusals are loggable."""
from .config import CAPS, LIVE


def base_size_usd():
    return CAPS["max_position_usd"] / 2  # analyst conviction scales up to cap


def check_order(order, state, gross_exposure_usd):
    """Gate one proposed order against every cap. order: {symbol, side,
    notional_usd, asset_class, data_age_min}."""
    if state.get("halt"):
        return False, "loop halted"
    if order["side"] == "buy":
        stale_cap = CAPS["stale_data_min"][order["asset_class"]]
        if order.get("data_age_min", 0) > stale_cap:
            return False, f"stale data {order['data_age_min']:.0f}m > {stale_cap}m"
        if order["notional_usd"] > CAPS["max_position_usd"]:
            return False, "exceeds max position size"
        if gross_exposure_usd + order["notional_usd"] > CAPS["max_gross_usd"]:
            return False, "would breach max gross exposure"
    if state.get("trades_today", 0) >= CAPS["max_trades_per_day"]:
        return False, "max trades per day reached"
    if order["notional_usd"] <= 0:
        return False, "non-positive notional"
    return True, "ok"


def daily_pnl_pct(state, equity):
    anchor = state.get("day_start_equity")
    if not anchor or not equity:
        return 0.0
    return (equity / anchor - 1) * 100


def kill_switch_breached(state, equity):
    return daily_pnl_pct(state, equity) <= CAPS["daily_loss_limit_pct"]


def live_trading_armed(state):
    """Live execution requires mode AND explicit ack in state — belt and
    suspenders with the per-trade approval gate."""
    return LIVE and state.get("live_ack") is True
