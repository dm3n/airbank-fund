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

MODE = os.environ.get("AIRBANK_MODE", "paper")  # paper | live
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
CAPS = {
    "max_position_usd": 200.0 if LIVE else 2000.0,
    "max_gross_usd": 1000.0 if LIVE else 10000.0,
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
