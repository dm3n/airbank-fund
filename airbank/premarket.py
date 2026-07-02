"""The pre-market desk: Airbank's first real multi-stage analyst agent.

A morning pipeline, not a prompt (loops.md: write the loop):
  gather  — free keyless data: daily bars, pre-market gaps, RSS headlines
  screen  — the fund's own backtested strategies score every universe name
  argue   — brain 1 (Claude) writes the watchlist; brain 2 (Codex, a
            different vendor) attacks it independently over the same dossier
  verify  — deterministic structure checks, warnings appended in the open
  deliver — Slack/macOS notify, optional Resend email to the inbox

Filed through the analyst desk like any report — advisory only, no code
path to an order (contract assertion 40)."""
import html
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import config, data, signals
from .state import load_state, log

RULES_FILE = config.HOME_DIR / "watchlist-rules.md"
REVIEW_TIMEOUT_S = 300

DEFAULT_RULES = """# Watchlist rules — the desk reads this file every morning. Edit it.

These are the fund's own backtested strategies (see `airbank contract`), not
generic setups. A name is ACTIONABLE only via a strategy that currently holds
an ELIGIBLE backtest gate; everything else is watch-only.

## Momentum (trend-join long)
- Entry: 20d SMA above 60d SMA AND 20d return positive.
- Exit: the trend condition breaks.
- Names close to a fresh cross belong on the watchlist with the trigger level.

## Mean reversion
- Entry: 20d z-score below -2.0. Exit: z back at 0.
- Names with z under -1.5 are "approaching" — list the price that gets z to -2.

## Filters (always on)
- No new entries when 20d realized vol is above 2x its trailing median.
- Long only, no shorts, no leverage. Hard caps per `airbank contract`.
- Equities trade regular hours only; crypto is 24/7.

## Report taste
- Flat is a position: an empty watchlist beats a forced one.
- Every call needs a trigger, an invalidation level, and a falsifier.
"""

ANALYST_PROMPT = """You are the Pre-Market Desk at Airbank by Finsider, an
AI-native hedge fund run as an agent loop (long-only, momentum + mean-reversion
behind an LLM entry gate, hard risk caps). It is {date} UTC, ahead of the US open.

Write today's pre-market watchlist report in tight, opinionated markdown.
Required sections, in order:

# Pre-Market Watchlist — {date}
## Market sentiment — risk-on / risk-off / mixed, argued from the tape below
## Overnight & pre-market tape — the moves that matter (equity gaps, crypto overnight)
## Watchlist — the only section a PM trades from. Per name: the strategy
   (ELIGIBLE strategies are actionable; ineligible = watch-only), the trigger,
   the invalidation level, the plan. Include names within striking distance of
   a trigger. If nothing qualifies say "no setups" — flat is a position.
## Catalysts & news — only what could move THIS book today
## Falsifiers — what would flip your sentiment call by lunch

THE TRADER'S OWN RULES (backtested — never hand back generic garbage):
{rules}

Hard constraints: universe names only; every number comes from the dossier —
no invented prices or levels; under 700 words.

=== DOSSIER ===
{dossier}"""

REVIEW_PROMPT = """You are an independent second brain reviewing another AI's
pre-market report before a human trader reads it. You did not write it; assume
it contains at least one error and go find it.

Check against the dossier: every number it cites, every trigger and
invalidation level, whether each watchlist call actually follows the trader's
rules, and what it missed. Do not rewrite the report.

Reply in markdown, under 250 words, exactly these sections:
### Where I agree
### Where I disagree
### Errors found
### What it missed

THE TRADER'S RULES:
{rules}

=== DOSSIER (identical to what the first brain saw) ===
{dossier}

=== REPORT UNDER REVIEW ===
{report}"""


# ----------------------------------------------------------------- gather

