<h1 align="center">Airbank by Finsider</h1>

<p align="center"><b>The AI-native hedge fund that lives in your terminal.</b></p>

A 24/7 agent loop
(gather → reason → act → verify → repeat) that researches crypto and US
equities, generates hybrid alpha (systematic signals gated by an LLM analyst),
and executes through Alpaca with hard risk caps and per-trade human approval
on live money.

Built on the LOOPS architecture: three roles (Planner / Generator / Evaluator),
exactly three state files, restart over patch-piling, subjective quality scored
by a written rubric. The binding spec is [`airbank/contract.md`](airbank/contract.md)
— run `airbank contract` to read it.

## Install

```bash
brew install dm3n/tap/airbank
```

or, with pipx:

```bash
pipx install git+https://github.com/dm3n/airbank-fund
```

## Sixty seconds to a running fund

Type `airbank`. The first run opens the onboarding wizard — pick an account,
pick a style (themes live-preview as you scroll) — and every run after that
drops you straight into **the terminal**: a full-screen, Bloomberg-style live
fund view with streaming quotes, your portfolio and P&L, strategy gates, the
analyst desk, and the loop's thinking on the tape.

```bash
airbank           # the terminal — [r]un cycle · [d]eploy analyst · [b]acktest · [t]heme · [q]uit
airbank backtest  # strategies must earn a Sharpe > 0.5 to trade
airbank start     # the loop runs 24/7, one cycle every 15 min
```

## Talk to your fund

The chat bar at the bottom of the terminal is the main way you drive
Airbank — it works exactly like Claude Code. Type anything; the screen flips
to the desk conversation, the answer streams in grounded in your live book,
and `esc` flips you straight back to the dashboard.

The desk can act, not just talk: ask it to run a cycle, backtest, or deploy
an analyst and it does it (through the same risk and approval layers —
live-money approvals still require the explicit `airbank approve <id>`).
Slash commands work too: `/run` `/deploy <name>` `/backtest` `/theme` `/quit`.

## The analyst desk

Deploy a research agent whenever you want a second brain on the book. Each
one gathers the fund's live context (prices, positions, gates, trade log),
briefs Claude, and files a timestamped markdown report to
`~/.airbank/research/`:

```bash
airbank analysts          # the roster
airbank deploy premarket  # morning briefing: overnight moves, today's setup
airbank deploy risk       # adversarial review of the current book
airbank research          # read the latest report
```

Roster: `premarket` · `macro` · `crypto` · `equity` · `risk` · `journal`.
Reports are advisory only — the desk has no code path to an order.

Four account types:

| Account | What it is |
|---|---|
| **Mock portfolio** | simulated cash (you choose how much), fills at real market prices — zero setup, trades out of the box |
| **Alpaca paper** | Alpaca's free paper-trading account |
| **Alpaca live** | real money — triple-locked, every trade needs your approval |
| **Watch-only wallet** | track a public BTC/ETH address; the fund researches but never trades |

Four themes (`airbank theme` to switch anytime): midnight, terminal, matrix,
mono. `NO_COLOR` is honored.

## The loop

Every cycle:

1. **Gather** — prices and news for BTC/ETH/SOL + 7 US megacaps (equities only
   during market hours; crypto around the clock).
2. **Reason** — momentum and mean-reversion signals behind a volatility filter;
   only backtest-eligible strategies may propose trades. Every entry is then
   judged by an LLM analyst (`claude` CLI) that returns proceed/veto, a
   conviction score, and a falsifiable thesis. Analyst failure drops the entry.
   Exits are risk-reducing and never wait on the analyst.
3. **Act** — risk caps checked, then execution: mock and paper accounts
   auto-execute, live mode creates a pending approval and notifies you.
4. **Verify** — a separate Evaluator grades the cycle in [0,1] against the
   contract rubric and halts the fund on any critical breach.

## Live money — three deliberate locks

1. `AIRBANK_MODE=live` in `~/.airbank/config.env` with **live** Alpaca keys.
2. `"live_ack": true` set manually in `~/.airbank/state/state.json`.
3. Per-trade approval: `airbank approve <id>` (expires after 4 hours).

Live caps: $200/position, $1,000 gross, 10 trades/day, -3% daily kill switch,
long-only, no margin. Changing them means editing the contract AND the config
together — they are the same promise in two places.

## Operating it

```bash
airbank status            # gates, approvals, last cycle
airbank doctor            # health check
airbank halt "reason"     # kill switch
airbank resume            # after review
tail -f ~/.airbank/state/log.md   # the raw trace — read this to debug
```

State lives in `~/.airbank/` — three files (`contract.md`, `state/state.json`,
`state/log.md`), designed for crash-resume.

## Development

```bash
python3 -m unittest discover tests
```
