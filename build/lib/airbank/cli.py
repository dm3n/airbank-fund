"""Airbank by Finsider — CLI entry point (`airbank`)."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .state import LOG_FILE, load_state, log, save_state

__version__ = "1.0.0"

# ------------------------------------------------------------------ styling

TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if TTY else str(text)


def bold(t):     return _c("1", t)
def dim(t):      return _c("2", t)
def green(t):    return _c("32", t)
def red(t):      return _c("31", t)
def yellow(t):   return _c("33", t)
def cyan(t):     return _c("36", t)
def magenta(t):  return _c("35", t)


BANNER = r"""
    _   ___ ___ ___   _   _  _ _  __
   /_\ |_ _| _ \ _ ) /_\ | \| | |/ /
  / _ \ | ||   / _ \/ _ \| .` | ' <
 /_/ \_\___|_|_\___/_/ \_\_|\_|_|\_\
"""


def banner():
    print(cyan(BANNER))
    print(bold("  Airbank by Finsider") + dim(f"  ·  AI-native hedge fund  ·  v{__version__}"))
    print()


# ------------------------------------------------------------------ helpers

def mode_line():
    broker = green("connected") if config.HAS_BROKER else yellow("no keys — research mode")
    mode = red(bold("LIVE")) if config.LIVE else green("paper")
    return f"mode {mode}  ·  broker {broker}"


def pnl_str(pct):
    text = f"{pct:+.2f}%"
    return green(text) if pct >= 0 else red(text)


def confirm(prompt):
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


# ----------------------------------------------------------------- commands

def cmd_init(*_):
    """Interactive setup: ~/.airbank, keys, first backtest."""
    banner()
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    contract_dst = config.HOME_DIR / "contract.md"
    if not contract_dst.exists():
        shutil.copy(config.PKG_DIR / "contract.md", contract_dst)
    print(f"home       {config.HOME_DIR}")
    print(f"contract   {contract_dst}  {dim('(the graded spec — read it)')}")

    if config.CONFIG_ENV.exists():
        print(f"config     {config.CONFIG_ENV} {dim('(exists, keeping)')}")
    else:
        print()
        print(bold("Broker keys") + dim(" — leave blank to run keyless research mode"))
        key = input("  Alpaca API key    : ").strip()
        secret = input("  Alpaca API secret : ").strip() if key else ""
        slack = input("  Slack webhook URL (optional): ").strip()
        lines = ["# Airbank by Finsider — config", "AIRBANK_MODE=paper"]
        if key:
            lines += [f"ALPACA_API_KEY={key}", f"ALPACA_API_SECRET={secret}"]
        if slack:
            lines.append(f"AIRBANK_SLACK_WEBHOOK={slack}")
        config.CONFIG_ENV.write_text("\n".join(lines) + "\n")
        config.CONFIG_ENV.chmod(0o600)
        print(f"config     {config.CONFIG_ENV} {dim('(chmod 600)')}")

    print()
    if confirm("Run the backtest gate now (~1 min, decides which strategies may trade)?"):
        cmd_backtest()
    print()
    print(bold("Next:"))
    print(f"  {cyan('airbank start')}   run the loop 24/7 (launchd, every 15 min)")
    print(f"  {cyan('airbank watch')}   live dashboard")
    print(f"  {cyan('airbank doctor')}  health check")


def cmd_status(*_):
    banner()
    state = load_state()
    print(mode_line())
    if state["halt"]:
        print(red(bold(f"HALTED: {state.get('halt_reason', '')}")) + dim("  (airbank resume)"))
    print(f"day {state['day'] or dim('—')}  ·  trades today {state['trades_today']}"
          f"  ·  last cycle {state['last_cycle_utc'][:16] or dim('never')}")
    print()
    print(bold("Strategy gates") + dim("  (backtest-earned right to trade)"))
    if not state["strategy_gates"]:
        print(dim("  none — run `airbank backtest`"))
    for name, gate in state["strategy_gates"].items():
        badge = green("ELIGIBLE  ") if gate["eligible"] else dim("ineligible")
        print(f"  {name:10s} {badge}  sharpe {gate['sharpe']:.2f}  "
              f"return {gate['total_return']:+.1%}  maxDD {gate['max_drawdown']:.1%}")
    pending = [a for a in state["pending_approvals"] if a["status"] == "pending"]
    print()
    print(bold(f"Pending approvals ({len(pending)})"))
    for a in pending:
        o = a["order"]
        print(f"  {yellow('[' + a['id'] + ']')} {o['side']} ${o['notional_usd']:.0f} "
              f"{o['symbol']}  conviction {a['conviction']:.1f}")
        print(f"      {dim(a['thesis'])}")
        print(f"      approve: {cyan('airbank approve ' + a['id'])}")


def cmd_run(*_):
    from .loop import run_cycle
    print(dim("cycle: gather -> reason -> act -> verify"))
    score, cycle = run_cycle()
    color = green if score >= 0.8 else (yellow if score >= 0.6 else red)
    print(f"score {color(bold(f'{score:.2f}'))}  ·  "
          f"{len(cycle['candidates'])} candidates, {len(cycle['gated'])} gated, "
          f"{len(cycle['executed'])} executed, {len(cycle['refused'])} refused")
    for err in cycle["data_errors"]:
        print(yellow(f"  data: {err}"))


def cmd_backtest(days="365", *_):
    from . import backtest
    print(dim(f"backtesting {days}d across {len(config.CRYPTO_UNIVERSE)} crypto + "
              f"{len(config.EQUITY_UNIVERSE)} equities..."))
    results = backtest.run(int(days))
    for strategy, r in results.items():
        p, b = r["portfolio"], r["benchmark"]
        badge = green(bold("ELIGIBLE")) if r["eligible"] else dim("ineligible")
        print(f"  {strategy:10s} return {p['total_return']:+7.1%}  "
              f"sharpe {p['sharpe']:5.2f}  maxDD {p['max_drawdown']:6.1%}  "
              f"bench {b['total_return']:+7.1%}  ->  {badge}")


def cmd_watch(*_):
    """Live dashboard: redraws from state + log every few seconds."""
    try:
        while True:
            os.system("clear" if TTY else "true")
            banner()
            state = load_state()
            print(mode_line())
            if state["halt"]:
                print(red(bold(f"⛔ HALTED: {state.get('halt_reason', '')}")))
            print(f"last cycle {state['last_cycle_utc'][:19] or dim('never')}  ·  "
                  f"trades today {state['trades_today']}")
            print()
            gates = ", ".join(
                (green(k) if v["eligible"] else dim(k))
                for k, v in state["strategy_gates"].items()) or dim("no gates")
            print(bold("gates  ") + gates)
            pending = [a for a in state["pending_approvals"] if a["status"] == "pending"]
            if pending:
                print(bold(yellow(f"⏳ {len(pending)} trade(s) awaiting your approval")))
                for a in pending:
                    o = a["order"]
                    print(f"   [{a['id']}] {o['side']} ${o['notional_usd']:.0f} {o['symbol']}")
            print()
            print(bold("trace ") + dim(str(LOG_FILE)))
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text().strip().splitlines()[-14:]
                for line in lines:
                    if line.startswith("## "):
                        line = cyan(line[3:])
                    print("  " + line)
            print()
            print(dim("refreshing every 5s — ctrl-c to exit"))
            time.sleep(5)
    except KeyboardInterrupt:
        print()


def cmd_decide(decision, approval_id=None, *_):
    if not approval_id:
        print("usage: airbank approve|reject <id>")
        sys.exit(1)
    from . import approvals
    state = load_state()
    approval = approvals.resolve(state, approval_id, decision)
    save_state(state)
    if approval is None:
        print(red(f"no pending approval {approval_id}"))
        sys.exit(1)
    order = approval["order"]
    if decision == "approved":
        print(green(f"approved {order['side']} ${order['notional_usd']:.0f} "
                    f"{order['symbol']} — executes next cycle"))
    else:
        print(f"rejected [{approval_id}]")


def cmd_halt(*reason):
    state = load_state()
    state["halt"], state["halt_reason"] = True, " ".join(reason) or "manual halt"
    save_state(state)
    log("halt", state["halt_reason"])
    print(red("halted"))


def cmd_resume(*_):
    state = load_state()
    state["halt"], state["halt_reason"] = False, ""
    state["consecutive_failures"] = 0
    save_state(state)
    log("resume", "manual resume")
    print(green("resumed"))


# ------------------------------------------------------------------- daemon

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.airbank.loop.plist"


def cmd_start(interval="900", *_):
    binary = shutil.which("airbank") or sys.argv[0]
    plist = {
        "Label": "com.airbank.loop",
        "ProgramArguments": [str(binary), "run"],
        "StartInterval": int(interval),
        "StandardOutPath": str(config.HOME_DIR / "loop-stdout.log"),
        "StandardErrorPath": str(config.HOME_DIR / "loop-stderr.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)
    print(green(f"loop running 24/7 — one cycle every {int(interval) // 60} min"))
    print(dim(f"watch it: airbank watch   ·   stop it: airbank stop"))


def cmd_stop(*_):
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print("loop stopped")


def cmd_doctor(*_):
    banner()
    checks = []
    checks.append(("python >= 3.9", sys.version_info >= (3, 9), sys.version.split()[0]))
    claude_bin = shutil.which("claude")
    checks.append(("claude CLI (LLM analyst)", bool(claude_bin), claude_bin or "not found"))
    for name, url in [("crypto data (Coinbase)",
                       "https://api.exchange.coinbase.com/products/BTC-USD/ticker"),
                      ("equity data (Yahoo)",
                       "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1d&interval=1d")]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 airbank"})
            urllib.request.urlopen(req, timeout=10)
            checks.append((name, True, "reachable"))
        except Exception as exc:
            checks.append((name, False, str(exc)[:60]))
    checks.append(("broker keys", config.HAS_BROKER,
                   "configured" if config.HAS_BROKER else "absent (research mode ok)"))
    checks.append(("state home", config.STATE_DIR.exists(), str(config.STATE_DIR)))
    checks.append(("24/7 loop installed", PLIST_PATH.exists(),
                   str(PLIST_PATH) if PLIST_PATH.exists() else "run `airbank start`"))
    state = load_state()
    checks.append(("not halted", not state["halt"], state.get("halt_reason") or "ok"))
    ok_all = True
    for name, ok, detail in checks:
        mark = green("✓") if ok else red("✗")
        ok_all &= ok or name in ("broker keys", "24/7 loop installed")
        print(f"  {mark} {name:26s} {dim(detail)}")
    print()
    print(green("all systems go") if ok_all else yellow("issues above need attention"))


def cmd_contract(*_):
    print((config.PKG_DIR / "contract.md").read_text())


def cmd_version(*_):
    print(f"airbank {__version__}")


HELP = f"""{cyan(BANNER)}
{bold('  Airbank by Finsider')} — the AI-native hedge fund in your terminal

  {bold('setup')}
    airbank init              interactive setup (keys optional — keyless = research mode)
    airbank doctor            health check
    airbank contract          print the loop contract (the graded spec)

  {bold('alpha')}
    airbank backtest [days]   gate strategies on historical data
    airbank run               one full cycle: gather -> reason -> act -> verify
    airbank start [seconds]   run 24/7 via launchd (default: every 900s)
    airbank stop              stop the 24/7 loop

  {bold('operate')}
    airbank watch             live dashboard
    airbank status            fund state, gates, pending approvals
    airbank approve <id>      approve a pending live trade
    airbank reject <id>       reject a pending live trade
    airbank halt [reason]     kill switch
    airbank resume            clear halt after review

  {dim('Live money needs three locks: AIRBANK_MODE=live + live_ack in state + per-trade approval.')}
"""

COMMANDS = {
    "init": cmd_init, "status": cmd_status, "run": cmd_run, "run-once": cmd_run,
    "backtest": cmd_backtest, "watch": cmd_watch, "halt": cmd_halt,
    "resume": cmd_resume, "start": cmd_start, "stop": cmd_stop,
    "doctor": cmd_doctor, "contract": cmd_contract,
    "version": cmd_version, "--version": cmd_version,
    "approve": lambda *a: cmd_decide("approved", *a),
    "reject": lambda *a: cmd_decide("rejected", *a),
}


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("help", "-h", "--help") or args[0] not in COMMANDS:
        print(HELP)
        sys.exit(0 if args and args[0] in ("help", "-h", "--help") else (0 if not args else 1))
    COMMANDS[args[0]](*args[1:])


if __name__ == "__main__":
    main()