def _premarket_gap(symbol):
    """(last, prev_close, gap_pct) including pre/post trades — Yahoo, keyless."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range=1d&interval=5m&includePrePost=true")
    chart = json.loads(data._get(url))["chart"]["result"][0]
    prev = float(chart["meta"]["chartPreviousClose"])
    closes = [c for c in chart["indicators"]["quote"][0]["close"] if c is not None]
    last = float(closes[-1]) if closes else float(chart["meta"]["regularMarketPrice"])
    return last, prev, (last / prev - 1) * 100 if prev else 0.0


def _rss(url, limit=6):
    root = ET.fromstring(data._get(url, timeout=15))
    titles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if title:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def _news():
    """Headlines from free keyless feeds; Alpaca news joins when keys exist."""
    feeds = [("macro", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
             ("crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/")]
    feeds += [(sym, "https://feeds.finance.yahoo.com/rss/2.0/headline"
                    f"?s={sym}&region=US&lang=en-US")
              for sym in config.EQUITY_UNIVERSE]
    lines = []
    for tag, url in feeds:
        try:
            lines += [f"[{tag}] {t}" for t in _rss(url, 4 if tag in ("macro", "crypto") else 2)]
        except Exception:
            continue
    lines += data.headlines(config.CRYPTO_UNIVERSE + config.EQUITY_UNIVERSE, limit=10)
    return lines


# ----------------------------------------------------------------- screen

def _screen_row(symbol, asset_class, closes, holding):
    """One universe name scored by the fund's actual strategy math."""
    p = config.STRATEGY_PARAMS
    fast = signals.sma(closes, p["momentum"]["fast"])
    slow = signals.sma(closes, p["momentum"]["slow"])
    look = p["momentum"]["lookback"]
    row = {"symbol": symbol, "class": asset_class, "last": round(closes[-1], 2),
           "chg_1d_pct": round((closes[-1] / closes[-2] - 1) * 100, 2),
           "holding": holding}
    if fast and slow:
        row.update({"sma20": round(fast, 2), "sma60": round(slow, 2),
                    "trend": "up" if fast > slow else "down"})
    if len(closes) > look:
        row[f"ret_{look}d_pct"] = round((closes[-1] / closes[-1 - look] - 1) * 100, 2)
    z = signals.zscore(closes, p["meanrev"]["window"])
    if z is not None:
        row["z20"] = round(z, 2)
    row["vol_filter_ok"] = signals.vol_filter_ok(closes)
    row["signals_now"] = [f"{s['strategy']}:{s['side']} ({s['why']})"
                          for s in signals.generate_candidates(
                              symbol, asset_class, closes, holding)]
    return row


def _dossier():
    """Everything both brains see — computed, not narrated, wherever possible."""
    state = load_state()
    view = state.get("portfolio_view") or {}
    held = {p["symbol"] for p in view.get("positions", [])}
    lines = [f"UTC now: {datetime.now(timezone.utc).isoformat()[:16]}",
             f"Account: {config.ACCOUNT_TYPE}",
             f"Portfolio: {json.dumps(view) if view else '(no view yet)'}",
             f"Strategy gates (backtest-earned): "
             f"{json.dumps(state.get('strategy_gates', {}))}",
             f"Risk caps: {json.dumps(config.CAPS)}",
             "", "Strategy screen (the fund's own signal math, per name):"]
    universe = ([(s, "crypto") for s in config.CRYPTO_UNIVERSE]
                + [(s, "equity") for s in config.EQUITY_UNIVERSE])
    for symbol, asset_class in universe:
        try:
            _, closes = data.daily_closes(symbol, asset_class, days=150)
            lines.append("  " + json.dumps(_screen_row(
                symbol, asset_class, closes, symbol in held)))
        except Exception as exc:
            lines.append(f"  {symbol}: data unavailable ({str(exc)[:40]})")
    lines.append("")
    lines.append("Equity pre-market tape (incl. extended hours):")
    for symbol in config.EQUITY_UNIVERSE:
        try:
            last, prev, gap = _premarket_gap(symbol)
            lines.append(f"  {symbol}: last {last:.2f}  prev close {prev:.2f}  "
                         f"gap {gap:+.2f}%")
        except Exception as exc:
            lines.append(f"  {symbol}: unavailable ({str(exc)[:40]})")
    headlines = _news()
    if headlines:
        lines.append("")
        lines.append("Headlines:")
        lines += [f"  - {h}" for h in headlines]
    log_file = config.STATE_DIR / "log.md"
    if log_file.exists():
        lines.append("")
        lines.append("Recent trade log:")
        lines += ["  " + l for l in log_file.read_text().splitlines()[-25:]]
    return "\n".join(lines)


