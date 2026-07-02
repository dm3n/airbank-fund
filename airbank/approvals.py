"""Pending actions: nothing external leaves the building without a human in
live mode (contract §C, §E). Outreach approvals expire in 24h, LOIs in 72h.
Simulation auto-approves so the funnel runs hands-free."""
import json
import subprocess
import urllib.request
import uuid
from datetime import datetime, timedelta

from .config import SIMULATION, SLACK_WEBHOOK_URL
from .state import log, now_utc

TTL_HOURS = {"outreach": 24, "loi": 72}


def create(state, kind, deal, payload, summary):
    approval = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "deal_id": deal["id"],
        "company": deal["company"],
        "created_utc": now_utc().isoformat(),
        "status": "auto-approved" if SIMULATION else "pending",
        "payload": payload,
        "summary": summary[:200],
    }
    if not SIMULATION:
        state.setdefault("pending_approvals", []).append(approval)
        notify(f"Airbank {kind} pending [{approval['id']}]: {deal['company']} — "
               f"{summary[:120]}\nApprove: airbank approve {approval['id']}")
        log("approval-pending", f"{kind} · {deal['company']} [{approval['id']}]", summary)
    return approval


def resolve(state, approval_id, decision):
    for approval in state.get("pending_approvals", []):
        if approval["id"] == approval_id and approval["status"] == "pending":
            approval["status"] = decision
            approval["resolved_utc"] = now_utc().isoformat()
            log(f"approval-{decision}", f"{approval['kind']} · {approval['company']} [{approval_id}]")
            return approval
    return None


def expire_stale(state):
    for approval in state.get("pending_approvals", []):
        if approval["status"] != "pending":
            continue
        ttl = timedelta(hours=TTL_HOURS.get(approval["kind"], 24))
        if now_utc() - datetime.fromisoformat(approval["created_utc"]) > ttl:
            approval["status"] = "expired"
            log("approval-expired", f"{approval['kind']} · {approval['company']} [{approval['id']}]")


def approved_ready(state, kind=None):
    return [a for a in state.get("pending_approvals", [])
            if a["status"] == "approved" and (kind is None or a["kind"] == kind)]


def mark_done(state, approval_id):
    for approval in state.get("pending_approvals", []):
        if approval["id"] == approval_id:
            approval["status"] = "done"


def notify(message):
    if SLACK_WEBHOOK_URL:
        try:
            req = urllib.request.Request(
                SLACK_WEBHOOK_URL, data=json.dumps({"text": message}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
            return
        except OSError:
            pass
    try:
        safe = message.replace('"', "'").replace("\n", " ")[:200]
        subprocess.run(["osascript", "-e",
                        f'display notification "{safe}" with title "Airbank by Finsider"'],
                       capture_output=True, timeout=10)
    except OSError:
        pass
