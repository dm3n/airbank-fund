"""The loop: gather → reason → act → verify. One cycle per invocation;
launchd provides the repeat. Crash-resume from the 3 state files alone.

Each cycle the bank: ingests new leads (§B), advances every active deal one
honest step (§C, §D, §E) — outreach drafts, simulated or approved sends,
follow-ups, financials requests, pre-LOI diligence, LOI (approval-gated in
live), post-LOI QoE, closing — then the Evaluator grades the cycle."""
import traceback

from . import approvals, config, diligence, outreach, pipeline, sources
from .evaluator import evaluate_cycle
from .state import load_state, log, now_utc, roll_day, save_state


def run_cycle():
    state = load_state()
    cycle = {"errors": [], "new_leads": 0, "advanced": [], "outreach": 0,
             "memos": 0, "waiting": 0, "events": []}
    try:
        _gather_reason_act(state, cycle)
        state["consecutive_failures"] = 0
    except Exception:
        cycle["errors"].append(traceback.format_exc(limit=3))
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        if state["consecutive_failures"] >= 3:
            state["halt"] = True
            state["halt_reason"] = "3 consecutive cycle failures"
            approvals.notify("Airbank halted: 3 consecutive cycle failures. Read the tape.")

    # refresh dashboard aggregates
    counts, value = pipeline.funnel(state)
    active = sum(counts[s] for s in pipeline.ACTIVE_STAGES)
    state["funnel_view"] = {"counts": counts, "value": value, "active": active}
    history = state.setdefault("pipeline_history", [])
    history.append(active)
    del history[:-96]

    score, findings, halted = evaluate_cycle(state, cycle)
    if halted:
        approvals.notify(f"Airbank HALTED by evaluator: {state['halt_reason']}")
    state["last_cycle_utc"] = now_utc().isoformat()
    save_state(state)
    log("cycle", f"{cycle['new_leads']} sourced, {cycle['outreach']} outreach, "
        f"{len(cycle['advanced'])} advanced, {cycle['memos']} memos, score {score}")
    return score, cycle


def _gather_reason_act(state, cycle):
    roll_day(state, None)
    approvals.expire_stale(state)
    if state.get("halt"):
        log("cycle-skip", f"halted: {state.get('halt_reason', '')}")
        return

    # ---- gather: fresh leads in, deduped and scored
    for lead in sources.gather(state)[:config.CAPS["max_leads_per_cycle"]]:
        deal = pipeline.add_lead(state, lead)
        if deal is None:
            continue
        cycle["new_leads"] += 1
        cycle["events"].append(("sourced", deal["company"], deal["source"]))
        if deal["score"] < config.CAPS["min_fit_score"]:
            pipeline.advance(state, deal, "dead",
                             f"fit score {deal['score']} under mandate floor")

    # ---- act on approved external actions first (live mode)
    for approval in approvals.approved_ready(state):
        _execute_approved(state, cycle, approval)

    # ---- reason + act: every active deal takes one honest step
    for deal in pipeline.active_deals(state):
        try:
            _step(state, cycle, deal)
        except Exception as exc:
            cycle["errors"].append(f"{deal['id']}: {exc}")


def _execute_approved(state, cycle, approval):
    deal = pipeline.book(state)["deals"].get(approval["deal_id"])
    if deal is None:
        approvals.mark_done(state, approval["id"])
        return
    if approval["kind"] == "outreach":
        outreach.send(deal, approval["payload"]["sequence"], deal["touches"] + 1)
        outreach.count_send(state)
        deal["touches"] += 1
        if deal["stage"] == "sourced":
            pipeline.advance(state, deal, "contacted", "approved outreach in the outbox")
        cycle["outreach"] += 1
        cycle["events"].append(("contacted", deal["company"], approval["id"]))
    elif approval["kind"] == "loi":
        _issue_loi(state, deal, approval)
        cycle["advanced"].append(deal["id"])
    approvals.mark_done(state, approval["id"])


