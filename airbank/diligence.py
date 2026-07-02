"""The Finsider diligence engine (contract §D). Every number is computed in
Python from the deal's own financials; the LLM only narrates. No documents →
no memo — the deal waits.

Input: <deal_dir>/financials.csv with columns month,revenue,cogs,opex
(optional: customers.csv with customer,revenue for concentration)."""
import csv
import hashlib
import json
import os
import statistics
import subprocess

from . import config, pipeline
from .state import log, now_utc

MEMO_TIMEOUT_S = 300


# ------------------------------------------------------------- the numbers

def load_financials(deal_dir):
    path = deal_dir / "financials.csv"
    if not path.exists():
        return None
    rows = list(csv.DictReader(path.read_text().splitlines()))
    months = []
    for row in rows:
        try:
            months.append({"month": row["month"],
                           "revenue": float(row["revenue"]),
                           "cogs": float(row.get("cogs", 0)),
                           "opex": float(row.get("opex", 0))})
        except (KeyError, ValueError):
            continue
    return months or None


def metrics(months, deal_dir=None):
    """Deterministic diligence metrics (assertion 13)."""
    revenue = [m["revenue"] for m in months]
    gross = [m["revenue"] - m["cogs"] for m in months]
    ebitda = [g - m["opex"] for g, m in zip(gross, months)]
    ttm = revenue[-12:]
    half = max(1, len(revenue) // 2)
    first, second = revenue[:half], revenue[half:]
    growth = (sum(second) / len(second)) / max(sum(first) / len(first), 1) - 1
    gross_margins = [g / r for g, r in zip(gross, revenue) if r]
    ebitda_margins = [e / r for e, r in zip(ebitda, revenue) if r]
    out = {
        "months": len(months),
        "ttm_revenue": round(sum(ttm), 0),
        "ttm_ebitda": round(sum(ebitda[-12:]), 0),
        "growth_h2_vs_h1": round(growth, 4),
        "gross_margin_avg": round(statistics.mean(gross_margins), 4),
        "gross_margin_vol": round(statistics.pstdev(gross_margins), 4),
        "ebitda_margin_avg": round(statistics.mean(ebitda_margins), 4),
        "revenue_vol": round(statistics.pstdev(revenue) / max(statistics.mean(revenue), 1), 4),
        "worst_month_drop": round(min(
            (revenue[i] / revenue[i - 1] - 1) for i in range(1, len(revenue))), 4)
        if len(revenue) > 1 else 0.0,
    }
    concentration = _concentration(deal_dir)
    if concentration is not None:
        out["top_customer_share"] = concentration
    out["flags"] = _flags(out)
    out["score"] = _score(out)
    return out


def _concentration(deal_dir):
    if not deal_dir or not (deal_dir / "customers.csv").exists():
        return None
    rows = list(csv.DictReader((deal_dir / "customers.csv").read_text().splitlines()))
    values = sorted((float(r.get("revenue", 0) or 0) for r in rows), reverse=True)
    total = sum(values)
    return round(values[0] / total, 4) if total else None


def _flags(m):
    flags = []
    if m["growth_h2_vs_h1"] < -0.05:
        flags.append("revenue declining half-over-half")
    if m["ebitda_margin_avg"] < 0.05:
        flags.append("thin EBITDA margins")
    if m["gross_margin_vol"] > 0.08:
        flags.append("unstable gross margin")
    if m["revenue_vol"] > 0.25:
        flags.append("high revenue volatility")
    if m["worst_month_drop"] < -0.35:
        flags.append(f"severe single-month revenue drop ({m['worst_month_drop']:.0%})")
    if m.get("top_customer_share", 0) and m["top_customer_share"] > 0.25:
        flags.append(f"customer concentration {m['top_customer_share']:.0%}")
    if m["months"] < 12:
        flags.append("under 12 months of financials")
    return flags


def _score(m):
    score = 60.0
    score += min(20, max(-20, m["growth_h2_vs_h1"] * 100))
    score += min(15, m["ebitda_margin_avg"] * 60)
    score -= len(m["flags"]) * 7
    return max(0.0, min(100.0, round(score, 1)))


# ---------------------------------------------------------------- the memo

MEMO_PROMPT = """You are the diligence analyst at Airbank by Finsider, the
AI-native investment bank. Write the {kind} memo for this target. Every number
you cite MUST come from the computed metrics below — never invent figures.
Tight, opinionated markdown: # title, short sections, and end with
"## Verdict" — proceed / pass / needs-more-data, with the 2 facts that most
drive it. Under 400 words.

Target: {company} ({sector}), sourced via {source}.
Deal notes: {notes}

Computed metrics (the only numbers you may use):
{metrics}"""


def run(state, deal, kind="pre_loi", runner=None):
    """Compute metrics + file the memo. Returns metrics or None if no docs."""
    deal_dir = pipeline.deal_dir(deal["id"])
    months = load_financials(deal_dir)
    if months is None:
        return None
    m = metrics(months, deal_dir)
    label = "pre-LOI screening" if kind == "pre_loi" else "post-LOI quality-of-earnings"
    prompt = MEMO_PROMPT.format(kind=label, company=deal["company"],
                                sector=deal["sector"], source=deal["source"],
                                notes=deal.get("notes", ""),
                                metrics=json.dumps(m, indent=2))
    try:
        memo = (runner or _claude)(prompt).strip()
    except Exception as exc:
        log("diligence-error", f"{deal['company']}: {str(exc)[:60]}")
        return None                       # assertion 14: no memo, deal waits
    if not memo:
        return None
    path = deal_dir / f"{kind}-memo.md"
    path.write_text(memo + "\n")
    research = config.HOME_DIR / "research"
    research.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%Y-%m-%d-%H%M")
    (research / f"{stamp}-{kind}-{deal['id']}.md").write_text(memo + "\n")
    deal["diligence"][kind] = {"score": m["score"], "flags": m["flags"],
                               "memo": str(path), "run_utc": now_utc().isoformat()}
    log("diligence", f"{deal['company']} {label}: score {m['score']}",
        "; ".join(m["flags"]) or "no flags")
    return m


def _claude(prompt):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    out = subprocess.run(["claude", "--print", "--output-format", "text", prompt],
                         capture_output=True, text=True, timeout=MEMO_TIMEOUT_S, env=env)
    if out.returncode != 0:
        raise RuntimeError(out.stdout[:100] or "claude CLI error")
    return out.stdout


# ------------------------------------------- demo financials (assertion 16)

def generate_demo_financials(deal):
    """Deterministic monthly P&L for a simulation deal, so the real engine
    crunches real numbers."""
    deal_dir = pipeline.deal_dir(deal["id"])
    path = deal_dir / "financials.csv"
    if path.exists():
        return path
    seed = int(hashlib.sha256(deal["id"].encode()).hexdigest(), 16)
    base = max(deal.get("revenue", 3_000_000), 600_000) / 12
    drift = ((seed % 21) - 8) / 1000            # -0.8%..+1.2% monthly drift
    rows = ["month,revenue,cogs,opex"]
    level = base
    for i in range(18):
        wobble = 1 + (((seed >> (i % 32)) % 13) - 6) / 100
        level *= 1 + drift
        revenue = level * wobble
        cogs = revenue * (0.42 + ((seed >> 5) % 12) / 100)
        opex = revenue * (0.30 + ((seed >> 9) % 10) / 100)
        rows.append(f"2025-{(i % 12) + 1:02d},{revenue:.0f},{cogs:.0f},{opex:.0f}")
    path.write_text("\n".join(rows) + "\n")
    return path
