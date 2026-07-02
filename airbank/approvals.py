"""Approval gate: every live trade is a pending action until Daniel approves.
Paper mode auto-approves. Approvals expire (contract cap table). Notify via
Slack webhook when configured, else macOS notification."""
import json
import subprocess
import urllib.request
import uuid
from datetime import datetime, timedelta

from .config import CAPS, LIVE, SLACK_WEBHOOK_URL
from .state import log, now_utc


def create(state, order, verdict):
    approval = {
        "id": uuid.uuid4().hex[:8],
        "created_utc": now_utc().isoformat(),
        "status": "auto-approved" if not LIVE else "pending",
        "order": order,
        "thesis": verdict.get("thesis", ""),
        "conviction": verdict.get("conviction", 0),
    }
    if LIVE:
        state["pending_approvals"].append(approval)
        notify(
            f"Airbank trade pending approval [{approval['id']}]: "
            f"{order['side']} ${order['notional_usd']:.0f} {order['symbol']} — "
            f"{approval['thesis']}\n"
            f"Approve: python3 cli.py approve {approval['id']}"
        )
        log("approval-pending", f"{order['side']} {order['symbol']} ${order['notional_usd']:.0f}",
            approval["thesis"])
    return approval


def resolve(state, approval_id, decision):
    """decision: approved | rejected. Returns the approval or None."""
    for approval in state["pending_approvals"]:
        if approval["id"] == approval_id and approval["status"] == "pending":
            approval["status"] = decision
            approval["resolved_utc"] = now_utc().isoformat()
            log(f"approval-{decision}", f"{approval['order']['symbol']} [{approval_id}]")
            return approval
    return None


def expire_stale(state):
    ttl = timedelta(hours=CAPS["approval_ttl_hours"])
    for approval in state["pending_approvals"]:
        if approval["status"] != "pending":
            continue
        created = datetime.fromisoformat(approval["created_utc"])
        if now_utc() - created > ttl:
            approval["status"] = "expired"
            log("approval-expired", f"{approval['order']['symbol']} [{approval['id']}]")


def approved_ready(state):
    """Approved and unexpired approvals awaiting execution."""
    return [a for a in state["pending_approvals"] if a["status"] == "approved"]


def mark_executed(state, approval_id):
    for approval in state["pending_approvals"]:
        if approval["id"] == approval_id:
            approval["status"] = "executed"


def notify(message):
    if SLACK_WEBHOOK_URL:
        try:
            req = urllib.request.Request(
                SLACK_WEBHOOK_URL,
                data=json.dumps({"text": message}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=15)
            return
        except OSError:
            pass
    try:
        safe = message.replace('"', "'").replace("\n", " ")[:200]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe}" with title "Airbank by Finsider"'],
            capture_output=True, timeout=10,
        )
    except OSError:
        pass
