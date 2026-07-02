"""The Airbank terminal: a full-screen, boxed-grid, Bloomberg-style live fund
view. Typing bare `airbank` lands here (contract assertion 36). Background
threads stream quotes and run actions; only the main thread draws (assertion
38). Live-money approvals stay explicit CLI commands (assertion 37)."""
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
    return s + " " * max(0, width - vlen(s))


def clip(s, width):
    """Clip to visible width, keeping ANSI sanity by closing style at the end."""
    if vlen(s) <= width:
        return s
    out, visible, i = [], 0, 0
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


def panel(title, lines, width, height):
    """A boxed panel with the title set into the top border."""
    inner = width - 2
    fill = max(0, inner - vlen(title) - 3)
    rows = [ui.dim("┌─") + ui.accent2(ui.bold(f" {title} ")) + ui.dim("─" * fill + "┐")]
    for i in range(height - 2):
        content = lines[i] if i < len(lines) else ""
        rows.append(ui.dim("│") + pad(clip(content, inner), inner) + ui.dim("│"))
    rows.append(ui.dim("└" + "─" * inner + "┘"))
    return rows


def beside(a, b, gap=0):
    """Merge two equal-height row lists side by side."""
    wa = max(vlen(r) for r in a) if a else 0
    out = []
    for i in range(max(len(a), len(b))):
        l = a[i] if i < len(a) else " " * wa
        r = b[i] if i < len(b) else ""
        out.append(pad(l, wa) + " " * gap + r)
    return out


def us_market_open(now=None):
    now = now or datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 13 * 60 + 30 <= minutes < 20 * 60


# ---------------------------------------------------------------- the app

