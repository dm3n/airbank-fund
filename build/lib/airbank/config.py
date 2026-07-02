"""Airbank config: env loading, universe, hard risk caps (see contract.md)."""
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
ACCOUNT = PRODUCT.get("account", {})
# mock | alpaca_paper | alpaca_live | wallet — mock is the zero-setup default
ACCOUNT_TYPE = ACCOUNT.get("type", "mock")
STARTING_CASH = float(ACCOUNT.get("starting_cash", 100_000.0))
WALLET = ACCOUNT.get("wallet", {})
THEME = PRODUCT.get("theme", "midnight")

# env override kept for ops/testing; onboarding choice is the primary lock
MODE = os.environ.get("AIRBANK_MODE") or (
    "live" if ACCOUNT_TYPE == "alpaca_live" else "paper")
LIVE = MODE == "live"

ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_API_SECRET", "")
ALPACA_TRADE_URL = (
    "https://api.alpaca.markets" if LIVE else "https://paper-api.alpaca.markets"
)
ALPACA_DATA_URL = "https://data.alpaca.markets"
SLACK_WEBHOOK_URL = os.environ.get("AIRBANK_SLACK_WEBHOOK", "")

HAS_BROKER = bool(ALPACA_KEY and ALPACA_SECRET)

CRYPTO_UNIVERSE = ["BTC/USD", "ETH/USD", "SOL/USD"]
EQUITY_UNIVERSE = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "META", "GOOGL"]

# Hard caps — contract.md table. CRITICAL: evaluator halts on breach.
# Mock accounts scale with chosen bankroll (assertion 31): 2% / 10%.
if ACCOUNT_TYPE == "mock":
    _pos_cap = max(100.0, STARTING_CASH * 0.02)
    _gross_cap = max(500.0, STARTING_CASH * 0.10)
else:
    _pos_cap = 200.0 if LIVE else 2000.0
    _gross_cap = 1000.0 if LIVE else 10000.0

CAPS = {
    "max_position_usd": _pos_cap,
    "max_gross_usd": _gross_cap,
    "max_trades_per_day": 10,
    "daily_loss_limit_pct": -3.0,
    "approval_ttl_hours": 4,
    "stale_data_min": {"crypto": 20, "equity": 30},
}

STRATEGY_PARAMS = {
    "momentum": {"fast": 20, "slow": 60, "lookback": 20},
    "meanrev": {"window": 20, "entry_z": -2.0, "exit_z": 0.0},
    "vol_filter": {"window": 20, "median_window": 252, "max_ratio": 2.0},
}

BACKTEST_GATE = {"min_sharpe": 0.5}
ANALYST_TIMEOUT_S = 120
