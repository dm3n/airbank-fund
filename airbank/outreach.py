"""Outreach (contract §C): LLM-drafted, personalized from the lead's own
fields, volume-capped in code. Live mode queues approvals; approved messages
land in ~/.airbank/outbox/ for the connected sender (Dripify/Zapier/email
tool watches that folder) — Airbank never emails anyone directly. Simulation
simulates sends and responses so the funnel moves 24/7."""
import hashlib
import os
import subprocess

from . import config
from .state import log, now_utc

OUTBOX = config.HOME_DIR / "outbox"
DRAFT_TIMEOUT_S = 180

PROMPT = """You are the outreach writer at Airbank by Finsider, an AI-native
investment bank sourcing acquisitions for its mandate. Draft a 3-touch
sequence to the owner of this business. Warm, specific, respectful of their
time; never pushy, never generic. Use ONLY the facts below — do not invent
anything about the company. Each touch under 90 words. Format exactly:

TOUCH 1:
<text>
TOUCH 2:
<text>
TOUCH 3:
<text>

Lead: {company} — {sector}, ~${revenue:,.0f} revenue, contact: {contact}.
Sourced via {source}. Notes: {notes}
Our mandate: {mandate}."""


def draft(deal, runner=None):
    prompt = PROMPT.format(company=deal["company"], sector=deal["sector"],
                           revenue=deal.get("revenue", 0), contact=deal.get("contact", "unknown"),
                           source=deal["source"], notes=deal.get("notes", ""),
                           mandate=", ".join(config.MANDATE.get("sectors", [])) or "lower middle market")
    text = (runner or _claude)(prompt).strip()
    if "TOUCH 1:" not in text:
        raise RuntimeError("draft came back unstructured")
    return text


def send(deal, sequence, touch=1):
    """'Sending' = writing to the outbox for the connected sender."""
    OUTBOX.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%Y-%m-%d-%H%M")
    path = OUTBOX / f"{stamp}-{deal['id']}-touch{touch}.md"
    path.write_text(f"# {deal['company']} — touch {touch}\n"
                    f"contact: {deal.get('contact', '')}\n\n{sequence}\n")
    return path


def today_sends(state):
    today = now_utc().strftime("%Y-%m-%d")
    counters = state.setdefault("outreach_days", {})
    for key in sorted(counters)[:-7]:
        del counters[key]
    return counters, today


def under_daily_cap(state):
    counters, today = today_sends(state)
    return counters.get(today, 0) < config.CAPS["max_outreach_per_day"]


def count_send(state):
    counters, today = today_sends(state)
    counters[today] = counters.get(today, 0) + 1


def simulate_response(deal):
    """Deterministic 'did they reply to this touch?' — weighted by fit score
    (assertion 11)."""
    seed = int(hashlib.sha256(f"{deal['id']}:{deal['touches']}".encode()).hexdigest(), 16)
    threshold = 0.10 + deal["score"] / 250            # 10%..50% per touch
    return (seed % 1000) / 1000 < threshold


def _claude(prompt):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    out = subprocess.run(["claude", "--print", "--output-format", "text", prompt],
                         capture_output=True, text=True, timeout=DRAFT_TIMEOUT_S, env=env)
    if out.returncode != 0:
        raise RuntimeError(out.stdout[:100] or "claude CLI error")
    return out.stdout
