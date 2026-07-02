"""Desk chat: the conversational brain of the Airbank terminal. Fast context
(state + live quotes, no extra network), claude CLI backend, and an ACTION
protocol so the assistant can drive the terminal (run cycles, deploy
analysts) — execution still flows through the same risk/approval layers."""
import json
import os
import re
import subprocess

from . import config
from .state import load_state

CHAT_TIMEOUT_S = 120
ACTION_RE = re.compile(r"^ACTION:\s*(.+?)\s*$", re.MULTILINE)

PROMPT = """You are the desk assistant living inside the Airbank terminal —
Airbank by Finsider, the AI-native hedge fund (long-only, momentum +
mean-reversion behind an LLM entry gate, hard risk caps, per-trade human
approval on live money).

Voice: a sharp, warm trading-desk partner. Concise by default — a few
sentences unless the user asks for depth. Ground every number in the fund
data below; never invent prices or P&L. Plain text (no markdown headers); short lines read best in the terminal.
Use `backticks` around tickers, commands, numbers-as-code, and ``` fences
for multi-line code — the terminal renders them as code chips.

You can drive the terminal. If — and only if — the user asks for an action,
end your reply with exactly one final line:
ACTION: run
ACTION: backtest
ACTION: deploy <premarket|macro|crypto|equity|risk|journal>
Live-money approvals can NOT be given here — those need `airbank approve <id>`.

=== FUND DATA (live) ===
{context}

=== CONVERSATION SO FAR ===
{history}

User: {message}
Assistant:"""


def _context(quotes=None):
    state = load_state()
    lines = [f"account: {config.ACCOUNT_TYPE}",
             f"halt: {state.get('halt')} {state.get('halt_reason', '')}",
             f"trades today: {state.get('trades_today', 0)} / {config.CAPS['max_trades_per_day']}",
             f"caps: position ${config.CAPS['max_position_usd']:,.0f}, "
             f"gross ${config.CAPS['max_gross_usd']:,.0f}, "
             f"kill {config.CAPS['daily_loss_limit_pct']}%/day"]
    view = state.get("portfolio_view")
    if view:
        lines.append("portfolio: " + json.dumps(view))
    lines.append("strategy gates: " + json.dumps(state.get("strategy_gates", {})))
    pending = [a for a in state.get("pending_approvals", []) if a["status"] == "pending"]
    if pending:
        lines.append("pending approvals: " + json.dumps(pending))
    if quotes:
        quote_bits = [f"{s} {q['price']:,.2f} ({q['change_pct']:+.2f}%)"
                      for s, q in quotes.items() if q.get("price")]
        lines.append("live quotes: " + ", ".join(quote_bits))
    desk = state.get("analyst_desk", {})
    if desk:
        lines.append("analyst desk (latest filings): " + json.dumps(
            {k: v.get("headline", "") for k, v in desk.items()}))
    log_file = config.STATE_DIR / "log.md"
    if log_file.exists():
        tail = [l for l in log_file.read_text().splitlines() if l.strip()][-25:]
        lines.append("recent tape:\n" + "\n".join(tail))
    return "\n".join(lines)


def respond(message, history, quotes=None, runner=None):
    """Returns (reply_text, action_or_None). Fail-safe: errors surface as a
    readable reply, never an exception into the render loop."""
    convo = "\n".join(f"{'User' if role == 'user' else 'Assistant'}: {text}"
                      for role, text in history[-12:] if role in ("user", "assistant"))
    prompt = PROMPT.format(context=_context(quotes), history=convo or "(fresh session)",
                           message=message)
    try:
        text = (runner or _claude)(prompt).strip()
    except Exception as exc:
        return f"desk link is down ({str(exc)[:60]}) — try again in a moment", None
    action = None
    m = ACTION_RE.search(text)
    if m:
        action = m.group(1).strip().lower()
        text = ACTION_RE.sub("", text).strip()
    return text or "…", action


def _claude(prompt):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    out = subprocess.run(
        ["claude", "--print", "--output-format", "text", prompt],
        capture_output=True, text=True, timeout=CHAT_TIMEOUT_S, env=env)
    if out.returncode != 0:
        raise RuntimeError(out.stdout[:100] or "claude CLI error")
    return out.stdout
