"""The Airbank terminal: a full-screen, live, Bloomberg-style fund view.
Typing bare `airbank` lands here (contract assertion 36). Background threads
stream quotes and run actions; only the main thread draws (assertion 38).
Live-money approvals stay explicit CLI commands (assertion 37)."""
import os
import re
import select as _select
import shutil
import sys
import termios
import threading
import time
import tty
from datetime import datetime, timezone

from . import analysts, config, ui
from .quotes import QuoteBoard
from .state import LOG_FILE, load_state

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vlen(s):
    return len(ANSI_RE.sub("", s))


def pad(s, width):
    gap = width - vlen(s)
    return s + " " * max(0, gap)


def clip(s, width):
    """Clip to visible width, keeping ANSI sanity by closing style at the end."""
    if vlen(s) <= width:
        return s
    out, visible = [], 0
    i = 0
    while i < len(s) and visible < width - 1:
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
        else:
            out.append(s[i])
            visible += 1
            i += 1
    return "".join(out) + "\033[0m…"


# ---------------------------------------------------------------- the app

class Terminal:
    def __init__(self):
        self.board = QuoteBoard()
        self.status = ui.dim("streaming quotes — keys: [r]un cycle  [d]eploy analyst  [b]acktest  [t]heme  [q]uit")
        self.mode = "main"          # main | deploy
        self.busy = ""              # name of running background action
        self.frame = 0

    # -------- actions (threads set status; never draw)

    def _spawn(self, name, fn):
        if self.busy:
            self.status = ui.warn(f"busy: {self.busy} still running")
            return
        self.busy = name

        def runner():
            try:
                fn()
            except Exception as exc:
                self.status = ui.bad(f"{name} failed: {str(exc)[:60]}")
            finally:
                self.busy = ""
        threading.Thread(target=runner, daemon=True).start()

    def action_cycle(self):
        def go():
            from .loop import run_cycle
            self.status = ui.warn("cycle running — gather → reason → act → verify …")
            score, cycle = run_cycle()
            self.status = ui.good(
                f"cycle done · score {score:.2f} · {len(cycle['candidates'])} candidates "
                f"· {len(cycle['executed'])} executed")
        self._spawn("cycle", go)

    def action_deploy(self, name):
        def go():
            self.status = ui.warn(f"deploying {analysts.ROSTER[name]['title']} …")
            _, headline = analysts.deploy(name)
            self.status = ui.good(f"{name} filed: {headline[:70]}")
        self._spawn(f"deploy:{name}", go)

    def action_backtest(self):
        def go():
            from . import backtest
            self.status = ui.warn("backtesting the universe …")
            results = backtest.run(365)
            gates = ", ".join(f"{k} {'ELIGIBLE' if v['eligible'] else 'benched'}"
                              for k, v in results.items())
            self.status = ui.good(f"backtest done: {gates}")
        self._spawn("backtest", go)

    def action_theme(self):
        import json
        names = list(ui.THEMES)
        nxt = names[(names.index(ui._theme_name) + 1) % len(names)]
        ui.set_theme(nxt)
        product = config.load_product_config()
        product["theme"] = nxt
        config.CONFIG_JSON.write_text(json.dumps(product, indent=2) + "\n")
        self.status = ui.good(f"theme: {nxt}")

    # -------- frame

    def build(self, width, height):
        state = load_state()
        quotes = self.board.snapshot()
        rows = []
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        view = state.get("portfolio_view") or {}
        equity = view.get("equity")
        day_pnl = 0.0
        if equity and state.get("day_start_equity"):
            day_pnl = (equity / state["day_start_equity"] - 1) * 100

        # header bar
        left = f" ▮ AIRBANK — AI-NATIVE HEDGE FUND · {config.ACCOUNT_TYPE}"
        mid = f"equity {ui.money(equity)}  day {ui.pnl(day_pnl, pct=True)}" if equity else "no book yet"
        head = pad(ui.bold(ui.accent(left)) + "   " + mid, width - len(now) - 1) + ui.dim(now)
        rows.append(clip(head, width))
        if state.get("halt"):
            rows.append(clip(ui.bad(ui.bold(
                f" ⛔ HALTED: {state.get('halt_reason', '')} — airbank resume")), width))
        rows.append(ui.dim("─" * width))

        lw = 38
        rw = width - lw - 3
        left_col, right_col = [], []

        # left: markets
        left_col.append(ui.bold(ui.accent2("MARKETS")))
        for symbol, _ in self.board.symbols:
            q = quotes.get(symbol)
            if not q or q.get("price") is None:
                left_col.append(f"{symbol:<9s} " + ui.dim("…"))
                continue
            price = f"{q['price']:>12,.2f}"
            chg = q["change_pct"]
            spark = ui.sparkline(q["spark"], width=10) if len(q["spark"]) > 1 else ""
            left_col.append(f"{symbol:<9s}{price} {ui.pnl(chg, pct=True)} {spark}")
        if self.board.error:
            left_col.append(ui.dim(clip("feed: " + self.board.error, lw)))
        left_col.append("")
        left_col.append(ui.bold(ui.accent2("STRATEGIES")))
        gates = state.get("strategy_gates", {})
        if not gates:
            left_col.append(ui.dim("no gates — press b to backtest"))
        for name, gate in gates.items():
            badge = ui.good("ELIGIBLE") if gate["eligible"] else ui.dim("benched ")
            left_col.append(f"{name:<10s}{badge} shp {gate['sharpe']:.2f} dd {gate['max_drawdown']:.0%}")

        # right: portfolio
        right_col.append(ui.bold(ui.accent2("PORTFOLIO")))
        if equity is None:
            right_col.append(ui.dim("no data yet — press r to run a cycle"))
        else:
            line = f"equity {ui.bold(ui.money(equity))}   cash {ui.money(view.get('cash', 0))}"
            line += f"   total {ui.pnl(view.get('total_pnl_pct', 0), pct=True)}"
            if "realized_pnl" in view:
                line += f"   realized {ui.pnl(view['realized_pnl'])}"
            right_col.append(line)
            right_col.append(ui.sparkline(state.get("equity_history", []), width=min(48, rw - 12))
                             + ui.dim("  24h equity"))
            positions = view.get("positions", [])
            if positions:
                for p in positions[:6]:
                    right_col.append(f"  {ui.accent2(p['symbol']):<20s} "
                                     f"{ui.money(p['value']):>14s}  {ui.pnl(p['pnl_pct'], pct=True)}")
            else:
                right_col.append(ui.dim("  no open positions — the loop is hunting"))
        pending = [a for a in state.get("pending_approvals", []) if a["status"] == "pending"]
        if pending:
            right_col.append(ui.warn(ui.bold(f"⏳ {len(pending)} awaiting approval — airbank approve <id>")))
            for a in pending[:3]:
                o = a["order"]
                right_col.append(f"  [{a['id']}] {o['side']} ${o['notional_usd']:.0f} {o['symbol']}")
        right_col.append("")
        right_col.append(ui.bold(ui.accent2("ANALYST DESK")))
        desk = state.get("analyst_desk", {})
        for key, spec in analysts.ROSTER.items():
            info = desk.get(key)
            if self.busy == f"deploy:{key}":
                stamp = ui.warn("deploying …")
            elif info:
                stamp = ui.dim(info["last_run_utc"][5:16].replace("T", " ")) + "  " + \
                    clip(info["headline"], max(10, rw - 34))
            else:
                stamp = ui.dim(spec["desc"])
            right_col.append("  " + pad(ui.accent(key), 12) + stamp)

        body_h = max(len(left_col), len(right_col))
        for i in range(body_h):
            l = left_col[i] if i < len(left_col) else ""
            r = right_col[i] if i < len(right_col) else ""
            rows.append(" " + pad(clip(l, lw), lw) + ui.dim("│ ") + clip(r, rw))

        # tape
        rows.append(ui.dim("─" * width))
        rows.append(clip(ui.bold(ui.accent2(" TAPE"))
                         + ui.dim("  the loop, thinking — " + str(LOG_FILE)), width))
        tape_h = max(3, height - len(rows) - 2)
        tape = LOG_FILE.read_text().strip().splitlines()[-tape_h:] if LOG_FILE.exists() else []
        for line in tape[-tape_h:]:
            if line.startswith("## "):
                line = ui.accent(line[3:])
            rows.append(clip(" " + line, width))
        while len(rows) < height - 1:
            rows.append("")

        # footer
        if self.mode == "deploy":
            foot = " deploy: " + "  ".join(
                f"{ui.accent(str(i + 1))} {name}" for i, name in enumerate(analysts.ROSTER)) \
                + "  " + ui.dim("esc cancel")
        else:
            foot = " " + self.status
        rows = rows[:height - 1]
        rows.append(clip(pad(foot, width), width))
        return rows

    # -------- key handling

    def handle(self, key):
        if self.mode == "deploy":
            names = list(analysts.ROSTER)
            if key.isdigit() and 1 <= int(key) <= len(names):
                self.mode = "main"
                self.action_deploy(names[int(key) - 1])
            elif key in ("\x1b", "q"):
                self.mode = "main"
                self.status = ui.dim("deploy cancelled")
            return True
        if key == "q":
            return False
        if key == "r":
            self.action_cycle()
        elif key == "d":
            self.mode = "deploy"
        elif key == "b":
            self.action_backtest()
        elif key == "t":
            self.action_theme()
        return True

    # -------- lifecycle

    def run(self):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        sys.stdout.write("\033[?1049h\033[?25l")  # alt screen, hide cursor
        self.board.start()
        try:
            tty.setcbreak(fd)
            alive = True
            while alive:
                width, height = shutil.get_terminal_size((100, 30))
                rows = self.build(width, height)
                sys.stdout.write("\033[H" + "\r\n".join(
                    row + "\033[K" for row in rows))
                sys.stdout.flush()
                readable, _, _ = _select.select([fd], [], [], 1.0)
                if readable:
                    key = os.read(fd, 8).decode(errors="ignore")
                    if key:
                        alive = self.handle(key[0] if key[0] != "\x1b" or len(key) == 1 else "\x1b")
        finally:
            self.board.stop()
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()


def run():
    if not sys.stdin.isatty():
        print("the airbank terminal needs a TTY — try `airbank status`")
        return
    Terminal().run()
