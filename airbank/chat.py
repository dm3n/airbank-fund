"""Desk chat: the conversational brain of the Airbank terminal. Fast context
(pipeline state, no extra network), claude CLI backend, and an ACTION protocol
so the assistant can drive the bank — every external action still flows
through the approval layer."""
import json
import os
import re
import subprocess

from . import config, pipeline
from .state import load_state

CHAT_TIMEOUT_S = 120
ACTION_RE = re.compile(r"^ACTION:\s*(.+?)\s*$", re.MULTILINE)

PROMPT = """You are the desk assistant living inside the Airbank terminal —
Airbank by Finsider, the AI-native investment bank. An agent loop runs the
whole M&A pipeline 24/7: sourcing across channels, personalized outreach,
Finsider-grade pre-LOI diligence, LOI, post-LOI QoE, close. In live mode
nothing external happens without a human approval.

Voice: a sharp, warm banking-desk partner. Concise by default. Ground every
company, number, and stage in the pipeline data below — never invent deals
or figures. Plain text (no markdown headers); short lines read best in the
terminal. Use `backticks` around deal ids, tickers-of-speech like stage
names, and commands.

You can drive the terminal. If — and only if — the user asks for an action,
end your reply with exactly one final line:
ACTION: run
ACTION: deploy <sourcing|outreach|screening|diligence|market|pipeline-coach>
ACTION: diligence <deal id or company>
Approvals (outreach, LOIs) can NOT be given here — those need
`airbank approve <id>`.

=== PIPELINE DATA (live) ===
{context}

=== CONVERSATION SO FAR ===
{history}

User: {message}
Assistant:"""


def _context():
    state = load_state()
    counts, value = pipeline.funnel(state)
    lines = [f"mode: {config.MODE}",
             f"mandate: {json.dumps(config.MANDATE)}",
             f"halt: {state.get('halt')} {state.get('halt_reason', '')}",
             f"funnel: {json.dumps(counts)}",
             f"active pipeline revenue: ${sum(value[s] for s in pipeline.ACTIVE_STAGES):,.0f}"]
    pending = [a for a in state.get("pending_approvals", []) if a["status"] == "pending"]
    if pending:
        lines.append("pending approvals: " + json.dumps(
            [{"id": a["id"], "kind": a["kind"], "company": a["company"],
              "summary": a["summary"]} for a in pending]))
    for d in pipeline.active_deals(state)[:12]:
        lines.append(f"deal {d['id']}: {d['company']} | {d['sector']} | {d['stage']} | "
                     f"fit {d['score']} | rev ${d['revenue']:,.0f} | "
                     f"dd {json.dumps(d.get('diligence', {}))[:150]}")
    desk = state.get("analyst_desk", {})
    if desk:
        lines.append("deal team latest: " + json.dumps(
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
    prompt = PROMPT.format(context=_context(), history=convo or "(fresh session)",
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
