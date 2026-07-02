"""Airbank by Finsider — CLI entry point (`airbank`)."""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, pipeline, ui
from .state import load_state, log, save_state
from .ui import accent, accent2, bad, bold, dim, good, warn

__version__ = "3.0.0"


def banner():
    print(accent(ui.BANNER))
    print(bold("  Airbank by Finsider") + dim(f"  ·  the AI-native investment bank  ·  v{__version__}"))
    print()


# ----------------------------------------------------------------- commands

def cmd_init(*_):
    from . import onboard
    if onboard.run() and sys.stdin.isatty():
        os.execvp(sys.argv[0], [sys.argv[0]])   # seamless: straight into the terminal


def cmd_status(*_):
    banner()
    state = load_state()
    mode = good("simulation") if config.SIMULATION else warn("LIVE")
    print(f"firm {bold(config.MANDATE.get('firm', 'Airbank'))}  ·  mode {mode}"
          f"  ·  last cycle {state['last_cycle_utc'][:16] or dim('never')}")
    if state["halt"]:
        print(bad(bold(f"HALTED: {state.get('halt_reason', '')}")) + dim("  (airbank resume)"))
    print()
    counts, value = pipeline.funnel(state)
    active_value = sum(value[s] for s in pipeline.ACTIVE_STAGES)
    print(bold("Funnel") + dim(f"  ·  active pipeline ${active_value:,.0f} revenue"))
    print("  " + "  ".join(
        (accent2(f"{pipeline.STAGE_LABEL[s]} {counts[s]}") if counts[s]
         else dim(f"{pipeline.STAGE_LABEL[s]} {counts[s]}"))
        for s in pipeline.STAGES))
    print()
    print(bold("Top of the book"))
    deals = pipeline.active_deals(state)[:8]
    if not deals:
        print(dim("  nothing yet — airbank run to source the first leads"))
    for d in deals:
        print(f"  {accent2(d['id']):<18} {d['company'][:28]:<30} "
              f"{pipeline.STAGE_LABEL[d['stage']]:<10} fit {d['score']:>3.0f}  "
              f"${d['revenue']:,.0f}")
    pending = [a for a in state["pending_approvals"] if a["status"] == "pending"]
    print()
    print(bold(f"Pending approvals ({len(pending)})"))
    for a in pending:
        print(f"  {warn('[' + a['id'] + ']')} {a['kind']:<9} {a['company']:<28} {dim(a['summary'])}")
        print(f"      approve: {accent('airbank approve ' + a['id'])}")


def cmd_run(*_):
    from .loop import run_cycle
    print(dim("cycle: source → advance → diligence → verify"))
    score, cycle = run_cycle()
    color = good if score >= 0.8 else (warn if score >= 0.6 else bad)
    print(f"score {color(bold(f'{score:.2f}'))}  ·  {cycle['new_leads']} sourced, "
          f"{cycle['outreach']} outreach, {len(cycle['advanced'])} advanced, "
          f"{cycle['memos']} memos, {cycle['waiting']} waiting")
    for err in cycle["errors"][:3]:
        print(warn(f"  error: {err.splitlines()[-1] if err.splitlines() else err}"))


def cmd_deals(*_):
    state = load_state()
    for d in sorted(pipeline.book(state)["deals"].values(),
                    key=lambda d: d["updated_utc"], reverse=True):
        marker = dim if d["stage"] == "dead" else accent2
        print(f"  {marker(d['id']):<18} {d['company'][:30]:<32} "
              f"{pipeline.STAGE_LABEL[d['stage']]:<10} fit {d['score']:>3.0f}  "
              f"${d['revenue']:,.0f}  {dim('via ' + d['source'])}")


def cmd_deal(needle=None, *_):
    if not needle:
        print("usage: airbank deal <id or company>")
        sys.exit(1)
    state = load_state()
    d = pipeline.find(state, needle)
    if d is None:
        print(bad(f"no deal matching {needle!r}"))
        sys.exit(1)
    banner()
    print(f"{bold(d['company'])}  ·  {d['sector']}  ·  {pipeline.STAGE_LABEL[d['stage']]}")
    print(f"fit {d['score']}  ·  revenue ${d['revenue']:,.0f}  ·  "
          f"ebitda ${d['ebitda']:,.0f}  ·  via {d['source']}")
    print(f"contact  {d['contact'] or dim('unknown')}")
    print(f"folder   {dim(str(pipeline.deal_dir(d['id'])))}")
    for kind, dd in d.get("diligence", {}).items():
        print(f"{kind}: score {dd['score']}  {dim('; '.join(dd['flags']) or 'no flags')}")
    print()
    print(bold("History"))
    for h in d["history"][-12:]:
        print(dim("  " + h))


def cmd_diligence(needle=None, *_):
    if not needle:
        print("usage: airbank diligence <id or company>")
        sys.exit(1)
    from . import diligence
    state = load_state()
    d = pipeline.find(state, needle)
    if d is None:
        print(bad(f"no deal matching {needle!r}"))
        sys.exit(1)
    if config.SIMULATION:
        diligence.generate_demo_financials(d)
    kind = "post_loi" if d["stage"] in ("loi", "post_loi", "closing") else "pre_loi"
    print(dim(f"running the Finsider engine on {d['company']} ({kind}) …"))
    m = diligence.run(state, d, kind)
    save_state(state)
    if m is None:
        print(warn(f"waiting on financials — drop financials.csv into "
                   f"{pipeline.deal_dir(d['id'])}"))
        return
    print(good(f"score {m['score']}") + f"  ·  ttm revenue ${m['ttm_revenue']:,.0f}"
          f"  ·  ttm ebitda ${m['ttm_ebitda']:,.0f}"
          f"  ·  growth {m['growth_h2_vs_h1']:+.1%}")
    for flag in m["flags"]:
        print(warn(f"  ⚑ {flag}"))
    print(dim("  memo: airbank research"))


