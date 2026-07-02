#!/usr/bin/env python3
"""Airbank by Finsider — CLI.

  python3 cli.py run-once            one loop cycle (gather->reason->act->verify)
  python3 cli.py status              render state, no network
  python3 cli.py backtest [days]     run backtest gate, write strategy_gates
  python3 cli.py approve <id>        approve a pending live trade
  python3 cli.py reject <id>         reject a pending live trade
  python3 cli.py halt "<reason>"     manual halt
  python3 cli.py resume              clear halt + reset failure counter
"""
import json
import sys

from airbank import config
from airbank.state import load_state, log, save_state


def cmd_status():
    state = load_state()
    print(f"Airbank by Finsider — mode={config.MODE} broker={'yes' if config.HAS_BROKER else 'NO KEYS (research mode)'}")
    print(f"halt={state['halt']} {state.get('halt_reason', '')}")
    print(f"day={state['day']} trades_today={state['trades_today']} "
          f"day_start_equity={state['day_start_equity']}")
    print(f"strategy_gates={json.dumps(state['strategy_gates'], indent=2)}")
    pending = [a for a in state["pending_approvals"] if a["status"] == "pending"]
    print(f"pending approvals: {len(pending)}")
    for a in pending:
        o = a["order"]
        print(f"  [{a['id']}] {o['side']} ${o['notional_usd']:.0f} {o['symbol']} — {a['thesis']}")
    print(f"last_cycle={state['last_cycle_utc']}")


def cmd_run_once():
    from airbank.loop import run_cycle
    score, cycle = run_cycle()
    print(f"cycle complete — score {score}")
    for key in ("candidates", "gated", "executed", "refused"):
        print(f"  {key}: {len(cycle[key])}")
    for err in cycle["data_errors"]:
        print(f"  data error: {err}")
    sys.exit(0)


def cmd_backtest(days=365):
    from airbank import backtest
    results = backtest.run(int(days))
    for strategy, r in results.items():
        p, b = r["portfolio"], r["benchmark"]
        print(f"{strategy:9s} return {p['total_return']:+.1%}  sharpe {p['sharpe']:.2f}  "
              f"maxDD {p['max_drawdown']:.1%}  (bench {b['total_return']:+.1%})  "
              f"-> {'ELIGIBLE' if r['eligible'] else 'ineligible'}")


def cmd_decide(approval_id, decision):
    from airbank import approvals
    state = load_state()
    approval = approvals.resolve(state, approval_id, decision)
    save_state(state)
    if approval is None:
        print(f"no pending approval {approval_id}")
        sys.exit(1)
    print(f"{decision}: {approval['order']['symbol']} "
          f"${approval['order']['notional_usd']:.0f} — executes next cycle"
          if decision == "approved" else f"rejected {approval_id}")


def cmd_halt(reason):
    state = load_state()
    state["halt"], state["halt_reason"] = True, reason
    save_state(state)
    log("halt", reason)
    print("halted")


def cmd_resume():
    state = load_state()
    state["halt"], state["halt_reason"] = False, ""
    state["consecutive_failures"] = 0
    save_state(state)
    log("resume", "manual resume")
    print("resumed")


COMMANDS = {
    "status": cmd_status,
    "run-once": cmd_run_once,
    "backtest": cmd_backtest,
    "halt": cmd_halt,
    "resume": cmd_resume,
    "approve": lambda approval_id: cmd_decide(approval_id, "approved"),
    "reject": lambda approval_id: cmd_decide(approval_id, "rejected"),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](*sys.argv[2:])
