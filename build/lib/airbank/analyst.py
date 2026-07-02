"""Generator, narrative half: LLM analyst gate over systematic candidates.
Calls the `claude` CLI (same pattern as pkb-process.py). Fail-safe: any
failure drops the proposal — contract assertions 13–14."""
import json
import os
import subprocess

from .config import ANALYST_TIMEOUT_S

PROMPT = """You are the risk-aware markets analyst for Airbank by Finsider, an
AI-native fund. A systematic strategy proposes this trade:

{proposal}

Recent headlines for context (may be empty):
{headlines}

Judge the trade on regime fit, crowding, obvious news risk, and whether the
systematic rationale still holds. You are the gate, not the cheerleader:
veto anything you would not defend to an investment committee.

Reply with ONLY a JSON object, no prose, no code fences:
{{"verdict": "proceed" or "veto", "conviction": 0.0-1.0, "thesis": "one falsifiable sentence"}}"""


def review(proposal, headlines):
    """Return verdict dict or None (None == drop the proposal)."""
    prompt = PROMPT.format(
        proposal=json.dumps(proposal, indent=2),
        headlines="\n".join(f"- {h}" for h in headlines) or "(none)",
    )
    # Strip API-key vars so the CLI uses the logged-in claude.ai account even
    # when invoked from inside another agent session.
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        out = subprocess.run(
            ["claude", "--print", "--output-format", "text", prompt],
            capture_output=True, text=True, timeout=ANALYST_TIMEOUT_S, env=env,
        )
        if out.returncode != 0:
            return None
        return _parse(out.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _parse(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        verdict = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if verdict.get("verdict") not in ("proceed", "veto"):
        return None
    try:
        conviction = float(verdict.get("conviction", 0))
    except (TypeError, ValueError):
        return None
    verdict["conviction"] = max(0.0, min(1.0, conviction))
    verdict["thesis"] = str(verdict.get("thesis", ""))[:300]
    return verdict
