# Airbank by Finsider — Loop Contract v2

The world's first AI-native hedge fund. This contract is what gets graded. The
Evaluator halts the loop on any CRITICAL breach; the build is judged only
against the assertions below.

## Mission

A 24/7 loop (gather → reason → act → verify → repeat) that researches crypto
and US equities, generates hybrid alpha (systematic signals gated by an LLM
analyst), and executes through Alpaca. Live trades NEVER execute without
Daniel's explicit per-trade approval. Paper mode may auto-execute.

## Roles (loops.md rule 2)

- **Planner** — `contract.md` + per-cycle regime assessment. Sets universe,
  enabled strategies, risk budget. Never places orders.
- **Generator** — `signals.py` + `analyst.py`. Produces trade proposals.
  Forbidden from grading its own proposals.
- **Evaluator** — `evaluator.py`. Assumes every cycle is broken and tries to
  prove it against this contract. Owns the halt switch.

## State (rule 4) — exactly 3 files, crash-resume

1. `contract.md` (this file) — never mutated by the loop.
2. `state/state.json` — positions view, pending approvals, strategy params,
   daily P&L anchor, halt flag, last-cycle timestamp.
3. `state/log.md` — append-only, `## [YYYY-MM-DD HH:MM] op | title` entries.

## Hard risk caps (CRITICAL — Evaluator halts on breach)

| Cap | Value |
|---|---|
| Mode default | `paper` (live requires `AIRBANK_MODE=live` AND live keys AND `live_ack: true` in state) |
| Live approval | Every live order requires explicit human approval; approvals expire after 4h |
| Max position size | $200 per position (live), $2,000 (paper) |
| Max gross exposure | $1,000 (live), $10,000 (paper) |
| Max trades per day | 10 |
| Daily loss kill switch | -3% of equity → halt, notify, require manual resume |
| Stale data | No entry on data older than 20 min (crypto) / 30 min (equities) |
| Order types | Market/limit, long-only v1. No margin, no shorts, no options, no leverage |

## Testable assertions

### A. Loop mechanics
1. A single cycle runs end-to-end via `python3 cli.py run-once` and exits 0.
2. The loop crash-resumes: delete nothing, kill mid-cycle, next run recovers
   from the 3 state files alone.
3. Every cycle appends exactly one `cycle` entry to `log.md`.
4. `state.json` is always valid JSON after any cycle (atomic writes).
5. Equities entries are only proposed during regular US market hours;
   crypto proposals are allowed 24/7.
6. A halted loop performs gather/verify but proposes and executes nothing.
7. `python3 cli.py status` renders state without touching the network.

### B. Signals (Generator, systematic)
8. Momentum signal: long only when fast MA > slow MA and lookback return > 0.
9. Mean-reversion signal: entry only when z-score < -2.0; exit when z ≥ 0.
10. Volatility filter: no new entries when realized vol > 2× trailing median.
11. Signals compute from public keyless data (Coinbase candles, Stooq CSV)
    when Alpaca keys are absent — research mode works with zero setup.
12. Each strategy must pass the backtest gate (assertion 20) before its
    proposals are eligible for execution; ineligible strategies log only.

### C. LLM analyst gate (Generator, narrative)
13. Every execution-eligible ENTRY passes through the analyst (`claude -p`)
    and gets a JSON verdict: proceed|veto, conviction ∈ [0,1], thesis.
    EXITS are risk-reducing and never wait on the analyst — a long-only fund
    must always be able to get flat.
14. Analyst failure (timeout, bad JSON) → entry is dropped, never executed.
15. Final size = base size × conviction, then re-checked against caps.
16. The analyst verdict and thesis are persisted in the log entry.

### D. Risk & execution
17. No order is submitted that would breach any cap in the table above.
18. In live mode, an order without a matching approved-and-unexpired approval
    id is refused at the broker layer (belt and suspenders: checked in
    approvals AND broker).
19. Daily loss beyond the kill switch sets `halt: true` and sends a
    notification; the loop stays halted across restarts until manual resume.

### E. Backtest gate
20. `python3 cli.py backtest` runs on ≥ 1 year of daily bars per symbol and
    reports total return, max drawdown, Sharpe, and buy-and-hold benchmark.
21. A strategy is execution-eligible only if backtest Sharpe > 0.5 AND it
    outperforms buy-and-hold OR has max drawdown < half of buy-and-hold's.
22. Backtest results are written into `state.json` under `strategy_gates`.

