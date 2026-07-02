"""The Airbank terminal: a full-screen Bloomberg-style fund view with a
Claude Code-style chat bar as the primary way to drive it. Typing bare
`airbank` lands here (contract assertion 36). Background threads stream
quotes and run actions; only the main thread draws (assertion 38).
Live-money approvals stay explicit CLI commands (assertion 37)."""
import os
import re
import select as _select
import shutil
import sys
import termios
import textwrap
import threading
import time
import tty
from datetime import datetime, timezone

from . import analysts, chat, config, ui
from .quotes import QuoteBoard
from .state import LOG_FILE, load_state

ANSI_RE = re.compile(r"\033\[[0-9;]*m")
THINK_VERBS = ["thinking", "reading the tape", "checking the book",
               "weighing it", "running the numbers"]

SLASH_COMMANDS = {
    "/run": "run one cycle: gather → reason → act → verify",
    "/deploy": "deploy an analyst — premarket · macro · crypto · equity · risk · journal",
    "/backtest": "re-gate the strategies on a year of daily bars",
    "/dash": "the full dashboard",
    "/hybrid": "desk chat inside the grid",
    "/chat": "full-screen desk chat",
    "/quit": "leave the terminal (the 24/7 loop keeps running)",
}


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
        self.status = ui.dim("your fund is live — say something to the desk below")
        self.view = "dash"            # dash | hybrid | chat
        self.input = ""
        self.transcript = []          # (role, text): user | assistant | system
        self.thinking = False
        self.think_started = 0.0
        self.reveal = 0               # typewriter progress on last assistant msg
        self.busy = ""
        self.frame = 0
        self.switch_anim = False
        self.sugg_idx = 0
        self.quit = False

    def _breather(self):
        """The working indicator: text that breathes. One full inhale/exhale
        takes ~2.5s — the color steps through the shark-blue ramp one shade
        at a time, no flashing, clean to the last pixel."""
        elapsed = time.time() - self.think_started
        step = int(elapsed / 0.21)          # one shade every ~0.21s
        verb = THINK_VERBS[int(elapsed) // 5 % len(THINK_VERBS)]
        return ui.breath(f"✻ {verb}… ({int(elapsed)}s)", step)

    def _goto(self, view):
        if view != self.view:
            self.view = view
            self.switch_anim = True

    # -------- background actions (threads set state; never draw)

    def _say(self, role, text):
        self.transcript.append((role, text))
        if role == "assistant":
            self.reveal = 0

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
                self._say("system", f"{name} failed: {str(exc)[:80]}")
            finally:
                self.busy = ""
        threading.Thread(target=runner, daemon=True).start()

    def action_cycle(self):
        def go():
            from .loop import run_cycle
            self.status = ui.warn("CYCLE RUNNING — gather → reason → act → verify …")
            score, cycle = run_cycle()
            msg = (f"cycle done · score {score:.2f} · {len(cycle['candidates'])} candidates · "
                   f"{len(cycle['executed'])} executed · {len(cycle['refused'])} refused")
            self.status = ui.good(msg)
            self._say("system", msg)
        self._spawn("cycle", go)

    def action_deploy(self, name):
        def go():
            self.status = ui.warn(f"DEPLOYING {analysts.ROSTER[name]['title'].upper()} …")
            path, headline = analysts.deploy(name)
            self.status = ui.good(f"{name} filed: {headline[:70]}")
            self._say("system", f"{name} filed: {headline} → {path}")
        self._spawn(f"deploy:{name}", go)

    def action_backtest(self):
        def go():
            from . import backtest
            self.status = ui.warn("BACKTESTING the universe — one year of daily bars …")
            results = backtest.run(365)
            gates = ", ".join(f"{k} {'ELIGIBLE' if v['eligible'] else 'benched'}"
                              for k, v in results.items())
            self.status = ui.good(f"backtest done: {gates}")
            self._say("system", f"backtest done: {gates}")
        self._spawn("backtest", go)

    def ask(self, message):
        self._say("user", message)
        if self.view == "dash":       # chat lands where the tape was
            self._goto("hybrid")
        self.thinking = True
        self.think_started = time.time()

        def go():
            try:
                reply, action = chat.respond(
                    message, self.transcript[:-1], quotes=self.board.snapshot())
            finally:
                self.thinking = False
            self._say("assistant", reply)
            if action:
                self.dispatch(action)
        threading.Thread(target=go, daemon=True).start()

    def dispatch(self, action):
        parts = action.split()
        verb = parts[0] if parts else ""
        if verb == "run":
            self._say("system", "→ running a cycle …")
            self.action_cycle()
        elif verb == "backtest":
            self._say("system", "→ backtesting …")
            self.action_backtest()
        elif verb == "deploy" and len(parts) > 1 and parts[1] in analysts.ROSTER:
            self._say("system", f"→ deploying {parts[1]} …")
            self.action_deploy(parts[1])
        elif verb:
            self._say("system", f"unknown action: {action}")

    # -------- frame pieces (dash view — unchanged above the chat bar)

    def _ticker(self, quotes, width):
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
            lines.append(ui.dim("no book yet — ask the desk to run a cycle"))
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
            lines.append(ui.dim("no gates yet — ask the desk to backtest"))
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

    # -------- chat view

    def _chat_lines(self, width):
        wrap = max(30, width - 8)
        lines = []
        for i, (role, text) in enumerate(self.transcript):
            is_last = i == len(self.transcript) - 1
            if role == "assistant" and is_last and self.reveal < len(text):
                text = text[:self.reveal]
            if role == "user":
                # the submitted message carries a slight shark-blue highlight
                for j, seg in enumerate(textwrap.wrap(text, wrap) or [""]):
                    mark = ui.accent2("> ") if j == 0 else "  "
                    if ui.color_on():
                        seg = f"\033[{ui.SHARK_DIM_BG};{ui.SHARK_LIGHT}m {seg} \033[0m"
                    lines.append(mark + seg)
            elif role == "assistant":
                in_code, first = False, True
                for raw in text.splitlines() or [""]:
                    if raw.strip().startswith("```"):
                        in_code = not in_code
                        continue
                    if in_code:      # fenced code: a Claude-style slab
                        line = raw[:wrap - 2]
                        if ui.color_on():
                            line = f"\033[48;5;236;38;5;252m  {line.ljust(wrap - 2)}\033[0m"
                        lines.append("  " + line)
                        continue
                    for seg in textwrap.wrap(raw, wrap) or [""]:
                        lines.append((ui.accent("⏺ ") if first else "  ") + ui.rich(seg))
                        first = False
            else:
                lines.append(ui.dim("  → " + text))
            lines.append("")
        if self.thinking:
            lines.append(self._breather())
        if not lines:
            lines = ["", ui.dim("  this is your desk — ask about the book, the tape,"),
                     ui.dim("  the gates, or tell it to deploy an analyst."), "",
                     ui.dim("  try: “how are we positioned?”  ·  “deploy the premarket analyst”")]
        return lines

    # -------- the bottom: Claude Code-style chat bar (both views)

    def suggestions(self):
        """Closest slash-command matches for the current input — the
        Claude Code autocomplete (contract assertion 47)."""
        if not self.input.startswith("/"):
            return []
        parts = self.input.split()
        head = parts[0].lower() if parts else "/"
        past_first_word = len(parts) > 1 or self.input.endswith(" ")
        if head == "/deploy" and past_first_word:
            prefix = parts[1].lower() if len(parts) > 1 else ""
            return [(f"/deploy {name}", analysts.ROSTER[name]["desc"])
                    for name in analysts.ROSTER if name.startswith(prefix)]
        if past_first_word:
            return []
        return [(c, d) for c, d in SLASH_COMMANDS.items() if c.startswith(head)]

    def _complete(self):
        matches = self.suggestions()
        if not matches:
            return
        cmd = matches[min(self.sugg_idx, len(matches) - 1)][0]
        self.input = cmd + (" " if cmd == "/deploy" else "")
        self.sugg_idx = 0

    def _chat_bar(self, width):
        inner = width - 2
        status = self._breather() if self.thinking else self.status
        rows = [clip(" " + status, width)]
        if self.input:
            shown = self.input[-(inner - 5):]
            content = ui.accent("> ") + ui.bold(shown) + ui.cursor_block()
        else:
            content = ui.accent("> ") + ui.dim(
                "ask your fund anything…  /  for commands") + ui.cursor_block()
        rows.append(ui.dim("╭" + "─" * inner + "╮"))
        rows.append(ui.dim("│") + pad(" " + clip(content, inner - 2), inner) + ui.dim("│"))
        rows.append(ui.dim("╰" + "─" * inner + "╯"))
        matches = self.suggestions()
        if matches:
            self.sugg_idx = min(self.sugg_idx, len(matches) - 1)
            for i, (cmd, desc) in enumerate(matches[:6]):
                line = f"   {cmd:<18s} {desc}"
                if i == self.sugg_idx and ui.color_on():
                    line = f"\033[{ui.SHARK_BG};38;5;231m{pad(line, min(width, 78))}\033[0m"
                elif i == self.sugg_idx:
                    line = " ▸ " + line[3:]
                else:
                    line = ui.dim(line)
                rows.append(clip(line, width))
            rows.append(ui.dim("   ↑↓ choose · tab complete · ⏎ run"))
        else:
            back = {"chat": "hybrid", "hybrid": "dashboard", "dash": "desk"}[self.view]
            left = f"  ⏎ send   esc {back}   /  commands   ⌃c quit"
            right = f"{config.ACCOUNT_TYPE.upper().replace('_', ' ')} · airbank by finsider  "
            rows.append(clip(pad(ui.dim(left), width - vlen(right)) + ui.dim(right), width))
        return rows

    # -------- frame

    def build(self, width, height):
        self.frame += 1
        state = load_state()
        quotes = self.board.snapshot()
        view = state.get("portfolio_view") or {}
        now = datetime.now(timezone.utc)

        mkt = ui.good("US OPEN") if us_market_open(now) else ui.dim("US CLOSED")
        account = config.ACCOUNT_TYPE.upper().replace("_", " ")
        title = {"chat": "DESK CHAT", "hybrid": "AIRBANK TERMINAL · DESK"}.get(
            self.view, "AIRBANK TERMINAL")
        left = f" {title}  {ui.accent('▮▮')}  {account}"
        if state.get("halt"):
            left += "  " + ui.bad(ui.bold("⛔ HALTED"))
        right = f"CRYPTO 24/7 · {mkt} · {now.strftime('%a %H:%M:%S UTC')} "
        bar = pad(left, width - vlen(right)) + right
        top = "\033[7m" + pad(clip(bar, width), width) + "\033[0m" if ui.color_on() \
            else pad(clip(bar, width), width)
        rows = [top, self._ticker(quotes, width)]

        bottom = self._chat_bar(width)
        body_h = height - len(rows) - len(bottom)

        if self.view == "chat":
            chat_lines = self._chat_lines(width)
            visible = chat_lines[-(body_h - 2):]
            rows += panel("THE DESK — YOUR FUND, CONVERSATIONAL", visible, width, body_h)
        else:
            lw = max(42, int(width * 0.42))
            rw = width - lw - 1
            markets_h = len(self.board.symbols) + 3
            rows += beside(panel("MARKETS", self._markets(quotes), lw, markets_h),
                           panel("PORTFOLIO", self._portfolio(state, view, rw), rw, markets_h),
                           gap=1)
            desk_h = len(analysts.ROSTER) + 2
            rows += beside(panel("STRATEGY GATES · RISK", self._gates(state), lw, desk_h),
                           panel("ANALYST DESK", self._desk(state, rw), rw, desk_h),
                           gap=1)
            slot_h = max(4, height - len(rows) - len(bottom))
            if self.view == "hybrid":
                # the desk chat lives where the tape was — grid stays put
                chat_lines = self._chat_lines(width)
                rows += panel("THE DESK", chat_lines[-(slot_h - 2):], width, slot_h)
            else:
                tape_lines = []
                if LOG_FILE.exists():
                    recent = [l for l in LOG_FILE.read_text().strip().splitlines()
                              if l.strip()]
                    for line in recent[-(slot_h - 2):]:
                        if line.startswith("## "):
                            line = ui.accent(line[3:])
                        tape_lines.append(line)
                else:
                    tape_lines.append(ui.dim("the loop hasn't spoken yet"))
                rows += panel("TAPE — THE LOOP, THINKING", tape_lines, width, slot_h)

        while len(rows) < height - len(bottom):
            rows.append("")
        rows = rows[:height - len(bottom)] + bottom
        return [clip(r, width) for r in rows[:height]]

    # -------- key handling

    def handle(self, key):
        if key == "\x03":                       # ctrl-c
            return False
        if key in ("UP", "DOWN"):               # autocomplete navigation
            matches = self.suggestions()
            if matches:
                self.sugg_idx = (self.sugg_idx + (1 if key == "DOWN" else -1)) % min(len(matches), 6)
            return True
        if key == "\t":                         # tab: complete to the pick
            self._complete()
            return True
        if key == "\x1b":                       # esc: step back toward the dashboard
            if self.input.startswith("/"):
                self.input = ""                 # esc first dismisses the menu
                return True
            self._goto({"chat": "hybrid", "hybrid": "dash", "dash": "hybrid"}[self.view])
            return True
        if key in ("\r", "\n"):
            return self.submit()
        if key in ("\x7f", "\x08"):
            self.input = self.input[:-1]
            self.sugg_idx = 0
            return True
        if key.isprintable() and len(key) == 1:
            self.input += key
            self.sugg_idx = 0
        return True

    def submit(self):
        # enter with the menu open runs the highlighted command
        matches = self.suggestions()
        if matches and self.input.strip() != matches[min(self.sugg_idx, len(matches) - 1)][0]:
            self._complete()
            if self.input.endswith(" "):        # /deploy — needs its analyst
                return True
        message = self.input.strip()
        self.input = ""
        self.sugg_idx = 0
        if not message:
            return True
        if message.startswith("/"):
            return self.slash(message)
        self.ask(message)
        return True

    def slash(self, message):
        parts = message[1:].split()
        cmd = parts[0].lower() if parts else ""
        if cmd in ("quit", "exit", "q"):
            return False
        if cmd == "run":
            self.action_cycle()
        elif cmd == "backtest":
            self.action_backtest()
        elif cmd == "deploy":
            if len(parts) > 1 and parts[1] in analysts.ROSTER:
                self._say("system", f"→ deploying {parts[1]} …")
                self.action_deploy(parts[1])
            else:
                self.status = ui.warn("deploy who? " + " ".join(analysts.ROSTER))
        elif cmd in ("dash", "dashboard"):
            self._goto("dash")
        elif cmd == "chat":
            self._goto("chat")
        elif cmd == "hybrid":
            self._goto("hybrid")
        else:
            self.status = ui.warn(
                f"unknown command /{cmd} — /run /deploy /backtest /dash /hybrid /chat /quit")
        return True

    # -------- lifecycle

    def _draw(self, rows):
        sys.stdout.write("\033[H" + "\r\n".join(row + "\033[K" for row in rows))
        sys.stdout.flush()

    def _animate(self, width, height):
        """Top-to-bottom reveal when flipping between pages."""
        rows = self.build(width, height)
        step = max(2, height // 14)
        for k in range(step, height + step, step):
            self._draw(rows[:k] + [""] * (height - min(k, height)))
            time.sleep(0.016)
        self.switch_anim = False

    def run(self):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        sys.stdout.write("\033[?1049h\033[?25l")
        self.board.start()
        try:
            tty.setcbreak(fd)
            alive = True
            while alive:
                width, height = shutil.get_terminal_size((110, 32))
                if self.switch_anim:
                    self._animate(width, height)
                self._draw(self.build(width, height))
                animating = self.thinking or (
                    self.transcript and self.transcript[-1][0] == "assistant"
                    and self.reveal < len(self.transcript[-1][1]))
                if self.transcript and self.transcript[-1][0] == "assistant":
                    remaining = len(self.transcript[-1][1]) - self.reveal
                    if remaining > 0:
                        self.reveal += max(3, remaining // 12)
                readable, _, _ = _select.select([fd], [], [], 0.07 if animating else 1.0)
                if readable:
                    burst = os.read(fd, 64).decode(errors="ignore")
                    if burst == "\x1b":
                        alive = self.handle("\x1b")
                    else:
                        # translate arrows for autocomplete; strip any other
                        # escape sequence so it never leaks into the input
                        burst = burst.replace("\x1b[A", "\x00UP\x00")
                        burst = burst.replace("\x1b[B", "\x00DOWN\x00")
                        burst = re.sub(r"\x1b(\[[0-9;]*[A-Za-z~])?", "", burst)
                        for token in burst.split("\x00"):
                            keys = [token] if token in ("UP", "DOWN") else list(token)
                            for ch in keys:
                                alive = self.handle(ch)
                                if not alive:
                                    break
                            if not alive:
                                break
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
