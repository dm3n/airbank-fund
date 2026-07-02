"""Airbank by Finsider — CLI entry point (`airbank`)."""
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import config, ui
from .state import LOG_FILE, load_state, log, save_state
from .ui import accent, accent2, bad, bold, dim, good, money, warn

__version__ = "2.3.0"

ui.set_theme(config.THEME)


def banner():
    print(accent(ui.BANNER))
    print(bold("  Airbank by Finsider") + dim(f"  ·  AI-native hedge fund  ·  v{__version__}"))
    print()


ACCOUNT_LABEL = {
    "mock": "mock portfolio",
    "alpaca_paper": "Alpaca paper",
    "alpaca_live": "Alpaca LIVE",
    "wallet": "watch-only wallet",
    "mirror": "mirror account",
}


def mode_line():
    label = ACCOUNT_LABEL.get(config.ACCOUNT_TYPE, config.ACCOUNT_TYPE)
    account = bad(bold(label)) if config.LIVE else accent(label)
    extra = ""
    if config.ACCOUNT_TYPE in ("alpaca_paper", "alpaca_live"):
        extra = "  ·  " + (good("broker connected") if config.HAS_BROKER
                           else warn("no keys — research mode"))
    return f"account {account}{extra}  ·  theme {dim(config.THEME)}"


def portfolio_panel(state):
    view = state.get("portfolio_view")
    if not view:
        print(dim("  no portfolio yet — run `airbank run` or `airbank start`"))
        return
    equity = view.get("equity")
    print(bold("Portfolio") + dim(f"  ({view['account']})"))
    if equity is None:
        print(dim("  equity unknown (broker unreachable or no data yet)"))
        return
    total = view.get("total_pnl_pct", 0.0)
    line = f"  equity {bold(money(equity))}  ·  cash {money(view.get('cash', 0))}"
    line += f"  ·  total P&L {ui.pnl(total, pct=True)}"
    if "realized_pnl" in view:
        line += f"  ·  realized {ui.pnl(view['realized_pnl'])}"
    print(line)
    history = state.get("equity_history", [])
    print("  " + ui.sparkline(history) + dim("  24h equity"))
    positions = view.get("positions", [])
    if positions:
        for p in positions:
            print(f"    {accent2(p['symbol']):<24s} {money(p['value']):>14s}  "
                  f"{ui.pnl(p['pnl_pct'], pct=True)}")
    else:
        print(dim("    no open positions"))


# ----------------------------------------------------------------- commands

def cmd_init(*_):
    from . import onboard
    if onboard.run() and sys.stdin.isatty():
        os.execvp(sys.argv[0], [sys.argv[0]])  # seamless: straight into the terminal


def cmd_status(*_):
    banner()
    state = load_state()
    print(mode_line())
    if state["halt"]:
        print(bad(bold(f"HALTED: {state.get('halt_reason', '')}")) + dim("  (airbank resume)"))
    print(f"day {state['day'] or dim('—')}  ·  trades today {state['trades_today']}"
          f"  ·  last cycle {state['last_cycle_utc'][:16] or dim('never')}")
    print()
    portfolio_panel(state)
    print()
    print(bold("Strategy gates") + dim("  (backtest-earned right to trade)"))
    if not state["strategy_gates"]:
        print(dim("  none — run `airbank backtest`"))
    for name, gate in state["strategy_gates"].items():
        badge = good("ELIGIBLE  ") if gate["eligible"] else dim("ineligible")
        print(f"  {name:10s} {badge}  sharpe {gate['sharpe']:.2f}  "
              f"return {gate['total_return']:+.1%}  maxDD {gate['max_drawdown']:.1%}")
    pending = [a for a in state["pending_approvals"] if a["status"] == "pending"]
    print()
    print(bold(f"Pending approvals ({len(pending)})"))
    for a in pending:
        o = a["order"]
        print(f"  {warn('[' + a['id'] + ']')} {o['side']} ${o['notional_usd']:.0f} "
              f"{o['symbol']}  conviction {a['conviction']:.1f}")
        print(f"      {dim(a['thesis'])}")
        print(f"      approve: {accent('airbank approve ' + a['id'])}")


