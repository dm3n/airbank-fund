# Airbank by Finsider — Loop Contract v3

The AI-native investment bank. This contract is what gets graded. The
Evaluator halts the loop on any CRITICAL breach; the build is judged only
against the assertions below.

## Mission

A 24/7 agent loop (gather → reason → act → verify → repeat) that runs the
entire M&A pipeline: automated prospecting across deal sources, personalized
outreach, lead conversion, Finsider-style pre-LOI financial diligence, LOI,
post-LOI QoE, and close. NOTHING leaves the building without approval in
live mode: outreach messages and LOIs are pending actions until a human
approves. Simulation mode runs the full pipeline hands-free on demo deal
flow so the product works out of the box.

## Roles (loops.md rule 2)

- **Planner** — this contract + the mandate (sectors, deal size, guardrails
  set in onboarding). Never contacts a lead.
- **Generator** — sourcing, outreach drafting, diligence analysis. Forbidden
  from grading its own work.
- **Evaluator** — `evaluator.py`. Assumes every cycle is broken and tries to
  prove it. Owns the halt switch.

## State (rule 4) — exactly 3 files, crash-resume

1. `contract.md` (this file) — never mutated by the loop.
2. `state/state.json` — the pipeline (all deals + stages), source stats,
   pending approvals, day counters, halt flag.
3. `state/log.md` — append-only tape, `## [YYYY-MM-DD HH:MM] op | title`.

Deal artifacts (financials in, memos out) live under `~/.airbank/deals/<id>/`
— deliverables, not loop state.

## Guardrails (CRITICAL — Evaluator halts on breach)

| Guardrail | Value |
|---|---|
| Mode default | `simulation` (live requires explicit onboarding choice) |
| External sends | In live mode NO outreach is sent autonomously — drafts queue as pending approvals; approved messages land in `~/.airbank/outbox/` for the connected sender. Airbank never emails anyone directly. |
| LOI | Issuing an LOI is always an approval in live mode (72h expiry) |
| Outreach volume | ≤ 25 new first-touches per day; ≤ 3 touches per lead, then the lead rests |
| Diligence honesty | Every number in a diligence memo traces to the provided financials — metrics are computed deterministically in code; the LLM only narrates. No documents → no memo, the deal waits. |
| Stage discipline | Deals advance one stage at a time through: sourced → contacted → engaged → nda → pre_loi → loi → post_loi → closing → closed (or → dead). No skipping. |

## Testable assertions

### A. Loop mechanics
1. One cycle runs end-to-end via `airbank run` and exits 0; crash-resume
   from the 3 state files alone.
2. Every cycle appends exactly one `cycle` entry to the tape; `state.json`
   stays valid JSON (atomic writes).
3. A halted loop gathers and verifies but sources, contacts, and advances
   nothing.
4. `airbank status` renders the pipeline without touching the network.

### B. Sourcing (Generator)
5. Sources are adapters: `demo` (simulation deal flow), `inbox`
   (~/.airbank/leads/ — drop JSON/CSV from ANY tool: SearchFunder exports,
   LinkedIn Sales Navigator, Dripify webhooks, Origami, Zapier), and
   configured connectors. Unknown files never crash the loop.
6. Every sourced lead gets: company, sector, source, size estimate, contact,
   and a deterministic fit score (0–100) against the mandate before any
   agent touches it.
7. Duplicate companies (same normalized name) are merged, never re-added.
8. Per-source daily lead counts are tracked for the flow board (14-day ring).

### C. Outreach (Generator)
9. Outreach sequences are drafted by the LLM, personalized from the lead's
   own fields — grounded, no invented facts about the target.
10. Live mode: drafts become pending approvals; `airbank approve <id>`
    moves the message to the outbox. Nothing external happens before that.
11. Simulation: sends are simulated and responses arrive probabilistically,
    weighted by fit score — the funnel moves 24/7 hands-free.
12. Volume guardrails (assertion table) are enforced in code, not prompts.

### D. Diligence (the Finsider engine)
13. Pre-LOI diligence runs when financials exist in the deal folder:
    revenue trend & growth, gross/EBITDA margins and volatility, customer
    concentration, working-capital signals — computed in Python.
14. The memo cites the computed metrics, scores the deal 0–100, and ends
    proceed / pass / needs-more-data. LLM failure → no memo, deal waits.
15. Post-LOI produces the deeper QoE-style report over the same engine and
    a closing checklist; both file to the deal folder and the research desk.
16. In simulation, demo deals get generated monthly P&L files so the real
    diligence engine crunches real numbers end to end.

### E. Stage engine
17. Advancement criteria are explicit per stage and logged with reasons;
    the tape shows every transition.
18. LOI issuance in live mode requires an unexpired approval — checked at
    the stage engine AND where the LOI document is written (two layers).
19. Dead deals keep their history; nothing is deleted.

### F. Evaluator (rule 6 rubric)
20. Scores every cycle in [0,1]: pipeline discipline (0.4), data integrity
    (0.2), artifact quality (0.2), process hygiene (0.2). CRITICAL → halt.
21. Evaluator never generates pipeline work; the Generator never scores.
22. All unit tests pass: `python3 -m unittest discover tests`.

### G. The terminal (carried from v2 — the UX stays)
23. Bare `airbank` on a TTY opens the terminal; first run onboards
    seamlessly (wizard → auto-start the 24/7 loop → land on the dashboard).
24. Three views — dashboard, hybrid, chat — same shark-blue standard
    profile, breathing thinker, heat-mapped graphs, chat-bar-first
    interaction, slash autocomplete. Panels: DEAL FLOW (sources),
    PIPELINE (funnel + top deals), CAMPAIGN · GUARDRAILS, DEAL TEAM,
    TAPE. The ticker strip streams pipeline events.
25. The desk chat is grounded in the live pipeline and can drive the
    terminal via the ACTION protocol; approvals stay explicit CLI commands.
26. The deal team (analyst roster) deploys on demand and files markdown
    reports to ~/.airbank/research/; reports are advisory only.

### H. Ops
27. Secrets only from env files (0600); never printed, never committed.
    Connector keys (Origami, etc.) are optional — the inbox adapter is the
    universal integration until keys are wired.

## Rubric calibration (rule 6)

Good cycle: fresh leads deduped and scored, outreach personalized and inside
volume caps, stages advanced with logged reasons, memos grounded in computed
numbers. Slop cycle: generic outreach, invented company facts, stage jumps,
memos with numbers that trace to nothing, silent guardrail breaches.

## Restart policy (rule 5)

3 consecutive cycle failures → halt and notify, never patch-pile. Humans are
inserted only when this contract is wrong.
