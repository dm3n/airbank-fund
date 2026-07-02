"""The pipeline: deal model, stage engine, funnel metrics (contract §E).
Deals live inside state.json (3-file rule); artifacts under ~/.airbank/deals/."""
import re
import uuid

from . import config
from .state import log, now_utc

STAGES = ["sourced", "contacted", "engaged", "nda", "pre_loi", "loi",
          "post_loi", "closing", "closed", "dead"]
ACTIVE_STAGES = STAGES[:-2]

STAGE_LABEL = {
    "sourced": "SOURCED", "contacted": "CONTACTED", "engaged": "ENGAGED",
    "nda": "NDA", "pre_loi": "PRE-LOI", "loi": "LOI", "post_loi": "POST-LOI",
    "closing": "CLOSING", "closed": "CLOSED", "dead": "DEAD",
}


def book(state):
    return state.setdefault("pipeline", {"deals": {}})


def normalize(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def fit_score(lead, mandate):
    """Deterministic 0–100 fit against the mandate (contract assertion 6)."""
    score = 50.0
    sectors = [s.lower() for s in mandate.get("sectors", [])]
    if sectors:
        score += 20 if lead.get("sector", "").lower() in sectors else -15
    revenue = float(lead.get("revenue", 0) or 0)
    lo, hi = mandate.get("size_min", 0), mandate.get("size_max", 10**9)
    if revenue:
        score += 20 if lo <= revenue <= hi else -20
    else:
        score -= 5
    if lead.get("ebitda"):
        margin = float(lead["ebitda"]) / max(float(lead.get("revenue", 1) or 1), 1)
        score += 10 if 0.10 <= margin <= 0.45 else -5
    if lead.get("contact"):
        score += 5
    return max(0.0, min(100.0, round(score, 1)))


def add_lead(state, lead):
    """Dedup, score, and file a new lead as a sourced deal. Returns the deal
    or None when it's a duplicate (assertion 7)."""
    deals = book(state)["deals"]
    key = normalize(lead.get("company", ""))
    if not key:
        return None
    for deal in deals.values():
        if normalize(deal["company"]) == key:
            return None
    deal_id = f"{key[:10]}-{uuid.uuid4().hex[:4]}"
    deal = {
        "id": deal_id,
        "company": lead["company"],
        "sector": lead.get("sector", "unknown"),
        "source": lead.get("source", "inbox"),
        "revenue": float(lead.get("revenue", 0) or 0),
        "ebitda": float(lead.get("ebitda", 0) or 0),
        "contact": lead.get("contact", ""),
        "notes": str(lead.get("notes", ""))[:400],
        "stage": "sourced",
        "score": fit_score(lead, config.MANDATE),
        "touches": 0,
        "diligence": {},
        "history": [f"{now_utc().strftime('%m-%d %H:%M')} sourced via {lead.get('source', 'inbox')}"],
        "created_utc": now_utc().isoformat(),
        "updated_utc": now_utc().isoformat(),
    }
    deals[deal_id] = deal
    return deal


def advance(state, deal, to_stage, reason):
    """One stage at a time, always logged (assertions 17–19)."""
    frm = deal["stage"]
    if to_stage != "dead" and STAGES.index(to_stage) != STAGES.index(frm) + 1:
        raise ValueError(f"stage skip refused: {frm} -> {to_stage}")
    deal["stage"] = to_stage
    deal["updated_utc"] = now_utc().isoformat()
    deal["history"].append(f"{now_utc().strftime('%m-%d %H:%M')} {frm} → {to_stage}: {reason}")
    log("stage", f"{deal['company']} {frm} → {to_stage}", reason)


def funnel(state):
    counts = {stage: 0 for stage in STAGES}
    value = {stage: 0.0 for stage in STAGES}
    for deal in book(state)["deals"].values():
        counts[deal["stage"]] += 1
        value[deal["stage"]] += deal.get("revenue", 0.0)
    return counts, value


def active_deals(state):
    return sorted((d for d in book(state)["deals"].values()
                   if d["stage"] in ACTIVE_STAGES),
                  key=lambda d: (-STAGES.index(d["stage"]), -d["score"]))


def find(state, needle):
    needle = needle.lower()
    for deal in book(state)["deals"].values():
        if needle in deal["id"] or needle in deal["company"].lower():
            return deal
    return None


def deal_dir(deal_id):
    path = config.HOME_DIR / "deals" / deal_id
    path.mkdir(parents=True, exist_ok=True)
    return path