def cmd_run(*_):
    from .loop import run_cycle
    print(dim("cycle: gather -> reason -> act -> verify"))
    score, cycle = run_cycle()
    color = good if score >= 0.8 else (warn if score >= 0.6 else bad)
    print(f"score {color(bold(f'{score:.2f}'))}  ·  "
          f"{len(cycle['candidates'])} candidates, {len(cycle['gated'])} gated, "
          f"{len(cycle['executed'])} executed, {len(cycle['refused'])} refused")
    for err in cycle["data_errors"]:
        print(warn(f"  data: {err}"))


def cmd_backtest(days="365", *_):
    from . import backtest
    print(dim(f"backtesting {days}d across {len(config.CRYPTO_UNIVERSE)} crypto + "
              f"{len(config.EQUITY_UNIVERSE)} equities..."))
    results = backtest.run(int(days))
    for strategy, r in results.items():
        p, b = r["portfolio"], r["benchmark"]
        badge = good(bold("ELIGIBLE")) if r["eligible"] else dim("ineligible")
        print(f"  {strategy:10s} return {p['total_return']:+7.1%}  "
              f"sharpe {p['sharpe']:5.2f}  maxDD {p['max_drawdown']:6.1%}  "
              f"bench {b['total_return']:+7.1%}  ->  {badge}")


def cmd_watch(*_):
    try:
        while True:
            ui.clear()
            banner()
            state = load_state()
            print(mode_line())
            if state["halt"]:
                print(bad(bold(f"⛔ HALTED: {state.get('halt_reason', '')}")))
            print(f"last cycle {state['last_cycle_utc'][:19] or dim('never')}  ·  "
                  f"trades today {state['trades_today']}")
            print()
            portfolio_panel(state)
            print()
            gates = ", ".join(
                (good(k) if v["eligible"] else dim(k))
                for k, v in state["strategy_gates"].items()) or dim("no gates")
            print(bold("gates  ") + gates)
            pending = [a for a in state["pending_approvals"] if a["status"] == "pending"]
            if pending:
                print(bold(warn(f"⏳ {len(pending)} trade(s) awaiting your approval")))
                for a in pending:
                    o = a["order"]
                    print(f"   [{a['id']}] {o['side']} ${o['notional_usd']:.0f} {o['symbol']}")
            print()
            print(bold("trace ") + dim(str(LOG_FILE)))
            if LOG_FILE.exists():
                for line in LOG_FILE.read_text().strip().splitlines()[-12:]:
                    if line.startswith("## "):
                        line = accent(line[3:])
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
        print(bad(f"no pending approval {approval_id}"))
        sys.exit(1)
    order = approval["order"]
    if decision == "approved":
        print(good(f"approved {order['side']} ${order['notional_usd']:.0f} "
                   f"{order['symbol']} — executes next cycle"))
    else:
        print(f"rejected [{approval_id}]")


def cmd_halt(*reason):
    state = load_state()
    state["halt"], state["halt_reason"] = True, " ".join(reason) or "manual halt"
    save_state(state)
    log("halt", state["halt_reason"])
    print(bad("halted"))


def cmd_resume(*_):
    state = load_state()
    state["halt"], state["halt_reason"] = False, ""
    state["consecutive_failures"] = 0
    save_state(state)
    log("resume", "manual resume")
    print(good("resumed"))


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
    print(good(f"loop running 24/7 — one cycle every {int(interval) // 60} min"))
    print(dim("watch it: airbank watch   ·   stop it: airbank stop"))


def cmd_stop(*_):
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print("loop stopped")


