"""Backtest gate (contract assertions 20–22): daily bars, long-only, next-day
execution. A strategy must clear the gate before its proposals can execute."""
import math
import statistics

from . import config, data, signals
from .state import load_state, log, save_state


def _strategy_positions(closes, strategy):
    """Position (0/1) at each day's close, decided from data up to that close."""
    pos, out = 0, []
    for i in range(len(closes)):
        window = closes[: i + 1]
        if strategy == "momentum":
            sig = signals.momentum_signal(window)
            pos = 1 if sig else 0
        else:  # meanrev
            sig = signals.meanrev_signal(window, holding=pos == 1)
            if sig:
                pos = 1 if sig["side"] == "buy" else 0
        out.append(pos)
    return out


def _stats(daily_returns):
    if not daily_returns:
        return {"total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in daily_returns:
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    mean = sum(daily_returns) / len(daily_returns)
    stdev = statistics.pstdev(daily_returns)
    sharpe = 0.0 if stdev == 0 else mean / stdev * math.sqrt(365)
    return {"total_return": equity - 1, "sharpe": sharpe, "max_drawdown": max_dd}


def run(days=365):
    """Backtest each strategy across the whole universe; write gates to state."""
    universe = ([(s, "crypto") for s in config.CRYPTO_UNIVERSE]
                + [(s, "equity") for s in config.EQUITY_UNIVERSE])
    series = {}
    for symbol, asset_class in universe:
        try:
            _, closes = data.daily_closes(symbol, asset_class, days + 80)
            if len(closes) > 80:
                series[symbol] = closes
        except Exception as exc:
            print(f"  data error {symbol}: {exc}")

    results = {}
    for strategy in ("momentum", "meanrev"):
        strat_rets, bench_rets = [], []
        per_symbol = {}
        for symbol, closes in series.items():
            positions = _strategy_positions(closes, strategy)
            # position decided at close t earns return t -> t+1
            s_rets = [positions[i - 1] * (closes[i] / closes[i - 1] - 1)
                      for i in range(1, len(closes))]
            b_rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
            per_symbol[symbol] = _stats(s_rets)
            strat_rets.append(s_rets)
            bench_rets.append(b_rets)
        # equal-weight portfolio across symbols, aligned from the shortest series
        span = min(len(r) for r in strat_rets) if strat_rets else 0
        port = [sum(r[-span:][i] for r in strat_rets) / len(strat_rets) for i in range(span)]
        bench = [sum(r[-span:][i] for r in bench_rets) / len(bench_rets) for i in range(span)]
        stats, bench_stats = _stats(port), _stats(bench)
        eligible = (
            stats["sharpe"] > config.BACKTEST_GATE["min_sharpe"]
            and (stats["total_return"] > bench_stats["total_return"]
                 or stats["max_drawdown"] > bench_stats["max_drawdown"] / 2)
        )
        results[strategy] = {"eligible": eligible, "portfolio": stats,
                             "benchmark": bench_stats, "per_symbol": per_symbol}

    state = load_state()
    state["strategy_gates"] = {
        k: {"eligible": v["eligible"],
            "sharpe": round(v["portfolio"]["sharpe"], 2),
            "total_return": round(v["portfolio"]["total_return"], 4),
            "max_drawdown": round(v["portfolio"]["max_drawdown"], 4),
            "bench_return": round(v["benchmark"]["total_return"], 4)}
        for k, v in results.items()
    }
    save_state(state)
    log("backtest", ", ".join(
        f"{k}: sharpe {v['portfolio']['sharpe']:.2f} "
        f"{'ELIGIBLE' if v['eligible'] else 'ineligible'}" for k, v in results.items()))
    return results
