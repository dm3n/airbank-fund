"""First-run onboarding: a step-through terminal wizard (contract assertions
28–30). Persists choices to ~/.airbank/config.json; secrets to config.env."""
import json
import shutil
import sys
from datetime import datetime, timezone

from . import config, ui

STEPS = 4


def _header(step, title):
    ui.clear()
    print(ui.accent(ui.BANNER))
    print(ui.bold("  Airbank by Finsider") + ui.dim("  ·  fund setup"))
    print()
    print(ui.dim(f"  step {step}/{STEPS} ") + ui.bold(title))
    ui.hr()


def _valid_cash(value):
    try:
        v = float(value.replace(",", "").replace("$", ""))
    except ValueError:
        return False, "enter a number, e.g. 100000"
    if not 100 <= v <= 1_000_000_000:
        return False, "between $100 and $1B please"
    return True, ""


def _valid_address(chain):
    def check(value):
        if chain == "btc" and 26 <= len(value) <= 62:
            return True, ""
        if chain == "eth" and value.startswith("0x") and len(value) == 42:
            return True, ""
        return False, f"that doesn't look like a {chain.upper()} address"
    return check


def _reset_account_state_if_changed(account):
    """A new account is a fresh book: drop portfolio views, history, counters
    from the previous account. Strategy gates survive (account-independent)."""
    from .state import load_state, log, save_state
    if config.load_product_config().get("account") == account:
        return
    state = load_state()
    for key in ("mock", "wallet_view", "portfolio_view", "equity_history",
                "pending_approvals", "positions"):
        state.pop(key, None)
    state["pending_approvals"] = []
    state["trades_today"] = 0
    state["day_start_equity"] = None
    save_state(state)
    log("account-switch", f"state reset for new {account['type']} account")


