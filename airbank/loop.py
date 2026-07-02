"""The loop: gather -> reason -> act -> verify. One cycle per invocation;
launchd provides the repeat. Crash-resume from the 3 state files alone."""
import traceback

from . import analyst, approvals, broker, config, data, risk, signals
from .evaluator import evaluate_cycle
from .state import load_state, log, now_utc, roll_day, save_state


def run_cycle():
    state = load_state()
    cycle = {"data_ages_min": {}, "data_errors": [], "errors": [],
             "candidates": [], "gated": [], "executed": [], "refused": []}
    try:
        _gather_reason_act(state, cycle)
        state["consecutive_failures"] = 0
    except Exception:
        cycle["errors"].append(traceback.format_exc(limit=3))
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        if state["consecutive_failures"] >= 3:  # restart policy: halt, don't patch-pile
            state["halt"] = True
            state["halt_reason"] = "3 consecutive cycle failures"
            approvals.notify("Airbank halted: 3 consecutive cycle failures. Read state/log.md.")

    # verify — evaluator owns the halt switch
    score, findings, halted = evaluate_cycle(state, cycle)
    if halted:
        approvals.notify(f"Airbank HALTED by evaluator: {state['halt_reason']}")

    state["last_cycle_utc"] = now_utc().isoformat()
    save_state(state)
    log("cycle", _summary(cycle, score))
    return score, cycle


def _gather_reason_act(state, cycle):
    # ---- gather
    equity = broker.equity() if config.HAS_BROKER else None
    roll_day(state, equity)
    gross = broker.gross_exposure_usd() if config.HAS_BROKER else 0.0
    held = {p["symbol"] for p in (broker.positions() if config.HAS_BROKER else [])}
    market_open = data.us_market_open()

    # kill switch before anything else
    cycle["daily_pnl_pct"] = risk.daily_pnl_pct(state, equity)
    if equity and risk.kill_switch_breached(state, equity):
        cycle["kill_switch"] = True
        return

    approvals.expire_stale(state)
    if state.get("halt"):
        log("cycle-skip", f"halted: {state.get('halt_reason', '')}")
        return

    # ---- act on previously approved live trades first
    for approval in approvals.approved_ready(state):
        _execute(state, cycle, approval["order"], approval)

    # ---- reason: systematic candidates per symbol
    universe = [(s, "crypto") for s in config.CRYPTO_UNIVERSE]
    if market_open:
        universe += [(s, "equity") for s in config.EQUITY_UNIVERSE]
    gates = state.get("strategy_gates", {})

    for symbol, asset_class in universe:
        try:
            _, closes = data.daily_closes(symbol, asset_class)
            price, age = data.latest_price(symbol, asset_class)
            cycle["data_ages_min"][symbol] = (asset_class, age)
            closes = closes + [price]
        except Exception as exc:
            cycle["data_errors"].append(f"{symbol}: {exc}")
            continue
        holding = symbol in held or symbol.replace("/", "") in held
        for candidate in signals.generate_candidates(
                symbol, asset_class, closes, holding=holding):
            candidate["data_age_min"] = age
            cycle["candidates"].append(candidate)
            gate = gates.get(candidate["strategy"], {})
            if not gate.get("eligible"):
                log("signal-only", f"{candidate['strategy']} {candidate['side']} "
                    f"{symbol} (strategy not backtest-eligible)", candidate["why"])
                continue
            _gate_and_propose(state, cycle, candidate, gross)


def _gate_and_propose(state, cycle, candidate, gross):
    headlines = data.headlines([candidate["symbol"]])
    verdict = analyst.review(candidate, headlines)
    if verdict is None or verdict["verdict"] == "veto" or verdict["conviction"] <= 0:
        cycle["refused"].append({**candidate, "reason": "analyst veto/failure"})
        log("veto", f"{candidate['strategy']} {candidate['side']} {candidate['symbol']}",
            (verdict or {}).get("thesis", "analyst failure -> dropped"))
        return
    order = {
        "symbol": candidate["symbol"].replace("/", "") if candidate["asset_class"] == "crypto"
                  else candidate["symbol"],
        "side": candidate["side"],
        "notional_usd": round(risk.base_size_usd() * (2 * verdict["conviction"]), 2),
        "asset_class": candidate["asset_class"],
        "data_age_min": candidate["data_age_min"],
        "strategy": candidate["strategy"],
    }
    ok, reason = risk.check_order(order, state, gross)
    if not ok:
        cycle["refused"].append({**order, "reason": reason})
        log("refused", f"{order['side']} {order['symbol']}", reason)
        return
    cycle["gated"].append({**order, **verdict})
    approval = approvals.create(state, order, verdict)
    if approval["status"] == "auto-approved":  # paper mode
        _execute(state, cycle, order, approval)


def _execute(state, cycle, order, approval):
    if not config.HAS_BROKER:
        log("dry-run", f"{order['side']} {order['symbol']} ${order['notional_usd']:.0f}",
            "no broker keys — research mode, order not sent")
        return
    try:
        broker.submit_order(order, state, approval)
        state["trades_today"] = state.get("trades_today", 0) + 1
        approvals.mark_executed(state, approval["id"])
        cycle["executed"].append({"order": order, "approval_status": approval["status"]})
        log("executed", f"{order['side']} {order['symbol']} ${order['notional_usd']:.0f}",
            approval.get("thesis", ""))
    except broker.BrokerError as exc:
        cycle["refused"].append({**order, "reason": str(exc)})
        log("refused", f"{order['side']} {order['symbol']}", str(exc))


def _summary(cycle, score):
    return (f"{len(cycle['candidates'])} candidates, {len(cycle['gated'])} gated, "
            f"{len(cycle['executed'])} executed, {len(cycle['refused'])} refused, "
            f"score {score}")
