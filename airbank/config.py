"""Airbank config: env loading, the mandate, guardrails (see contract.md)."""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
HOME_DIR = Path(os.environ.get("AIRBANK_HOME", Path.home() / ".airbank"))
STATE_DIR = HOME_DIR / "state"
CONFIG_ENV = HOME_DIR / "config.env"
STACK_ENV = Path.home() / ".config" / "finsider-stack" / "stack.env"


def _load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"'))


_load_env_file(CONFIG_ENV)
_load_env_file(STACK_ENV)

CONFIG_JSON = HOME_DIR / "config.json"


def load_product_config():
    if not CONFIG_JSON.exists():
        return {}
    import json
    try:
        return json.loads(CONFIG_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


PRODUCT = load_product_config()
ONBOARDED = bool(PRODUCT.get("onboarded"))

# simulation runs the whole pipeline hands-free on demo flow;
# live queues every external action for approval
MODE = os.environ.get("AIRBANK_MODE") or PRODUCT.get("mode", "simulation")
SIMULATION = MODE != "live"
LIVE = not SIMULATION

# the mandate: what the bank hunts for (set in onboarding)
MANDATE = PRODUCT.get("mandate", {
    "firm": "Airbank Capital",
    "sectors": [],
    "size_min": 1_000_000,
    "size_max": 25_000_000,
})

SLACK_WEBHOOK_URL = os.environ.get("AIRBANK_SLACK_WEBHOOK", "")

# Guardrails — contract.md table. CRITICAL: evaluator halts on breach.
CAPS = {
    "max_outreach_per_day": 25,
    "max_touches_per_lead": 3,
    "max_leads_per_cycle": 20,
    "min_fit_score": 40.0,          # below this, a lead files as dead on arrival
    "min_diligence_score": 55.0,    # below this, pre-LOI memo says pass
    "approval_ttl_hours": {"outreach": 24, "loi": 72},
}
