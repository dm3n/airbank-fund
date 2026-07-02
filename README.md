<h1 align="center">Airbank by Finsider</h1>

<p align="center"><b>The AI-native investment bank that lives in your terminal.</b></p>

A 24/7 agent loop that runs the entire M&A pipeline: automated prospecting
across deal sources, personalized outreach, lead conversion, Finsider-grade
pre-LOI financial diligence, LOI, post-LOI QoE, and close. In live mode
nothing external ever happens without your approval.

Built on the LOOPS architecture: three roles (Planner / Generator /
Evaluator), exactly three state files, restart over patch-piling, every cycle
graded against a written contract. The binding spec is
[`airbank/contract.md`](airbank/contract.md) — run `airbank contract`.

## Install

```bash
brew install dm3n/tap/airbank
```

or `pipx install git+https://github.com/dm3n/airbank-fund`.

## Sixty seconds to a running bank

Type `airbank`. The first run opens the onboarding wizard — pick a mode, set
your mandate (firm, sectors, deal size) — the 24/7 loop auto-starts, and you
land on **the terminal**: a full-screen Bloomberg-style live view with a
Claude Code-style chat bar as the primary way to drive everything.

Panels: **DEAL FLOW** (per-source lead volume with heat-mapped 14-day
sparklines) · **PIPELINE** (the funnel, active revenue, top of book) ·
**CAMPAIGN & GUARDRAILS** · **DEAL TEAM** · **TAPE** (the loop thinking, live).
The ticker strip streams pipeline events. Three views — dashboard, hybrid
(desk chat replaces just the tape), full chat — with the shark-blue standard
profile, breathing thinking indicator, and slash autocomplete.

## Two modes

**Simulation** (default): hands-free demo deal flow — the full pipeline moves
24/7 with zero setup, and demo deals get generated financials so the real
diligence engine crunches real numbers end to end.

**Live**: real leads, human control. Drop exports from ANY tool into
`~/.airbank/leads/` (JSON or CSV with a `company` column) — SearchFunder
exports, LinkedIn Sales Navigator lists, Dripify/Zapier webhooks, Origami.
Every outreach draft and every LOI queues as a pending approval; approved
messages land in `~/.airbank/outbox/` for your connected sender. Airbank
never emails anyone directly.

## The pipeline

```
sourced → contacted → engaged → nda → pre_loi → loi → post_loi → closing → closed
```

Each cycle the loop ingests and dedupes new leads, scores them against your
mandate, drafts personalized outreach (volume-capped in code: 25
first-touches/day, 3 touches per lead), advances every active deal one honest
step, and runs the **Finsider diligence engine** when financials land in a
deal folder: revenue trend, margins and volatility, customer concentration —
computed deterministically in Python; the LLM only narrates the memo. No
documents, no memo — the deal waits.

## Talk to your bank

The chat bar drives everything, exactly like Claude Code. Ask "which deal
should I push today?" and the answer streams in grounded in the live funnel.
The desk can act — run cycles, deploy the deal team, fire diligence — via the
ACTION protocol; approvals stay explicit CLI commands.

```
/run                     one cycle now
/deploy screening        the deal team: sourcing · outreach · screening ·
                         diligence · market · pipeline-coach
/diligence <deal>        the Finsider engine, on demand
```

## Operating it

```bash
airbank status            # funnel, top of book, pending approvals
airbank deals             # every deal in the book
airbank deal <id>         # one deal: history, memos, folder
airbank diligence <id>    # run the engine now
airbank approve <id>      # release pending outreach / an LOI
airbank research          # read the latest team report
tail -f ~/.airbank/state/log.md   # the tape — read this to debug
```

State lives in `~/.airbank/` — three files, designed for crash-resume. Deal
artifacts (financials in, memos and LOIs out) live under `~/.airbank/deals/`.

## Development

```bash
python3 -m unittest discover tests
```
