"""The deal team: pre-built agents you deploy on demand (contract §G).
Each deployment gathers live pipeline context, briefs a Claude analyst, and
files a timestamped markdown report to ~/.airbank/research/. Reports are
advisory — they never advance a deal."""
import json
import os
import subprocess
from datetime import datetime, timezone

from . import config, pipeline
from .state import load_state, log, save_state

RESEARCH_DIR = config.HOME_DIR / "research"
DEPLOY_TIMEOUT_S = 300

ROSTER = {
    "sourcing": {
        "title": "Sourcing Strategist",
        "desc": "where the next 50 leads come from, channel by channel",
        "brief": """Review the flow board and the mandate. Which sources are
producing, which are dry, and what specific search plays (SearchFunder
queries, Sales Navigator filters, referral asks) would fill the top of the
funnel this week. Be concrete — a play per channel.""",
    },
    "outreach": {
        "title": "Outreach Coach",
        "desc": "reviews sent sequences and response rates, sharpens the copy",
        "brief": """Read the recent outreach activity and per-source response
rates. Diagnose what's working, critique the current sequencing cadence, and
propose one specific improvement to the first-touch angle for this mandate.""",
    },
    "screening": {
        "title": "Screening Analyst",
        "desc": "ranks the live pipeline: where to spend partner time",
        "brief": """Rank the active deals by expected value of partner time:
fit score, stage, diligence findings, momentum. Name the top 3 to push and
any deal that should be killed today, with the reason.""",
    },
    "diligence": {
        "title": "Diligence Lead",
        "desc": "second-look on the latest memos: what would kill each deal",
        "brief": """Take the deals with diligence memos and attack them: for
each, name the single fact most likely to kill it in confirmatory diligence
and what document would confirm or clear it. You are paid to find problems.""",
    },
    "market": {
        "title": "Sector Mapper",
        "desc": "the mandate's landscape: multiples, buyers, timing",
        "brief": """Map the mandate's sectors from the pipeline evidence: where
deal flow clusters, what sizes are surfacing, and how the funnel data should
update our thesis on which sub-sector to concentrate on next month.""",
    },
    "pipeline-coach": {
        "title": "Pipeline Coach",
        "desc": "reads the tape and critiques the process",
        "brief": """Read the recent tape. Critique the PROCESS: are leads dying
for the right reasons, are stages advancing on evidence, did any approval sit
too long. Give the loop one concrete behavioral adjustment.""",
    },
}


def _context():
    """Everything a deal-team agent gets to see, gathered fresh."""
    state = load_state()
    counts, value = pipeline.funnel(state)
    lines = [f"UTC now: {datetime.now(timezone.utc).isoformat()[:16]}",
             f"Mode: {config.MODE}",
             f"Mandate: {json.dumps(config.MANDATE)}",
             f"Funnel counts: {json.dumps(counts)}",
             f"Funnel value (revenue $): { {k: round(v) for k, v in value.items()} }",
             f"Source stats: {json.dumps(state.get('source_stats', {}))[:1500]}"]
    deals = pipeline.active_deals(state)[:15]
    lines.append("Active deals (top 15):")
    for d in deals:
        lines.append(f"  {d['id']} | {d['company']} | {d['sector']} | "
                     f"stage {d['stage']} | fit {d['score']} | "
                     f"rev ${d['revenue']:,.0f} | dd {json.dumps(d.get('diligence', {}))[:200]}")
    log_file = config.STATE_DIR / "log.md"
    if log_file.exists():
        tail = [l for l in log_file.read_text().splitlines() if l.strip()][-40:]
        lines.append("Recent tape:\n" + "\n".join(tail))
    return "\n".join(lines)


PROMPT = """You are the {title} at Airbank by Finsider, the AI-native
investment bank — an agent loop that sources, converts, and diligences
acquisitions 24/7 under a written contract.

{brief}

Ground every claim in the data below — no invented companies, no invented
numbers. Tight, opinionated markdown (a partner will read this): a # title,
short sections, and a final "## Desk view" with 2-3 actionable calls.
Under 500 words.

=== PIPELINE DATA ===
{context}"""


def deploy(name, runner=None):
    """Run one agent now. Returns (report_path, headline)."""
    spec = ROSTER[name]
    prompt = PROMPT.format(title=spec["title"], brief=spec["brief"], context=_context())
    report = (runner or _claude)(prompt)
    if not report or not report.strip():
        raise RuntimeError(f"{name} produced no report")
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = RESEARCH_DIR / f"{stamp}-{name}.md"
    path.write_text(report.strip() + "\n")
    headline = next((l.lstrip("# ").strip() for l in report.splitlines()
                     if l.strip()), spec["title"])
    state = load_state()
    desk = state.setdefault("analyst_desk", {})
    desk[name] = {"last_run_utc": datetime.now(timezone.utc).isoformat(),
                  "report": str(path), "headline": headline[:80]}
    save_state(state)
    log("analyst", f"{name} filed: {headline[:60]}")
    return path, headline


def _claude(prompt):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    out = subprocess.run(
        ["claude", "--print", "--output-format", "text", prompt],
        capture_output=True, text=True, timeout=DEPLOY_TIMEOUT_S, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {out.stdout[:120]}")
    return out.stdout


def reports():
    """Newest first."""
    if not RESEARCH_DIR.exists():
        return []
    return sorted(RESEARCH_DIR.glob("*.md"), reverse=True)