def cmd_doctor(*_):
    banner()
    checks = []
    checks.append(("python >= 3.9", sys.version_info >= (3, 9), sys.version.split()[0]))
    checks.append(("onboarded", config.ONBOARDED,
                   ACCOUNT_LABEL.get(config.ACCOUNT_TYPE, "?") if config.ONBOARDED
                   else "run `airbank init`"))
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
    if config.ACCOUNT_TYPE in ("alpaca_paper", "alpaca_live"):
        checks.append(("broker keys", config.HAS_BROKER,
                       "configured" if config.HAS_BROKER else "missing — re-run `airbank init`"))
    checks.append(("state home", config.STATE_DIR.exists(), str(config.STATE_DIR)))
    checks.append(("24/7 loop installed", PLIST_PATH.exists(),
                   str(PLIST_PATH) if PLIST_PATH.exists() else "run `airbank start`"))
    state = load_state()
    checks.append(("not halted", not state["halt"], state.get("halt_reason") or "ok"))
    soft = ("24/7 loop installed", "onboarded")
    ok_all = True
    for name, ok, detail in checks:
        mark = good("✓") if ok else bad("✗")
        ok_all &= ok or name in soft
        print(f"  {mark} {name:26s} {dim(detail)}")
    print()
    print(good("all systems go") if ok_all else warn("issues above need attention"))


def cmd_theme(name=None, *_):
    if name not in ui.THEMES:
        print(bold("themes:"))
        for key, t in ui.THEMES.items():
            marker = accent("▸ ") if key == config.THEME else "  "
            print(f"  {marker}{key:10s} {dim(t['label'])}")
        print(dim("\n  set one: airbank theme <name>"))
        return
    import json
    product = config.load_product_config()
    product["theme"] = name
    config.CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_JSON.write_text(json.dumps(product, indent=2) + "\n")
    ui.set_theme(name)
    print(good(f"theme set: {name}") + "  " + accent("▮▮") + accent2(" ▮▮"))


def cmd_dash(*_):
    from . import dashboard
    dashboard.run()


def cmd_analysts(*_):
    from . import analysts
    banner()
    print(bold("The analyst desk") + dim("  — deploy one: airbank deploy <name>"))
    print()
    state = load_state()
    desk = state.get("analyst_desk", {})
    for name, spec in analysts.ROSTER.items():
        last = desk.get(name)
        stamp = dim(f"last: {last['last_run_utc'][:16].replace('T', ' ')}") if last \
            else dim("never deployed")
        print(f"  {accent(name):<18s} {bold(spec['title']):<32s} {stamp}")
        print(f"           {dim(spec['desc'])}")
        if last:
            print(f"           {dim('→ ' + last['headline'])}")
        print()


def cmd_deploy(name=None, *_):
    from . import analysts
    if name not in analysts.ROSTER:
        print(f"usage: airbank deploy <{'|'.join(analysts.ROSTER)}>")
        sys.exit(1)
    spec = analysts.ROSTER[name]
    print(dim(f"deploying {spec['title']} — gathering fund data, briefing the desk …"))
    path, headline = analysts.deploy(name)
    print(good(f"report filed: {headline}"))
    print(dim(f"  {path}"))
    print(dim("  read it: airbank research"))


