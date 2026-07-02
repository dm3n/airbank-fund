"""First-run onboarding: a step-through terminal wizard (contract §G).
Persists the mandate + mode to ~/.airbank/config.json, then autonomously
starts the 24/7 loop and opens the terminal."""
import json
import shutil
import sys
from datetime import datetime, timezone

from . import config, ui

STEPS = 3


def _header(step, title):
    ui.clear()
    print(ui.accent(ui.BANNER))
    print(ui.bold("  Airbank by Finsider") + ui.dim("  ·  bank setup"))
    print()
    print(ui.dim(f"  step {step}/{STEPS} ") + ui.bold(title))
    ui.hr()


def _valid_money(value):
    try:
        v = float(value.replace(",", "").replace("$", "").replace("m", "000000").replace("M", "000000"))
    except ValueError:
        return False, "enter a number, e.g. 1,000,000 or 5M"
    if not 10_000 <= v <= 10**10:
        return False, "between $10k and $10B please"
    return True, ""


def _money(value):
    return float(value.replace(",", "").replace("$", "").replace("m", "000000").replace("M", "000000"))


def run():
    """Returns True when a config was written."""
    product = {"created_utc": datetime.now(timezone.utc).isoformat()}

    # ---- step 1: welcome
    _header(1, "Welcome")
    print("""
  You're sixty seconds from a running AI-native investment bank.

  Airbank is an agent loop that works your M&A pipeline 24/7: it sources
  targets across channels, drafts personalized outreach, converts leads,
  runs Finsider-grade financial diligence pre-LOI, and carries deals
  through LOI, post-LOI QoE, and close.

  In live mode nothing external ever happens without your approval —
  outreach and LOIs queue as pending actions until you sign off.
""")
    ui.select("Ready?", ["Let's build the bank"], ["enter to continue"])

    # ---- step 2: mode + mandate
    _header(2, "Your mandate")
    print()
    mode_idx = ui.select(
        "How should the bank run?",
        ["Simulation", "Live pipeline"],
        ["hands-free demo deal flow — the full pipeline moves 24/7, zero setup",
         "real leads via ~/.airbank/leads/ (SearchFunder, Sales Navigator, "
         "Dripify, Origami exports) — every external action needs your approval"])
    product["mode"] = ["simulation", "live"][mode_idx]
    print()
    firm = ui.text("  Firm name", default="Airbank Capital")
    sectors_raw = ui.text("  Target sectors (comma-separated, blank = all)",
                          default="")
    lo = ui.text("  Deal size floor (revenue)", default="1,000,000",
                 validate=_valid_money, prefix="$")
    hi = ui.text("  Deal size ceiling (revenue)", default="25,000,000",
                 validate=_valid_money, prefix="$")
    product["mandate"] = {
        "firm": firm or "Airbank Capital",
        "sectors": [s.strip().lower() for s in sectors_raw.split(",") if s.strip()],
        "size_min": _money(lo),
        "size_max": max(_money(hi), _money(lo)),
    }
    if product["mode"] == "live":
        print()
        print(ui.dim("  connect your sources by dropping exports into "
                     f"{config.HOME_DIR / 'leads'}/"))
        print(ui.dim("  (JSON or CSV with a `company` column — SearchFunder, "
                     "Sales Navigator, Dripify/Zapier, Origami all export there)"))

    # ---- step 3: confirm + save
    _header(3, "Confirm")
    print()
    m = product["mandate"]
    print(f"  firm      {ui.bold(m['firm'])}")
    print(f"  mode      {ui.bold(product['mode'])}")
    print(f"  sectors   {ui.bold(', '.join(m['sectors']) or 'all')}")
    size_range = "${:,.0f} – ${:,.0f}".format(m["size_min"], m["size_max"])
    print(f"  size      {ui.bold(size_range)} revenue")
    print(f"  config    {ui.dim(str(config.CONFIG_JSON))}")
    print(f"  contract  {ui.dim(str(config.HOME_DIR / 'contract.md'))}")
    print()
    if ui.select("Save and launch?", ["Save — open the pipeline", "Start over"],
                 ["", "re-run the wizard"]) == 1:
        return run()

    product["onboarded"] = True
    config.HOME_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (config.HOME_DIR / "leads").mkdir(parents=True, exist_ok=True)
    config.CONFIG_JSON.write_text(json.dumps(product, indent=2) + "\n")
    contract_dst = config.HOME_DIR / "contract.md"
    shutil.copy(config.PKG_DIR / "contract.md", contract_dst)

    print()
    print(ui.good(ui.bold("  ✓ the bank is configured")))
    _autonomous_setup()
    return True


def _autonomous_setup():
    """Seamless finish: start the 24/7 loop and open the terminal."""
    try:
        from .cli import PLIST_PATH, cmd_start
        if not PLIST_PATH.exists():
            print(ui.dim("\n  starting the 24/7 sourcing loop …"))
            cmd_start()
    except Exception as exc:
        print(ui.warn(f"  loop not started ({str(exc)[:50]}) — run `airbank start`"))
    print(ui.dim("\n  opening your terminal …"))
    import time
    time.sleep(1.2)