class Terminal:
    def __init__(self):
        self.board = QuoteBoard()
        self.status = ui.dim("streaming live quotes …")
        self.mode = "main"          # main | deploy
        self.busy = ""
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
            self.status = ui.warn("CYCLE RUNNING — gather → reason → act → verify …")
            score, cycle = run_cycle()
            self.status = ui.good(
                f"cycle done · score {score:.2f} · {len(cycle['candidates'])} candidates "
                f"· {len(cycle['executed'])} executed · {len(cycle['refused'])} refused")
        self._spawn("cycle", go)

    def action_deploy(self, name):
        def go():
            self.status = ui.warn(f"DEPLOYING {analysts.ROSTER[name]['title'].upper()} …")
            _, headline = analysts.deploy(name)
            self.status = ui.good(f"{name} filed: {headline[:70]}")
        self._spawn(f"deploy:{name}", go)

    def action_backtest(self):
        def go():
            from . import backtest
            self.status = ui.warn("BACKTESTING the universe — one year of daily bars …")
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

    # -------- frame pieces

    def _ticker(self, quotes, width):
        """Rotating dense quote strip — the tape at the top of the screen."""
        items = []
        for symbol, _ in self.board.symbols:
            q = quotes.get(symbol)
            if not q or q.get("price") is None:
                continue
            arrow = ui.good("▲") if q["change_pct"] >= 0 else ui.bad("▼")
            items.append(f"{ui.accent2(symbol)} {q['price']:,.2f} "
                         f"{ui.pnl(q['change_pct'], pct=True)}{arrow}")
        if not items:
            return ui.dim(" … waiting for first prints …")
        start = self.frame // 3 % len(items)
        strip = "   ".join(items[start:] + items[:start])
        return clip(" " + strip, width)

    def _markets(self, quotes):
        lines = [ui.dim(f"{'SYM':<9}{'LAST':>12}  {'CHG%':>7}  TREND")]
        for symbol, asset_class in self.board.symbols:
            q = quotes.get(symbol)
            if not q or q.get("price") is None:
                lines.append(f"{symbol:<9}" + ui.dim(f"{'…':>12}"))
                continue
            spark = ui.sparkline(q["spark"], width=10) if len(q["spark"]) > 1 else ui.dim("·")
            lines.append(f"{symbol:<9}{q['price']:>12,.2f}  "
                         + pad(ui.pnl(q['change_pct'], pct=True), 7) + "  " + spark)
        if self.board.error:
            lines.append(ui.dim("feed: " + self.board.error))
        return lines

    def _portfolio(self, state, view, width):
        lines = []
        equity = view.get("equity")
        if equity is None:
            lines.append(ui.dim("no book yet — press R to run the first cycle"))
            return lines
        day_pnl = 0.0
        if state.get("day_start_equity"):
            day_pnl = (equity / state["day_start_equity"] - 1) * 100
        gross = sum(p.get("value", 0) for p in view.get("positions", []))
        cap = config.CAPS["max_gross_usd"]
        lines.append(f"EQUITY   {ui.bold(ui.money(equity)):<26} DAY      {ui.pnl(day_pnl, pct=True)}")
        lines.append(f"CASH     {ui.money(view.get('cash', 0)):<17} TOTAL    {ui.pnl(view.get('total_pnl_pct', 0), pct=True)}")
        realized = f"{ui.pnl(view['realized_pnl'])}" if "realized_pnl" in view else ui.dim("—")
        lines.append(f"GROSS    {ui.money(gross)} / {ui.money(cap):<8} REALIZED {realized}")
        lines.append(ui.sparkline(state.get("equity_history", []), width=min(46, width - 16))
                     + ui.dim("  equity 24h"))
        lines.append(ui.dim(f"{'POSITION':<12}{'QTY':>14}{'VALUE':>14}{'P&L':>9}"))
        positions = view.get("positions", [])
        if positions:
            for p in positions[:5]:
                lines.append(f"{ui.accent2(p['symbol']):<12}{p['qty']:>14,.6f}"
                             f"{ui.money(p['value']):>14}  " + ui.pnl(p['pnl_pct'], pct=True))
        else:
            lines.append(ui.dim("(flat — the loop is hunting for setups)"))
        pending = [a for a in state.get("pending_approvals", []) if a["status"] == "pending"]
        for a in pending[:2]:
            o = a["order"]
            lines.append(ui.warn(f"⏳ [{a['id']}] {o['side']} ${o['notional_usd']:.0f} "
                                 f"{o['symbol']} — airbank approve {a['id']}"))
        return lines

    def _gates(self, state):
        lines = [ui.dim(f"{'STRATEGY':<11}{'STATE':<11}{'SHARPE':>7}{'RET':>8}{'MAXDD':>8}")]
        gates = state.get("strategy_gates", {})
        if not gates:
            lines.append(ui.dim("no gates yet — press B to backtest"))
        for name, gate in gates.items():
            badge = ui.good("● ELIGIBLE") if gate["eligible"] else ui.dim("○ benched ")
            lines.append(f"{name.upper():<11}" + pad(badge, 11)
                         + f"{gate['sharpe']:>7.2f}{gate['total_return']:>8.1%}{gate['max_drawdown']:>8.1%}")
        lines.append("")
        lines.append(ui.dim("RISK  ")
                     + f"trades {state.get('trades_today', 0)}/{config.CAPS['max_trades_per_day']}"
                     + f"  kill {config.CAPS['daily_loss_limit_pct']:.0f}%/d"
                     + f"  cap ${config.CAPS['max_position_usd']:,.0f}"
                     + f"  gross ${config.CAPS['max_gross_usd']:,.0f}")
        return lines

    def _desk(self, state, width):
        lines = []
        desk = state.get("analyst_desk", {})
        for key, spec in analysts.ROSTER.items():
            if self.busy == f"deploy:{key}":
                stamp = ui.warn("deploying …")
            elif key in desk:
                info = desk[key]
                stamp = ui.dim(info["last_run_utc"][5:16].replace("T", " ") + "  ") \
                    + clip(info["headline"], max(10, width - 30))
            else:
                stamp = ui.dim(spec["desc"])
            lines.append(pad(ui.accent(key.upper()), 11) + stamp)
        return lines

    # -------- frame

    def build(self, width, height):
        self.frame += 1
        state = load_state()
        quotes = self.board.snapshot()
        view = state.get("portfolio_view") or {}
        now = datetime.now(timezone.utc)

        # top command bar (reverse video)
        mkt = ui.good("US OPEN") if us_market_open(now) else ui.dim("US CLOSED")
        account = config.ACCOUNT_TYPE.upper().replace("_", " ")
        left = f" AIRBANK TERMINAL  {ui.accent('▮▮')}  {account}"
        if state.get("halt"):
            left += "  " + ui.bad(ui.bold("⛔ HALTED"))
        right = f"CRYPTO 24/7 · {mkt} · {now.strftime('%a %H:%M:%S UTC')} "
        bar = pad(left, width - vlen(right)) + right
        top = "\033[7m" + pad(clip(bar, width), width) + "\033[0m" if ui.color_on() \
            else pad(clip(bar, width), width)

        rows = [top, self._ticker(quotes, width)]

        # grid
        lw = max(42, int(width * 0.42))
        rw = width - lw - 1
        markets_h = len(self.board.symbols) + 3
        row_a = beside(panel("MARKETS", self._markets(quotes), lw, markets_h),
                       panel("PORTFOLIO", self._portfolio(state, view, rw), rw, markets_h),
                       gap=1)
        gates_h = 7
        desk_h = len(analysts.ROSTER) + 2
        row_b_h = max(gates_h, desk_h)
        row_b = beside(panel("STRATEGY GATES · RISK", self._gates(state), lw, row_b_h),
                       panel("ANALYST DESK", self._desk(state, rw), rw, row_b_h),
                       gap=1)
        rows += row_a + row_b

        # tape fills the rest
        tape_h = max(4, height - len(rows) - 2)
        tape_lines = []
        if LOG_FILE.exists():
            recent = [l for l in LOG_FILE.read_text().strip().splitlines() if l.strip()]
            for line in recent[-(tape_h - 2):]:
                if line.startswith("## "):
                    line = ui.accent(line[3:])
                tape_lines.append(line)
        else:
            tape_lines.append(ui.dim("the loop hasn't spoken yet"))
        rows += panel("TAPE — THE LOOP, THINKING", tape_lines, width, tape_h)

        # status + function-key bar
        rows.append(clip(" " + self.status, width))
        if self.mode == "deploy":
            keys = [(str(i + 1), name.upper()) for i, name in enumerate(analysts.ROSTER)]
            keys.append(("ESC", "CANCEL"))
        else:
            keys = [("R", "RUN CYCLE"), ("D", "DEPLOY ANALYST"), ("B", "BACKTEST"),
                    ("T", "THEME"), ("Q", "QUIT")]
        fn = "  ".join(("\033[7m" if ui.color_on() else "") + f" {k} "
                       + ("\033[0m" if ui.color_on() else "") + f" {label}"
                       for k, label in keys)
        rows = rows[:height - 1]
        rows.append(clip(pad(" " + fn, width), width))
        return [clip(r, width) for r in rows[:height]]

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
        if key in ("q", "Q"):
            return False
        if key in ("r", "R"):
            self.action_cycle()
        elif key in ("d", "D"):
            self.mode = "deploy"
        elif key in ("b", "B"):
            self.action_backtest()
        elif key in ("t", "T"):
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
                width, height = shutil.get_terminal_size((110, 32))
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