def cmd_schedule(name=None, when="08:45", *_):
    """Deploy an analyst on a daily launchd schedule (Mac local time)."""
    from . import analysts
    if name not in analysts.ROSTER:
        print(f"usage: airbank schedule <{'|'.join(analysts.ROSTER)}> [HH:MM|off]")
        sys.exit(1)
    plist_path = PLIST_PATH.parent / f"com.airbank.analyst.{name}.plist"
    if when in ("off", "stop"):
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        if plist_path.exists():
            plist_path.unlink()
        print(f"{name} schedule removed")
        return
    try:
        hour, minute = (int(x) for x in when.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(when)
    except ValueError:
        print(bad(f"bad time {when!r} — use HH:MM, 24h"))
        sys.exit(1)
    binary = shutil.which("airbank") or sys.argv[0]
    plist = {
        "Label": f"com.airbank.analyst.{name}",
        "ProgramArguments": [str(binary), "deploy", name],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(config.HOME_DIR / f"analyst-{name}-stdout.log"),
        "StandardErrorPath": str(config.HOME_DIR / f"analyst-{name}-stderr.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)
    print(good(f"{analysts.ROSTER[name]['title']} files every day at {when} (Mac local time)"))
    print(dim(f"  read it: airbank research   ·   stop it: airbank schedule {name} off"))


def cmd_research(index="1", *_):
    from . import analysts
    files = analysts.reports()
    if not files:
        print(dim("no research yet — deploy an analyst: airbank deploy premarket"))
        return
    if index == "list":
        for i, f in enumerate(files[:15], 1):
            print(f"  {accent(str(i)):>4s}  {f.name}")
        return
    n = int(index) if str(index).isdigit() else 1
    target = files[min(n, len(files)) - 1]
    print(dim(f"— {target.name} —\n"))
    print(target.read_text())


def cmd_contract(*_):
    print((config.PKG_DIR / "contract.md").read_text())


def cmd_version(*_):
    print(f"airbank {__version__}")


def help_text():
    return f"""{accent(ui.BANNER)}
{bold('  Airbank by Finsider')} — the AI-native hedge fund in your terminal

    {accent(bold('airbank'))}                   open the terminal (live Bloomberg-style fund view)

  {bold('setup')}
    airbank init              onboarding wizard (account, style — mock money welcome)
    airbank theme [name]      list or switch themes
    airbank doctor            health check
    airbank contract          print the loop contract (the graded spec)

  {bold('alpha')}
    airbank backtest [days]   gate strategies on historical data
    airbank run               one full cycle: gather -> reason -> act -> verify
    airbank start [seconds]   run 24/7 via launchd (default: every 900s)
    airbank stop              stop the 24/7 loop

  {bold('analyst desk')}
    airbank analysts          the roster: premarket, macro, crypto, equity, risk, journal
    airbank deploy <name>     deploy an analyst now — files a markdown report
    airbank schedule <name> [HH:MM|off]  file a report every morning (default 08:45)
    airbank research [n|list] read the latest (or nth) research report

  {bold('operate')}
    airbank status            fund state, gates, pending approvals
    airbank watch             simple auto-refreshing status (the terminal is better)
    airbank approve <id>      approve a pending live trade
    airbank reject <id>       reject a pending live trade
    airbank halt [reason]     kill switch
    airbank resume            clear halt after review

  {dim('Live money needs three locks: live account in onboarding + live_ack in state + per-trade approval.')}
"""


COMMANDS = {
    "init": cmd_init, "status": cmd_status, "run": cmd_run, "run-once": cmd_run,
    "backtest": cmd_backtest, "watch": cmd_watch, "halt": cmd_halt,
    "resume": cmd_resume, "start": cmd_start, "stop": cmd_stop,
    "doctor": cmd_doctor, "contract": cmd_contract, "theme": cmd_theme,
    "dash": cmd_dash, "terminal": cmd_dash, "analysts": cmd_analysts,
    "deploy": cmd_deploy, "schedule": cmd_schedule, "research": cmd_research,
    "version": cmd_version, "--version": cmd_version,
    "approve": lambda *a: cmd_decide("approved", *a),
    "reject": lambda *a: cmd_decide("rejected", *a),
}

NO_ONBOARD = {"init", "help", "-h", "--help", "version", "--version",
              "contract", "theme", "stop"}


def main():
    args = sys.argv[1:]
    if not args:
        # bare `airbank` = the terminal (assertion 36); help when piped
        args = ["dash"] if sys.stdin.isatty() else ["help"]
    if args[0] in ("help", "-h", "--help") or args[0] not in COMMANDS:
        print(help_text())
        sys.exit(0 if args[0] in ("help", "-h", "--help") else 1)
    # first run: launch onboarding before any operating command (assertion 28);
    # never block a non-interactive caller (launchd) — fall back to defaults.
    if not config.ONBOARDED and args[0] not in NO_ONBOARD and sys.stdin.isatty():
        from . import onboard
        try:
            if onboard.run():
                # re-exec so every module sees the fresh config
                os.execvp(sys.argv[0], sys.argv)
        except KeyboardInterrupt:
            print(dim("\nsetup skipped — running with defaults (mock portfolio)"))
    try:
        COMMANDS[args[0]](*args[1:])
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