# ------------------------------------------------------------------ argue

def rules():
    if not RULES_FILE.exists():
        config.HOME_DIR.mkdir(parents=True, exist_ok=True)
        RULES_FILE.write_text(DEFAULT_RULES)
    return RULES_FILE.read_text()


def _codex(prompt):
    """Second brain from a different vendor — structurally unable to echo."""
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="r", suffix=".md", delete=False) as f:
        out_path = f.name
    try:
        out = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check",
             "--output-last-message", out_path, prompt],
            capture_output=True, text=True, timeout=REVIEW_TIMEOUT_S,
            cwd=str(config.HOME_DIR))
        if out.returncode != 0:
            raise RuntimeError(f"codex exec failed: {out.stderr[:120]}")
        with open(out_path) as f:
            answer = f.read().strip()
        if not answer:
            raise RuntimeError("codex returned nothing")
        return answer
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _second_opinion(dossier, report, reviewer=None):
    """(review_markdown, engine_label). Falls back Codex -> adversarial
    Claude -> honest 'unreviewed' note; the report always ships."""
    prompt = REVIEW_PROMPT.format(rules=rules(), dossier=dossier, report=report)
    if reviewer:
        return reviewer(prompt), "injected reviewer"
    if shutil.which("codex"):
        try:
            return _codex(prompt), "Codex (OpenAI) — independent"
        except Exception as exc:
            log("analyst", f"premarket codex fallback: {str(exc)[:60]}")
    from .analysts import _claude
    try:
        return _claude(prompt), "Claude (adversarial second pass)"
    except Exception:
        return ("(second brain unavailable — this report shipped unreviewed)",
                "unavailable")


# ----------------------------------------------------------------- verify

REQUIRED = ("Market sentiment", "Watchlist", "Falsifiers", "Second brain")


def _verify(merged):
    """Deterministic desk check. Warn in the open, never eat the report."""
    warnings = [f"required section missing: {s}" for s in REQUIRED if s not in merged]
    if len(merged) < 400:
        warnings.append("report suspiciously short")
    return warnings


# ------------------------------------------------------------------ build

def build(runner=None, reviewer=None, dossier=None):
    """Run the full pipeline; return the merged two-brain report (markdown).
    runner/reviewer/dossier are injectable for tests."""
    from .analysts import _claude
    dossier = dossier if dossier is not None else _dossier()
    date = datetime.now(timezone.utc).strftime("%a %Y-%m-%d")
    report = (runner or _claude)(ANALYST_PROMPT.format(
        date=date, rules=rules(), dossier=dossier))
    if not report or not report.strip():
        raise RuntimeError("premarket brain 1 produced no report")
    report = report.strip()
    review, engine = _second_opinion(dossier, report, reviewer)
    merged = (f"{report}\n\n---\n\n## Second brain — {engine}\n\n"
              f"{review.strip()}")
    warnings = _verify(merged)
    if warnings:
        log("analyst", "premarket desk check: " + "; ".join(warnings))
        merged += "\n\n" + "\n".join(f"> desk check: {w}" for w in warnings)
    return merged


# ---------------------------------------------------------------- deliver

def _email(report, headline):
    """Ship to the inbox via Resend when configured; False when not."""
    key = os.environ.get("RESEND_API_KEY", "")
    to = os.environ.get("AIRBANK_EMAIL_TO", "")
    if not (key and to):
        return False
    body = {"from": os.environ.get("AIRBANK_EMAIL_FROM",
                                   "Airbank <onboarding@resend.dev>"),
            "to": [to], "subject": headline,
            "html": ("<pre style=\"font:13px Menlo,monospace;"
                     "white-space:pre-wrap\">" + html.escape(report) + "</pre>")}
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    urllib.request.urlopen(req, timeout=20)
    return True


def deliver(path, report, headline):
    """Notify + optional email. Never raises — delivery is best-effort."""
    from .approvals import notify
    notify(f"Pre-market watchlist filed: {headline}\n{path}")
    try:
        if _email(report, headline):
            log("analyst", f"premarket emailed to {os.environ['AIRBANK_EMAIL_TO']}")
    except Exception as exc:
        log("analyst", f"premarket email failed: {str(exc)[:60]}")
