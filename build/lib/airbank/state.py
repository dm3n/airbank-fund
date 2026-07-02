"""State layer: exactly 3 files (contract.md, state.json, log.md). Crash-resume."""
import json
import os
import tempfile
from datetime import datetime, timezone

from .config import STATE_DIR

STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "log.md"

DEFAULT_STATE = {
    "halt": False,
    "halt_reason": "",
    "live_ack": False,
    "day": "",
    "day_start_equity": None,
    "trades_today": 0,
    "pending_approvals": [],
    "positions": {},
    "strategy_gates": {},
    "last_cycle_utc": "",
    "consecutive_failures": 0,
}


def now_utc():
    return datetime.now(timezone.utc)


def load_state():
    if not STATE_FILE.exists():
        return dict(DEFAULT_STATE)
    state = json.loads(STATE_FILE.read_text())
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    return merged


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)  # atomic — assertion 4


def log(op, title, body=""):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%Y-%m-%d %H:%M")
    entry = f"## [{stamp}] {op} | {title}\n"
    if body:
        entry += body.rstrip() + "\n"
    with open(LOG_FILE, "a") as f:
        f.write(entry + "\n")


def roll_day(state, equity):
    """Reset daily counters on a new UTC day; anchor equity for kill switch."""
    today = now_utc().strftime("%Y-%m-%d")
    if state["day"] != today:
        state["day"] = today
        state["day_start_equity"] = equity
        state["trades_today"] = 0
    elif state["day_start_equity"] is None and equity is not None:
        state["day_start_equity"] = equity