def _step(state, cycle, deal):
    stage = deal["stage"]

    if stage == "sourced":
        if not outreach.under_daily_cap(state):
            cycle["waiting"] += 1
            return
        sequence = outreach.draft(deal)
        deal["sequence"] = sequence
        if config.SIMULATION:
            outreach.send(deal, sequence, 1)
            outreach.count_send(state)
            deal["touches"] = 1
            pipeline.advance(state, deal, "contacted", "outreach sent (simulation)")
            cycle["outreach"] += 1
            cycle["events"].append(("contacted", deal["company"], deal["source"]))
        else:
            approvals.create(state, "outreach", deal, {"sequence": sequence},
                             f"3-touch sequence drafted for {deal['contact'] or 'owner'}")

    elif stage == "contacted":
        if config.SIMULATION and outreach.simulate_response(deal):
            pipeline.advance(state, deal, "engaged", "owner replied — call scheduled")
            _bump_response(state, deal)
            cycle["events"].append(("engaged", deal["company"], ""))
        elif deal["touches"] >= config.CAPS["max_touches_per_lead"]:
            pipeline.advance(state, deal, "dead", "no response after 3 touches — resting")
        elif config.SIMULATION and outreach.under_daily_cap(state):
            outreach.send(deal, deal.get("sequence", ""), deal["touches"] + 1)
            outreach.count_send(state)
            deal["touches"] += 1
            cycle["outreach"] += 1

    elif stage == "engaged":
        pipeline.advance(state, deal, "nda", "NDA executed — financials requested")
        cycle["advanced"].append(deal["id"])

    elif stage == "nda":
        if config.SIMULATION:
            diligence.generate_demo_financials(deal)
        m = diligence.run(state, deal, "pre_loi")
        if m is None:
            deal["next_action"] = "waiting on financials → " + str(
                pipeline.deal_dir(deal["id"]) / "financials.csv")
            cycle["waiting"] += 1
            return
        cycle["memos"] += 1
        if m["score"] >= config.CAPS["min_diligence_score"]:
            pipeline.advance(state, deal, "pre_loi",
                             f"diligence score {m['score']} — cleared for LOI work")
            cycle["events"].append(("pre_loi", deal["company"], f"score {m['score']}"))
        else:
            pipeline.advance(state, deal, "dead",
                             f"diligence score {m['score']} — pass")

    elif stage == "pre_loi":
        terms = _draft_terms(deal)
        if config.SIMULATION:
            approval = {"id": "sim", "payload": terms}
            _issue_loi(state, deal, approval)
            cycle["advanced"].append(deal["id"])
        else:
            existing = [a for a in state.get("pending_approvals", [])
                        if a["deal_id"] == deal["id"] and a["kind"] == "loi"
                        and a["status"] == "pending"]
            if not existing:
                approvals.create(state, "loi", deal, terms,
                                 f"LOI at {terms['multiple']}x EBITDA ≈ ${terms['offer']:,.0f}")
            cycle["waiting"] += 1

    elif stage == "loi":
        if config.SIMULATION:
            diligence.generate_demo_financials(deal)
        m = diligence.run(state, deal, "post_loi")
        if m is None:
            cycle["waiting"] += 1
            return
        cycle["memos"] += 1
        pipeline.advance(state, deal, "post_loi", f"QoE complete — score {m['score']}")
        cycle["advanced"].append(deal["id"])

    elif stage == "post_loi":
        checklist = pipeline.deal_dir(deal["id"]) / "closing-checklist.md"
        if not checklist.exists():
            checklist.write_text(CLOSING_CHECKLIST.format(company=deal["company"]))
        pipeline.advance(state, deal, "closing", "into legal — closing checklist filed")
        cycle["advanced"].append(deal["id"])

    elif stage == "closing":
        if config.SIMULATION:
            pipeline.advance(state, deal, "closed", "signed and funded (simulation)")
            cycle["events"].append(("closed", deal["company"], ""))
            cycle["advanced"].append(deal["id"])
        else:
            deal["next_action"] = "legal in flight — mark closed from the desk when signed"
            cycle["waiting"] += 1


def _draft_terms(deal):
    pre = deal["diligence"].get("pre_loi", {})
    multiple = 4.5 if pre.get("score", 60) >= 75 else 3.8
    ebitda = deal.get("ebitda") or deal.get("revenue", 0) * 0.12
    return {"multiple": multiple, "offer": round(ebitda * multiple, 0)}


def _issue_loi(state, deal, approval):
    """Two-layer check (assertion 18): live mode requires the approval here
    even though the stage engine gated it upstream."""
    if config.LIVE and approval.get("status") not in ("approved",):
        raise RuntimeError("LOI without approval refused at the document layer")
    terms = approval["payload"]
    path = pipeline.deal_dir(deal["id"]) / "loi.md"
    path.write_text(f"# LOI — {deal['company']}\n\nOffer: ${terms['offer']:,.0f} "
                    f"({terms['multiple']}x EBITDA)\nIssued {now_utc().isoformat()[:16]} "
                    f"by {config.MANDATE.get('firm', 'Airbank')}\n")
    pipeline.advance(state, deal, "loi", f"LOI issued at {terms['multiple']}x")


def _bump_response(state, deal):
    stats = state.get("source_stats", {}).get(deal["source"])
    if stats:
        stats["responses"] = stats.get("responses", 0) + 1


CLOSING_CHECKLIST = """# Closing checklist — {company}

- [ ] Purchase agreement drafted and redlined
- [ ] Financing commitment confirmed
- [ ] Working capital peg agreed
- [ ] Key-employee agreements signed
- [ ] Assignment of contracts / change-of-control consents
- [ ] Funds flow memo approved
"""