def run():
    """Returns True when a config was written."""
    product = {"created_utc": datetime.now(timezone.utc).isoformat()}
    secrets = []

    # ---- step 1: welcome
    _header(1, "Welcome")
    print("""
  You're sixty seconds from a running AI-native hedge fund.

  The fund is an agent loop: it gathers market data, computes systematic
  signals, has an LLM analyst judge every trade, executes within hard
  risk caps, and grades itself against a written contract every cycle.

  Nothing here touches real money unless you explicitly wire a live
  brokerage account — and even then, every trade waits for your approval.
""")
    ui.select("Ready?", ["Let's build the fund"], ["enter to continue"])

    # ---- step 2: account
    _header(2, "Choose your account")
    print()
    kinds = ["mock", "alpaca_paper", "alpaca_live", "wallet"]
    idx = ui.select(
        "How should the fund trade?",
        ["Mock portfolio", "Alpaca paper", "Alpaca live", "Watch-only crypto wallet"],
        ["simulated cash, real market prices — zero setup, the default",
         "Alpaca's free paper-trading account (needs API keys)",
         "real money — triple-locked, every trade needs your approval",
         "track a public BTC/ETH address; the fund researches but never trades"])
    account = {"type": kinds[idx]}
    print()

    if kinds[idx] == "mock":
        cash = ui.text("  Starting cash", default="100,000",
                       validate=_valid_cash, prefix="$")
        account["starting_cash"] = float(cash.replace(",", "").replace("$", ""))
        pos = max(100.0, account["starting_cash"] * 0.02)
        gross = max(500.0, account["starting_cash"] * 0.10)
        print(ui.dim(f"  risk caps scale with your bankroll: "
                     f"{ui.money(pos)}/position, {ui.money(gross)} gross"))
    elif kinds[idx] in ("alpaca_paper", "alpaca_live"):
        if kinds[idx] == "alpaca_live":
            print(ui.bad(ui.bold("  LIVE MONEY.")) + " Three locks apply: this choice, "
                  "live_ack in state, and per-trade approval.")
            print()
        key = ui.text("  Alpaca API key")
        secret = ui.text("  Alpaca API secret", secret=True)
        if key:
            secrets += [f"ALPACA_API_KEY={key}", f"ALPACA_API_SECRET={secret}"]
        else:
            print(ui.warn("  no key entered — the fund will run in research mode"))
    else:  # wallet
        chain_idx = ui.select("Which chain?", ["Bitcoin", "Ethereum"],
                              ["public address, e.g. bc1q…", "public address, 0x…"])
        chain = ["btc", "eth"][chain_idx]
        address = ui.text(f"  {chain.upper()} address", validate=_valid_address(chain))
        account["wallet"] = {"chain": chain, "address": address}
    product["account"] = account

    # ---- step 3: theme (live preview: the sample strip re-colors as you move)
    _header(3, "Pick your style")
    print()
    names = list(ui.THEMES)

    def _sample():
        return (f"  {ui.accent('▮▮ AIRBANK')}  {ui.accent2('BTC/USD 60,736')}  "
                f"{ui.good('+2.4%')}  {ui.bad('-0.8%')}  "
                f"{ui.accent(ui.SPARK * 2)}  {ui.dim('the fund, in this theme')}")

    def _preview(i):
        # cursor sits on the menu's first line; the sample strip is 3 rows up
        ui.set_theme(names[i])
        sys.stdout.write("\0337\033[3A\r\033[2K" + _sample() + "\0338")
        sys.stdout.flush()

    if sys.stdin.isatty():
        print(_sample())
        print()
        theme_idx = ui.select("Theme", [ui.THEMES[n]["label"] for n in names],
                              preview=_preview)
    else:
        theme_idx = ui.select("Theme", [ui.THEMES[n]["label"] for n in names])
    product["theme"] = names[theme_idx]
    ui.set_theme(names[theme_idx])

    # ---- step 4: confirm + save
    _header(4, "Confirm")
    print()
    label = {"mock": f"Mock portfolio · {ui.money(account.get('starting_cash', 0))} starting cash",
             "alpaca_paper": "Alpaca paper trading",
             "alpaca_live": "Alpaca LIVE (approval-gated)",
             "wallet": f"Watch-only {account.get('wallet', {}).get('chain', '?').upper()} wallet"}
    print(f"  account   {ui.bold(label[account['type']])}")
    print(f"  theme     {ui.bold(product['theme'])}")
    print(f"  config    {ui.dim(str(config.CONFIG_JSON))}")
    print(f"  contract  {ui.dim(str(config.HOME_DIR / 'contract.md'))}")
    print()
    if ui.select("Save and launch?", ["Save — let's print alpha", "Start over"],
                 ["", "re-run the wizard"]) == 1:
        return run()

    product["onboarded"] = True
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _reset_account_state_if_changed(account)
    config.CONFIG_JSON.write_text(json.dumps(product, indent=2) + "\n")
    contract_dst = config.HOME_DIR / "contract.md"
    if not contract_dst.exists():
        shutil.copy(config.PKG_DIR / "contract.md", contract_dst)
    if secrets:
        existing = config.CONFIG_ENV.read_text() if config.CONFIG_ENV.exists() else ""
        keep = [l for l in existing.splitlines()
                if l and not l.startswith(("ALPACA_API_KEY", "ALPACA_API_SECRET"))]
        config.CONFIG_ENV.write_text("\n".join(keep + secrets) + "\n")
        config.CONFIG_ENV.chmod(0o600)

    print()
    print(ui.good(ui.bold("  ✓ fund configured")))
    _autonomous_setup()
    return True


def _autonomous_setup():
    """Seamless finish: gate the strategies and start the 24/7 loop without
    asking the user to learn commands. The terminal opens right after."""
    from .state import load_state
    if not load_state().get("strategy_gates"):
        print(ui.dim("\n  gating strategies on a year of market data …"))
        try:
            from . import backtest
            results = backtest.run(365)
            for name, r in results.items():
                badge = ui.good("ELIGIBLE") if r["eligible"] else ui.dim("benched")
                print(f"    {name:<10s} sharpe {r['portfolio']['sharpe']:5.2f}  {badge}")
        except Exception as exc:
            print(ui.warn(f"    backtest skipped ({str(exc)[:50]}) — press B in the terminal"))
    try:
        from .cli import PLIST_PATH, cmd_start
        if not PLIST_PATH.exists():
            print(ui.dim("\n  starting the 24/7 loop …"))
            cmd_start()
    except Exception as exc:
        print(ui.warn(f"  loop not started ({str(exc)[:50]}) — run `airbank start`"))
    print(ui.dim("\n  opening your terminal …"))
    import time
    time.sleep(1.2)