### F. Evaluator (rule 6 rubric)
23. Evaluator runs after every cycle and scores it in [0,1] on four axes:
    risk discipline (0.4), data integrity (0.2), thesis quality (0.2),
    process hygiene (0.2). Score < 0.6 → warning; CRITICAL breach → halt.
24. Evaluator is a separate module with a separate prompt; the Generator
    never sets its own score.
25. All unit tests pass: `python3 -m unittest discover tests`.

### G. Ops
26. LaunchAgent plist provided but NOT auto-loaded; install is a deliberate
    `ops/install-launchd.sh` step.
27. Secrets only from `~/.config/finsider-stack/stack.env`; never printed,
    never committed. `.gitignore` covers `state/` and any `.env`.

### H. Product: onboarding, accounts, themes (v1.1)
28. First run of any operating command without a saved config launches the
    interactive onboarding wizard; the wizard also runs via `airbank init`.
29. Onboarding is a step-through terminal UX (arrow keys on a TTY, numbered
    fallback when piped) and persists every choice to `~/.airbank/config.json`;
    secrets go only to `~/.airbank/config.env` (0600).
30. Account types: `mock` (simulated portfolio, user-chosen starting cash),
    `alpaca_paper`, `alpaca_live`, `wallet` (watch-only crypto address).
31. The mock engine fills orders at real market prices, tracks cash /
    positions / realized P&L inside `state.json` (still 3 state files), and
    obeys the same risk layer as real accounts. Mock caps scale with starting
    cash: 2% per position, 10% gross (floors: $100 / $500).
32. A watch-only wallet account can never submit an order — `can_execute`
    is false at the broker layer, independent of upstream checks.
33. `alpaca_live` remains triple-locked: chosen in onboarding, `live_ack`
    in state, per-trade approval.
34. Themes are user-selectable in onboarding (and via config.json), applied
    across every command; a no-color theme exists and `NO_COLOR` is honored.
35. `status` and `watch` render portfolio (cash, equity, positions, day and
    total P&L) from state alone — no network. The loop refreshes the
    portfolio view and an equity-history ring buffer (≤96 points) each cycle.

### I. The terminal & the analyst desk (v2)
36. Bare `airbank` on a TTY opens the full-screen live terminal (after
    onboarding); piped/non-TTY callers get help instead of a hung screen.
37. The terminal can trigger cycles, backtests, and analyst deployments, but
    live-money approvals remain explicit CLI commands — never a single
    accidental keypress.
38. Quotes stream on a background thread; the terminal's key handling and
    redraw never block on the network. Only the main thread draws.
39. Analyst desk: a pre-built roster (premarket, macro, crypto, equity, risk,
    journal) deployable via `airbank deploy <name>` or the terminal. Every
    deployment gathers fresh fund context, files a timestamped markdown
    report in ~/.airbank/research/, and records status in state.
40. Analyst reports are advisory only — the desk has no code path to an
    order. Only the loop trades.
41. Onboarding: the cash prompt carries a `$` at the input point, and theme
    selection live-previews the highlighted theme before it is chosen.

### J. Mirror accounts & views (v2.3)
42. Account type `mirror` replicates an external portfolio — a public crypto
    wallet, the connected Alpaca account, or any holdings in
    ~/.airbank/mirror.json — into the fund's own book at real prices,
    scaled to a user-chosen bankroll.
43. Mirror rebalances are mechanical: they bypass the analyst gate but stay
    long-only and unlevered (cash is the constraint), skip drifts under 1%
    of equity, and log every trade to the tape. The signals engine never
    trades on a mirrored book.
44. The kill switch, marks, P&L, analysts, and chat treat a mirror book
    exactly like any other.
45. Three views: dashboard, hybrid (the desk chat replaces only the TAPE
    panel, the grid stays), and full chat. A message sent from the
    dashboard lands in hybrid; esc always steps back toward the dashboard.
46. While the desk thinks, a breathing indicator (glyph + verb + elapsed
    seconds) animates in the chat and the status line.

## Rubric calibration (rule 6)

Good cycle: fresh data, few high-conviction proposals with specific falsifiable
theses, all caps honored, clean log entry. Slop cycle: many low-conviction
trades, vague theses ("looks bullish"), stale data tolerated, caps checked
only in one layer, log noise.

## Restart policy (rule 5)

On repeated cycle failures (3 consecutive), the loop halts and notifies rather
than patch-piling. Humans are inserted only when this contract is wrong.
