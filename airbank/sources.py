"""Deal sources (contract §B). Adapters, not scrapers:

  demo    — simulation deal flow so the bank works out of the box
  inbox   — ~/.airbank/leads/: drop JSON or CSV from ANY tool — SearchFunder
            exports, LinkedIn Sales Navigator lists, Dripify webhooks (via
            Zapier file-out), Origami — the universal integration
  origami / searchfunder / linkedin / dripify — configured connectors: when
            keys/webhooks are wired they feed the same inbox; until then the
            flow board shows them as awaiting configuration

Every lead normalizes to: company, sector, source, revenue, ebitda, contact,
notes. Unknown files never crash the loop (assertion 5)."""
import csv
import hashlib
import json

from . import config
from .state import log, now_utc

LEADS_INBOX = config.HOME_DIR / "leads"
CONNECTORS = ["searchfunder", "linkedin", "dripify", "origami", "referrals"]

# ---- demo flow: deterministic per (day, cycle) so restarts don't duplicate
_ADJ = ["Summit", "Cascade", "Iron", "North", "Blue", "Granite", "Pioneer",
        "Vector", "Atlas", "Beacon", "Crest", "Harbor"]
_NOUN = ["Fabrication", "Logistics", "Dental", "HVAC", "Analytics", "Packaging",
         "Compliance", "Staffing", "Diagnostics", "Foods", "Controls", "Roofing"]
_SUFFIX = ["Co", "Group", "Partners", "Industries", "Systems", "Services"]
_SECTORS = ["manufacturing", "logistics", "healthcare", "software",
            "business services", "consumer"]
_TITLES = ["Owner", "Founder", "CEO", "President"]


def _rng(seed_text):
    return int(hashlib.sha256(seed_text.encode()).hexdigest(), 16)


def demo_leads(state):
    """0–2 fresh demo leads per cycle, deterministic for the cycle slot."""
    slot = now_utc().strftime("%Y-%m-%d-%H") + str(now_utc().minute // 15)
    seen = state.setdefault("demo_slots", [])
    if slot in seen:
        return []
    seen.append(slot)
    del seen[:-96]
    r = _rng(slot)
    leads = []
    for i in range(r % 3):                      # 0, 1 or 2 leads this cycle
        s = _rng(f"{slot}:{i}")
        revenue = 1_000_000 + s % 24_000_000
        sectors = config.MANDATE.get("sectors") or _SECTORS
        lead = {
            "company": f"{_ADJ[s % len(_ADJ)]} {_NOUN[s // 7 % len(_NOUN)]} "
                       f"{_SUFFIX[s // 61 % len(_SUFFIX)]}",
            "sector": sectors[s // 11 % len(sectors)] if s % 4 else _SECTORS[s % len(_SECTORS)],
            "source": CONNECTORS[s // 13 % len(CONNECTORS)],
            "revenue": revenue,
            "ebitda": int(revenue * (0.08 + (s % 30) / 100)),
            "contact": f"{_TITLES[s % len(_TITLES)]}, via platform",
            "notes": "demo deal flow (simulation mode)",
        }
        leads.append(lead)
    return leads


# ---- the inbox: universal escape hatch

def inbox_leads():
    """Consume ~/.airbank/leads/*.json|csv. Processed files get renamed
    .done so nothing is ingested twice."""
    if not LEADS_INBOX.exists():
        return []
    leads = []
    for path in sorted(LEADS_INBOX.iterdir()):
        if path.suffix.lower() not in (".json", ".csv"):
            continue
        try:
            if path.suffix.lower() == ".json":
                raw = json.loads(path.read_text())
                rows = raw if isinstance(raw, list) else raw.get("leads", [raw])
            else:
                rows = list(csv.DictReader(path.read_text().splitlines()))
            for row in rows:
                if row.get("company"):
                    row.setdefault("source", path.stem.split("-")[0] or "inbox")
                    leads.append(row)
            path.rename(path.with_suffix(path.suffix + ".done"))
        except Exception as exc:
            log("source-error", f"{path.name}: {str(exc)[:60]}")
            path.rename(path.with_suffix(path.suffix + ".error"))
    return leads


def gather(state):
    """All new leads this cycle + per-source daily counts (assertion 8)."""
    leads = inbox_leads()
    if config.SIMULATION:
        leads += demo_leads(state)
    stats = state.setdefault("source_stats", {})
    today = now_utc().strftime("%Y-%m-%d")
    for lead in leads:
        s = stats.setdefault(lead.get("source", "inbox"),
                             {"total": 0, "days": {}, "responses": 0})
        s["total"] += 1
        s["days"][today] = s["days"].get(today, 0) + 1
        for key in sorted(s["days"])[:-14]:
            del s["days"][key]
    return leads


def flow_board(state):
    """Per-source view for the DEAL FLOW panel: totals + 14d spark series."""
    stats = state.get("source_stats", {})
    board = []
    for name in CONNECTORS + ["inbox"]:
        s = stats.get(name)
        configured = config.SIMULATION or name == "inbox" or bool(
            config.PRODUCT.get("connectors", {}).get(name))
        days = [s["days"].get(k, 0) for k in sorted(s["days"])] if s else []
        board.append({"name": name, "total": s["total"] if s else 0,
                      "spark": days, "configured": configured,
                      "responses": s.get("responses", 0) if s else 0})
    return board