def cmd_analysts(*_):
    from . import analysts
    banner()
    print(bold("The deal team") + dim("  — deploy one: airbank deploy <name>"))
    print()
    state = load_state()
    desk = state.get("analyst_desk", {})
    for name, spec in analysts.ROSTER.items():
        last = desk.get(name)
        stamp = dim(f"last: {last['last_run_utc'][:16].replace('T', ' ')}") if last \
            else dim("never deployed")
        print(f"  {accent(name):<26s} {bold(spec['title']):<28s} {stamp}")
        print(f"             {dim(spec['desc'])}")
        if last:
            print(f"             {dim('→ ' + last['headline'])}")
        print()


def cmd_deploy(name=None, *_):
    from . import analysts
    if name not in analysts.ROSTER:
        print(f"usage: airbank deploy <{'|'.join(analysts.ROSTER)}>")
        sys.exit(1)
    print(dim(f"deploying {analysts.ROSTER[name]['title']} — briefing on the live pipeline …"))
    path, headline = analysts.deploy(name)
    print(good(f"report filed: {headline}"))
    print(dim(f"  {path}"))


def cmd_research(index="1", *_):
    from . import analysts
    files = analysts.reports()
    if not files:
        print(dim("no research yet — deploy the team: airbank deploy screening"))
        return
    if index == "list":
        for i, f in enumerate(files[:15], 1):
            print(f"  {accent(str(i)):>4s}  {f.name}")
        return
    n = int(index) if str(index).isdigit() else 1
    target = files[min(n, len(files)) - 1]
    print(dim(f"— {target.name} —\n"))
    print(target.read_text())


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
    if decision == "approved":
        print(good(f"approved {approval['kind']} for {approval['company']} — "
                   f"executes next cycle"))
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


def cmd_dash(*_):
    from . import dashboard
    dashboard.run()


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
    print(good(f"the bank is sourcing 24/7 — one cycle every {int(interval) // 60} min"))
    print(dim("watch it: airbank   ·   stop it: airbank stop"))


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
                   config.MODE if config.ONBOARDED else "run `airbank init`"))
    claude_bin = shutil.which("claude")
    checks.append(("claude CLI (the agents)", bool(claude_bin), claude_bin or "not found"))
    checks.append(("leads inbox", True,
                   f"{config.HOME_DIR / 'leads'} — drop JSON/CSV from any tool"))
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


def cmd_contract(*_):
    print((config.PKG_DIR / "contract.md").read_text())


def cmd_version(*_):
    print(f"airbank {__version__}")


def help_text():
    return f"""{accent(ui.BANNER)}
{bold('  Airbank by Finsider')} — the AI-native investment bank in your terminal

    {accent(bold('airbank'))}                   open the terminal (live pipeline, desk chat)

  {bold('setup')}
    airbank init              onboarding wizard (mandate, mode)
    airbank doctor            health check
    airbank contract          print the loop contract (the graded spec)

  {bold('the pipeline')}
    airbank run               one cycle: source → advance → diligence → verify
    airbank start [seconds]   run 24/7 via launchd (default: every 900s)
    airbank stop              stop the 24/7 loop
    airbank deals             every deal in the book
    airbank deal <id>         one deal: history, diligence, folder
    airbank diligence <id>    run the Finsider engine on a deal now

  {bold('the team')}
    airbank analysts          the deal team roster
    airbank deploy <name>     deploy an agent — files a markdown report
    airbank research [n|list] read the latest (or nth) report

  {bold('operate')}
    airbank status            funnel, top of book, pending approvals
    airbank approve <id>      approve pending outreach or an LOI
    airbank reject <id>       reject a pending action
    airbank halt [reason]     kill switch
    airbank resume            clear halt after review

  {dim('Live mode: no outreach, no LOI leaves the building without your approval.')}
"""


COMMANDS = {
    "init": cmd_init, "status": cmd_status, "run": cmd_run, "run-once": cmd_run,
    "deals": cmd_deals, "deal": cmd_deal, "diligence": cmd_diligence,
    "halt": cmd_halt, "resume": cmd_resume, "start": cmd_start, "stop": cmd_stop,
    "doctor": cmd_doctor, "contract": cmd_contract,
    "dash": cmd_dash, "terminal": cmd_dash, "analysts": cmd_analysts,
    "deploy": cmd_deploy, "research": cmd_research,
    "version": cmd_version, "--version": cmd_version,
    "approve": lambda *a: cmd_decide("approved", *a),
    "reject": lambda *a: cmd_decide("rejected", *a),
}

NO_ONBOARD = {"init", "help", "-h", "--help", "version", "--version",
              "contract", "stop"}


def main():
    args = sys.argv[1:]
    if not args:
        args = ["dash"] if sys.stdin.isatty() else ["help"]
    if args[0] in ("help", "-h", "--help") or args[0] not in COMMANDS:
        print(help_text())
        sys.exit(0 if args[0] in ("help", "-h", "--help") else 1)
    if not config.ONBOARDED and args[0] not in NO_ONBOARD and sys.stdin.isatty():
        from . import onboard
        try:
            if onboard.run():
                os.execvp(sys.argv[0], sys.argv)
        except KeyboardInterrupt:
            print(dim("\nsetup skipped — running in simulation with defaults"))
    try:
        COMMANDS[args[0]](*args[1:])
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
