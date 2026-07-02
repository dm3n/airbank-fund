# Airbank by Finsider

The world's first AI-native hedge fund: a 24/7 agent loop (gather → reason →
act → verify → repeat) that researches crypto and US equities, generates
hybrid alpha (systematic signals gated by an LLM analyst), and executes
through Alpaca with hard risk caps and per-trade human approval on live money.

Built on the LOOPS architecture. The binding spec is [contract.md](contract.md):
three roles (Planner / Generator / Evaluator), exactly three state files,
restart over patch-piling, subjective quality scored by written rubric.

## Quick start (zero setup — research mode)

```bash
python3 -m unittest discover tests   # contract assertion 25
python3 cli.py backtest              # gate strategies on 1y of daily bars
python3 cli.py run-once              # one full cycle
python3 cli.py status
```

With no Alpaca keys the loop still gathers public data (Coinbase candles,
Stooq CSV), computes signals, and logs proposals — it just can't execute.

## Wiring the broker

1. Create an Alpaca account, generate **paper** keys first.
2. Add to `~/.config/finsider-stack/stack.env` (chmod 600):
   ```
   ALPACA_API_KEY=...
   ALPACA_API_SECRET=...
   AIRBANK_SLACK_WEBHOOK=...   # optional
   ```
3. Paper mode is the default and auto-executes gated proposals.

## Going live (deliberate, three locks)

Live orders only execute when ALL of these hold:
1. `AIRBANK_MODE=live` in the environment, with **live** Alpaca keys.
2. `"live_ack": true` set manually in `state/state.json`.
3. A per-trade approval: the loop notifies you, you run
   `python3 cli.py approve <id>` (approvals expire after 4h).

Caps in live mode: $200/position, $1,000 gross, 10 trades/day, -3% daily
kill switch, long-only, no margin. Change them only by editing contract.md
AND config.py together.

## Running 24/7

```bash
./ops/install-launchd.sh   # launchd runs a cycle every 15 min
```

## State (crash-resume from 3 files)

- `contract.md` — the graded spec, never mutated by the loop
- `state/state.json` — positions, approvals, gates, halt flag
- `state/log.md` — append-only trace; read this to debug (loops.md rule 7)

## Operating it

```bash
python3 cli.py halt "reason"   # manual kill
python3 cli.py resume          # clear halt after review
tail -f state/log.md           # watch the loop think
```
