"""The analyst desk: pre-built research agents you deploy on demand
(contract assertions 39–40). Each deployment gathers live fund context,
briefs a Claude analyst, and files a timestamped markdown report to
~/.airbank/research/. Reports are advisory — they never place orders."""
import json
import os
import subprocess
from datetime import datetime, timezone

from . import config, data
from .state import load_state, log, save_state

RESEARCH_DIR = config.HOME_DIR / "research"
DEPLOY_TIMEOUT_S = 300

ROSTER = {
    "premarket": {
        "title": "Pre-Market Analyst",
        "desc": "overnight moves, today's setup, what to watch at the open",
        "brief": """Write the morning pre-market briefing. Cover: overnight crypto
action, where the equity universe closed vs its trend, the fund's current
exposure going into the session, and the 3 things most likely to move the
book today. Close with a stance: aggressive / neutral / defensive, and why.""",
    },
    "macro": {
        "title": "Macro Strategist",
        "desc": "regime read: risk-on or risk-off, and what flips it",
        "brief": """Assess the current market regime from the price data: trend
alignment across crypto and equities, volatility posture, cross-asset
divergences. State the regime (risk-on / risk-off / transition), the
strongest evidence against your own call, and what price action would
falsify it.""",
    },
    "crypto": {
        "title": "Crypto Desk",
        "desc": "deep dive on the BTC/ETH/SOL book and setups",
        "brief": """Deep-dive the crypto universe: relative strength between BTC,
ETH, SOL, where each sits vs its moving averages, and which (if any) offers
the best asymmetry right now. Be specific about levels. If nothing is
attractive, say so plainly — flat is a position.""",
    },
    "equity": {
        "title": "Equity Desk",
        "desc": "the megacap book: leaders, laggards, rotations",
        "brief": """Review the equity universe: leaders vs laggards, whether index
strength is broad or narrow, and which names the momentum strategy is most
likely to signal next. Flag any name whose trend looks exhausted.""",
    },
    "risk": {
        "title": "Risk Officer",
        "desc": "adversarial review of the current book vs the caps",
        "brief": """You are the risk officer and your job is to find the problem.
Review current positions, exposure vs caps, concentration, data staleness,
and the trade log for discipline drift. Assume something is wrong and try
to prove it. End with: the single biggest risk in the book right now.""",
    },
    "journal": {
        "title": "Performance Coach",
        "desc": "reads the trade log and critiques the process",
        "brief": """Read the recent trade log entries. Critique the PROCESS, not
the outcomes: were entries consistent with the stated theses, were exits
taken when invalidated, did any veto look wrong in hindsight. Give the loop
one concrete behavioral adjustment.""",
    },
}


def _context():
    """Everything an analyst gets to see, gathered fresh."""
    state = load_state()
    lines = [f"UTC now: {datetime.now(timezone.utc).isoformat()[:16]}"]
    lines.append(f"Account: {config.ACCOUNT_TYPE}")
    view = state.get("portfolio_view") or {}
    if view:
        lines.append(f"Portfolio: {json.dumps(view)}")
    lines.append(f"Strategy gates: {json.dumps(state.get('strategy_gates', {}))}")
    lines.append("Universe daily closes (last 30):")
    for symbol, asset_class in ([(s, "crypto") for s in config.CRYPTO_UNIVERSE]
                                + [(s, "equity") for s in config.EQUITY_UNIVERSE]):
        try:
            _, closes = data.daily_closes(symbol, asset_class, days=45)
            lines.append(f"  {symbol}: {[round(c, 2) for c in closes[-30:]]}")
        except Exception as exc:
            lines.append(f"  {symbol}: unavailable ({str(exc)[:40]})")
    headlines = data.headlines(config.CRYPTO_UNIVERSE + config.EQUITY_UNIVERSE, limit=15)
    if headlines:
        lines.append("Headlines: " + "; ".join(headlines))
    log_file = config.STATE_DIR / "log.md"
    if log_file.exists():
        lines.append("Recent trade log:\n" + "\n".join(
            log_file.read_text().splitlines()[-40:]))
    return "\n".join(lines)


PROMPT = """You are the {title} at Airbank by Finsider, an AI-native hedge fund
run as an agent loop (long-only, momentum + mean-reversion behind an LLM entry
gate, hard risk caps).

{brief}

Ground every claim in the data below — no invented numbers, no boilerplate.
Write tight, opinionated markdown (a real PM will read this): a # title,
short sections, and a final "## Desk view" with 2-3 actionable, falsifiable
calls. Under 500 words.

=== FUND DATA ===
{context}"""


def deploy(name, runner=None):
    """Run one analyst now. Returns (report_path, first_line). runner is
    injectable for tests; defaults to the claude CLI."""
    spec = ROSTER[name]
    prompt = PROMPT.format(title=spec["title"], brief=spec["brief"], context=_context())
    run = runner or _claude
    report = run(prompt)
    if not report or not report.strip():
        raise RuntimeError(f"{name} produced no report")
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = RESEARCH_DIR / f"{stamp}-{name}.md"
    path.write_text(report.strip() + "\n")
    headline = next((l.lstrip("# ").strip() for l in report.splitlines()
                     if l.strip()), spec["title"])
    state = load_state()
    desk = state.setdefault("analyst_desk", {})
    desk[name] = {"last_run_utc": datetime.now(timezone.utc).isoformat(),
                  "report": str(path), "headline": headline[:80]}
    save_state(state)
    log("analyst", f"{name} filed: {headline[:60]}")
    return path, headline


def _claude(prompt):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    out = subprocess.run(
        ["claude", "--print", "--output-format", "text", prompt],
        capture_output=True, text=True, timeout=DEPLOY_TIMEOUT_S, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {out.stdout[:120]}")
    return out.stdout


def reports():
    """Newest first."""
    if not RESEARCH_DIR.exists():
        return []
    return sorted(RESEARCH_DIR.glob("*.md"), reverse=True)
