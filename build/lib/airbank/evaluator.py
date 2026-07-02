"""Evaluator role: assumes every cycle is broken and tries to prove it against
contract.md. Separate from the Generator (assertion 24); owns the halt switch.
Rubric (assertion 23): risk 0.4, data 0.2, thesis 0.2, hygiene 0.2."""
import json

from .config import CAPS, LIVE
from .state import LOG_FILE, STATE_FILE, log

CRITICAL = "CRITICAL"
WARN = "WARN"


def evaluate_cycle(state, cycle):
    """cycle: dict the loop assembled while running — data ages, proposals,
    executions, refusals, equity, errors. Returns (score, findings, halt)."""
    findings = []

    # --- risk discipline (0.4)
    risk_score = 1.0
    for execution in cycle.get("executed", []):
        order = execution["order"]
        if order["notional_usd"] > CAPS["max_position_usd"]:
            findings.append((CRITICAL, f"executed order over max position: {order}"))
            risk_score = 0.0
        if LIVE and execution.get("approval_status") not in ("approved",):
            findings.append((CRITICAL, f"live execution without approval: {order}"))
            risk_score = 0.0
    if cycle.get("kill_switch"):
        findings.append((CRITICAL, f"daily loss kill switch: {cycle['daily_pnl_pct']:.2f}%"))
        risk_score = 0.0
    if state.get("trades_today", 0) > CAPS["max_trades_per_day"]:
        findings.append((CRITICAL, "trades_today exceeds cap"))
        risk_score = 0.0

    # --- data integrity (0.2)
    data_score = 1.0
    ages = cycle.get("data_ages_min", {})
    for symbol, (asset_class, age) in ages.items():
        if age > CAPS["stale_data_min"][asset_class]:
            findings.append((WARN, f"stale data tolerated for {symbol}: {age:.0f}m"))
            data_score = min(data_score, 0.5)
    if cycle.get("data_errors"):
        findings.append((WARN, f"data errors: {cycle['data_errors']}"))
        data_score = min(data_score, 0.5)

    # --- thesis quality (0.2)
    thesis_score = 1.0
    proposals = cycle.get("gated", [])
    if proposals:
        vague = [p for p in proposals
                 if len(p.get("thesis", "")) < 30 or p.get("conviction", 0) < 0.3]
        thesis_score = 1.0 - len(vague) / len(proposals)
        if vague:
            findings.append((WARN, f"{len(vague)} low-quality theses"))

    # --- process hygiene (0.2)
    hygiene_score = 1.0
    try:
        json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    except json.JSONDecodeError:
        findings.append((CRITICAL, "state.json is not valid JSON"))
        hygiene_score = 0.0
    if not LOG_FILE.exists():
        findings.append((WARN, "log.md missing"))
        hygiene_score = min(hygiene_score, 0.5)
    if cycle.get("errors"):
        findings.append((WARN, f"cycle errors: {cycle['errors']}"))
        hygiene_score = min(hygiene_score, 0.5)

    score = round(0.4 * risk_score + 0.2 * data_score
                  + 0.2 * thesis_score + 0.2 * hygiene_score, 3)
    halt = any(level == CRITICAL for level, _ in findings)

    body = f"score={score}\n" + "\n".join(f"- {lvl}: {msg}" for lvl, msg in findings)
    log("evaluate", f"cycle score {score}" + (" HALT" if halt else ""), body)

    if halt:
        state["halt"] = True
        state["halt_reason"] = "; ".join(m for lvl, m in findings if lvl == CRITICAL)
    elif score < 0.6:
        findings.append((WARN, "score below 0.6 — review the trace"))

    return score, findings, halt
