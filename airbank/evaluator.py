"""Evaluator role: assumes every cycle is broken and tries to prove it against
contract.md v3. Separate from the Generator (assertion 21); owns the halt
switch. Rubric (assertion 20): discipline 0.4, data 0.2, artifacts 0.2,
hygiene 0.2."""
import json

from . import config, pipeline
from .state import LOG_FILE, STATE_FILE, log

CRITICAL = "CRITICAL"
WARN = "WARN"


def evaluate_cycle(state, cycle):
    findings = []

    # --- pipeline discipline (0.4)
    discipline = 1.0
    counters = state.get("outreach_days", {})
    if counters and max(counters.values()) > config.CAPS["max_outreach_per_day"]:
        findings.append((CRITICAL, "daily outreach cap breached"))
        discipline = 0.0
    for deal in pipeline.book(state)["deals"].values():
        if deal["touches"] > config.CAPS["max_touches_per_lead"]:
            findings.append((CRITICAL, f"{deal['company']}: touch cap breached"))
            discipline = 0.0
        if config.LIVE and deal["stage"] in ("loi", "post_loi", "closing", "closed"):
            history = " ".join(deal["history"])
            if "LOI issued" in history and not any(
                    a["deal_id"] == deal["id"] and a["kind"] == "loi"
                    and a["status"] in ("approved", "done")
                    for a in state.get("pending_approvals", [])):
                findings.append((CRITICAL, f"{deal['company']}: LOI without approval"))
                discipline = 0.0

    # --- data integrity (0.2)
    data_score = 1.0
    names = [pipeline.normalize(d["company"])
             for d in pipeline.book(state)["deals"].values()]
    if len(names) != len(set(names)):
        findings.append((WARN, "duplicate companies in the pipeline"))
        data_score = 0.5
    for deal in pipeline.book(state)["deals"].values():
        if not deal.get("company") or deal.get("score") is None:
            findings.append((WARN, f"incomplete lead record {deal.get('id')}"))
            data_score = min(data_score, 0.5)

    # --- artifact quality (0.2)
    artifact_score = 1.0
    for deal_id in cycle.get("advanced", []):
        deal = pipeline.book(state)["deals"].get(deal_id, {})
        if deal.get("stage") in ("pre_loi", "loi", "post_loi") \
                and not deal.get("diligence"):
            findings.append((WARN, f"{deal.get('company')}: advanced without diligence"))
            artifact_score = 0.5

    # --- hygiene (0.2)
    hygiene = 1.0
    try:
        json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    except json.JSONDecodeError:
        findings.append((CRITICAL, "state.json is not valid JSON"))
        hygiene = 0.0
    if not LOG_FILE.exists():
        findings.append((WARN, "tape missing"))
        hygiene = min(hygiene, 0.5)
    if cycle.get("errors"):
        findings.append((WARN, f"cycle errors: {len(cycle['errors'])}"))
        hygiene = min(hygiene, 0.5)

    score = round(0.4 * discipline + 0.2 * data_score
                  + 0.2 * artifact_score + 0.2 * hygiene, 3)
    halt = any(level == CRITICAL for level, _ in findings)
    body = f"score={score}\n" + "\n".join(f"- {lvl}: {msg}" for lvl, msg in findings)
    log("evaluate", f"cycle score {score}" + (" HALT" if halt else ""), body)
    if halt:
        state["halt"] = True
        state["halt_reason"] = "; ".join(m for lvl, m in findings if lvl == CRITICAL)
    return score, findings, halt
