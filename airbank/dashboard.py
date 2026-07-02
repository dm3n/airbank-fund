"""The Airbank terminal: the full-screen live view of the AI-native
investment bank, with the Claude Code-style chat bar as the primary way to
drive it. Typing bare `airbank` lands here (contract assertion 23). Panels:
DEAL FLOW · PIPELINE · CAMPAIGN & GUARDRAILS · DEAL TEAM · TAPE. Only the
main thread draws; approvals stay explicit CLI commands (assertion 25)."""
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

from . import analysts, chat, config, pipeline, sources, ui
from .state import LOG_FILE, load_state

ANSI_RE = re.compile(r"\033\[[0-9;]*m")
THINK_VERBS = ["thinking", "reading the pipeline", "checking the funnel",
               "weighing it", "working the numbers"]

SLASH_COMMANDS = {
    "/run": "run one cycle: source → advance → diligence → verify",
    "/deploy": "deploy the deal team — sourcing · outreach · screening · diligence · market · pipeline-coach",
    "/diligence": "run the Finsider diligence engine on a deal",
    "/dash": "the full dashboard",
    "/hybrid": "desk chat inside the grid",
    "/chat": "full-screen desk chat",
    "/quit": "leave the terminal (the 24/7 loop keeps running)",
}

TICKER_OPS = {"sourced": "SOURCED", "stage": "", "diligence": "DILIGENCE",
              "approval-pending": "AWAITING", "analyst": "FILED"}


def vlen(s):
    return len(ANSI_RE.sub("", s))


def pad(s, width):
    return s + " " * max(0, width - vlen(s))


def clip(s, width):
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


def _tape_entries(limit=40):
    """Recent tape headlines: (op, title) pairs, newest last."""
    if not LOG_FILE.exists():
        return []
    entries = []
    for line in LOG_FILE.read_text().splitlines():
        m = re.match(r"## \[[\d\- :]+\] ([\w\-]+) \| (.+)", line)
        if m:
            entries.append((m.group(1), m.group(2)))
    return entries[-limit:]


# ---------------------------------------------------------------- the app

class Terminal:
    def __init__(self):
        self.status = ui.dim("the bank is live — say something to the desk below")
        self.view = "dash"            # dash | hybrid | chat
        self.input = ""
        self.transcript = []          # (role, text): user | assistant | system
        self.thinking = False
        self.think_started = 0.0
        self.reveal = 0
        self.busy = ""
        self.frame = 0
        self.switch_anim = False
        self.sugg_idx = 0

    def _breather(self):
        elapsed = time.time() - self.think_started
        step = int(elapsed / 0.21)
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
            self.status = ui.warn("CYCLE RUNNING — source → advance → diligence → verify …")
            score, cycle = run_cycle()
            msg = (f"cycle done · score {score:.2f} · {cycle['new_leads']} sourced · "
                   f"{cycle['outreach']} outreach · {len(cycle['advanced'])} advanced · "
                   f"{cycle['memos']} memos")
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

    def action_diligence(self, needle):
        def go():
            from . import diligence
            state = load_state()
            deal = pipeline.find(state, needle)
            if deal is None:
                self.status = ui.warn(f"no deal matching {needle!r}")
                return
            self.status = ui.warn(f"DILIGENCE — {deal['company']} …")
            if config.SIMULATION:
                diligence.generate_demo_financials(deal)
            kind = "post_loi" if deal["stage"] in ("loi", "post_loi", "closing") else "pre_loi"
            m = diligence.run(state, deal, kind)
            from .state import save_state
            save_state(state)
            if m is None:
                msg = (f"{deal['company']}: waiting on financials — drop "
                       f"financials.csv into {pipeline.deal_dir(deal['id'])}")
                self.status = ui.warn(msg)
                self._say("system", msg)
            else:
                msg = (f"{deal['company']} diligence: score {m['score']} · "
                       + ("; ".join(m["flags"]) or "no flags"))
                self.status = ui.good(msg)
                self._say("system", msg + " · read it: airbank research")
        self._spawn("diligence", go)

    def ask(self, message):
        self._say("user", message)
        if self.view == "dash":
            self._goto("hybrid")
        self.thinking = True
        self.think_started = time.time()

        def go():
            try:
                reply, action = chat.respond(message, self.transcript[:-1])
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
        elif verb == "deploy" and len(parts) > 1 and parts[1] in analysts.ROSTER:
            self._say("system", f"→ deploying {parts[1]} …")
            self.action_deploy(parts[1])
        elif verb == "diligence" and len(parts) > 1:
            self._say("system", f"→ diligence on {' '.join(parts[1:])} …")
            self.action_diligence(" ".join(parts[1:]))
        elif verb:
            self._say("system", f"unknown action: {action}")

    # -------- frame pieces

    def _ticker(self, width):
        items = []
        for op, title in _tape_entries(18):
            tag = TICKER_OPS.get(op)
            if tag is None:
                continue
            label = f"{tag} " if tag else ""
            items.append(ui.accent2(label) + title)
        if not items:
            return ui.dim(" … the tape is quiet — the first leads are on their way …")
        start = self.frame // 3 % len(items)
        strip = ui.dim("  ·  ").join(items[start:] + items[:start])
        return clip(" " + strip, width)

    def _flow(self, state):
        lines = [ui.dim(f"{'SOURCE':<13}{'LEADS':>6}{'RESP':>6}  14D FLOW")]
        for src in sources.flow_board(state):
            name = src["name"]
            if not src["configured"]:
                lines.append(f"{name:<13}" + ui.dim("awaiting keys — feeds via ~/.airbank/leads/"))
                continue
            spark = ui.sparkline(src["spark"], width=12) if len(src["spark"]) > 1 else ui.dim("·")
            resp = f"{src['responses']}" if src["responses"] else ui.dim("—")
            lines.append(f"{ui.accent2(name):<13}{src['total']:>6}" + pad(resp, 6) + "  " + spark)
        return lines

    def _pipeline_panel(self, state, width):
        lines = []
        view = state.get("funnel_view") or {}
        counts = view.get("counts", {})
        if not counts:
            lines.append(ui.dim("no pipeline yet — the first cycle is coming"))
            return lines
        active_value = sum(view.get("value", {}).get(s, 0) for s in pipeline.ACTIVE_STAGES)
        lines.append(f"ACTIVE {ui.bold(str(view.get('active', 0)))} deals   "
                     f"PIPELINE {ui.bold('$' + format(active_value, ',.0f'))} revenue   "
                     f"CLOSED {ui.good(str(counts.get('closed', 0)))}")
        funnel = "  ".join(
            (ui.accent2(f"{pipeline.STAGE_LABEL[s]} {counts.get(s, 0)}")
             if counts.get(s) else ui.dim(f"{pipeline.STAGE_LABEL[s]} 0"))
            for s in pipeline.ACTIVE_STAGES)
        lines.append(clip(funnel, width - 4))
        lines.append(ui.sparkline(state.get("pipeline_history", []),
                                  width=min(46, width - 18)) + ui.dim("  active deals, 24h"))
        lines.append(ui.dim(f"{'DEAL':<24}{'STAGE':<10}{'FIT':>5}{'REVENUE':>13}"))
        for d in pipeline.active_deals(state)[:5]:
            lines.append(f"{ui.accent2(d['company'][:22]):<24}"
                         f"{pipeline.STAGE_LABEL[d['stage']]:<10}{d['score']:>5.0f}"
                         f"{'$' + format(d['revenue'], ',.0f'):>13}")
        pending = [a for a in state.get("pending_approvals", []) if a["status"] == "pending"]
        for a in pending[:2]:
            lines.append(ui.warn(f"⏳ [{a['id']}] {a['kind']} · {a['company']} — "
                                 f"airbank approve {a['id']}"))
        return lines

    def _campaign(self, state):
        m = config.MANDATE
        sectors = ", ".join(m.get("sectors", [])) or "all sectors"
        lines = [f"MANDATE  {ui.accent2(sectors)}",
                 f"SIZE     ${m.get('size_min', 0):,.0f} – ${m.get('size_max', 0):,.0f} revenue",
                 f"MODE     " + (ui.good("simulation — hands-free demo flow")
                                 if config.SIMULATION else
                                 ui.warn("LIVE — external actions need approval")),
                 ""]
        today = state.get("outreach_days", {}).get(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"), 0)
        lines.append(ui.dim("GUARDRAILS  ")
                     + f"outreach {today}/{config.CAPS['max_outreach_per_day']}/day"
                     + f"  ·  {config.CAPS['max_touches_per_lead']} touches max"
                     + f"  ·  dd floor {config.CAPS['min_diligence_score']:.0f}")
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
                    + clip(info["headline"], max(10, width - 34))
            else:
                stamp = ui.dim(spec["desc"])
            lines.append(pad(ui.accent(key.upper()), 16) + stamp)
        return lines

    # -------- chat view pieces (identical UX to v2)

    def _chat_lines(self, width):
        wrap = max(30, width - 8)
        lines = []
        for i, (role, text) in enumerate(self.transcript):
            is_last = i == len(self.transcript) - 1
            if role == "assistant" and is_last and self.reveal < len(text):
                text = text[:self.reveal]
            if role == "user":
                for j, seg in enumerate(textwrap.wrap(text, wrap) or [""]):
                    mark = ui.accent2("> ") if j == 0 else "  "
                    if ui.color_on():
                        seg = f"\033[{ui.SHARK_DIM_BG};{ui.WHITE}m {seg} \033[0m"
                    lines.append(mark + seg)
            elif role == "assistant":
                in_code, first = False, True
                for raw in text.splitlines() or [""]:
                    if raw.strip().startswith("```"):
                        in_code = not in_code
                        continue
                    if in_code:
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
            lines = ["", ui.dim("  this is your desk — ask about the pipeline, a deal,"),
                     ui.dim("  the funnel, or tell it to run diligence."), "",
                     ui.dim("  try: “what should I push today?”  ·  “run diligence on summit”")]
        return lines

    # -------- the chat bar + autocomplete (identical UX to v2)

    def suggestions(self):
        if not self.input.startswith("/"):
            return []
        parts = self.input.split()
        head = parts[0].lower() if parts else "/"
        past_first_word = len(parts) > 1 or self.input.endswith(" ")
        if head == "/deploy" and past_first_word:
            prefix = parts[1].lower() if len(parts) > 1 else ""
            return [(f"/deploy {name}", analysts.ROSTER[name]["desc"])
                    for name in analysts.ROSTER if name.startswith(prefix)]
        if head == "/diligence" and past_first_word:
            prefix = " ".join(parts[1:]).lower()
            state = load_state()
            return [(f"/diligence {d['id']}", f"{d['company']} — {d['stage']}, fit {d['score']:.0f}")
                    for d in pipeline.active_deals(state)
                    if prefix in d["id"] or prefix in d["company"].lower()][:6]
        if past_first_word:
            return []
        return [(c, d) for c, d in SLASH_COMMANDS.items() if c.startswith(head)]

    def _complete(self):
        matches = self.suggestions()
        if not matches:
            return
        cmd = matches[min(self.sugg_idx, len(matches) - 1)][0]
        self.input = cmd + (" " if cmd in ("/deploy", "/diligence") else "")
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
                "ask your bank anything…  /  for commands") + ui.cursor_block()
        rows.append(ui.dim("╭" + "─" * inner + "╮"))
        rows.append(ui.dim("│") + pad(" " + clip(content, inner - 2), inner) + ui.dim("│"))
        rows.append(ui.dim("╰" + "─" * inner + "╯"))
        matches = self.suggestions()
        if matches:
            self.sugg_idx = min(self.sugg_idx, len(matches) - 1)
            for i, (cmd, desc) in enumerate(matches[:6]):
                line = f"   {cmd:<22s} {desc}"
                if i == self.sugg_idx and ui.color_on():
                    line = f"\033[{ui.SHARK_BG};38;5;231m{pad(line, min(width, 84))}\033[0m"
                elif i == self.sugg_idx:
                    line = " ▸ " + line[3:]
                else:
                    line = ui.dim(line)
                rows.append(clip(line, width))
            rows.append(ui.dim("   ↑↓ choose · tab complete · ⏎ run"))
        else:
            back = {"chat": "hybrid", "hybrid": "dashboard", "dash": "desk"}[self.view]
            left = f"  ⏎ send   esc {back}   /  commands   ⌃c quit"
            right = f"{config.MODE.upper()} · airbank by finsider  "
            rows.append(clip(pad(ui.dim(left), width - vlen(right)) + ui.dim(right), width))
        return rows

    # -------- frame

    def build(self, width, height):
        self.frame += 1
        state = load_state()
        now = datetime.now(timezone.utc)

        firm = config.MANDATE.get("firm", "Airbank")
        title = {"chat": "DESK CHAT", "hybrid": "AIRBANK TERMINAL · DESK"}.get(
            self.view, "AIRBANK TERMINAL")
        left = f" {title}  {ui.accent('▮▮')}  {firm.upper()}"
        if state.get("halt"):
            left += "  " + ui.bad(ui.bold("⛔ HALTED"))
        right = f"SOURCING 24/7 · {config.MODE.upper()} · {now.strftime('%a %H:%M:%S UTC')} "
        bar = pad(left, width - vlen(right)) + right
        top = "\033[7m" + pad(clip(bar, width), width) + "\033[0m" if ui.color_on() \
            else pad(clip(bar, width), width)
        rows = [top, self._ticker(width)]

        bottom = self._chat_bar(width)
        body_h = height - len(rows) - len(bottom)

        if self.view == "chat":
            chat_lines = self._chat_lines(width)
            rows += panel("THE DESK — YOUR BANK, CONVERSATIONAL",
                          chat_lines[-(body_h - 2):], width, body_h)
        else:
            lw = max(46, int(width * 0.44))
            rw = width - lw - 1
            flow_h = len(sources.CONNECTORS) + 4
            rows += beside(panel("DEAL FLOW", self._flow(state), lw, flow_h),
                           panel("PIPELINE", self._pipeline_panel(state, rw), rw, flow_h),
                           gap=1)
            desk_h = len(analysts.ROSTER) + 2
            rows += beside(panel("CAMPAIGN · GUARDRAILS", self._campaign(state), lw, desk_h),
                           panel("DEAL TEAM", self._desk(state, rw), rw, desk_h),
                           gap=1)
            slot_h = max(4, height - len(rows) - len(bottom))
            if self.view == "hybrid":
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

    # -------- keys (identical UX to v2)

    def handle(self, key):
        if key == "\x03":
            return False
        if key in ("UP", "DOWN"):
            matches = self.suggestions()
            if matches:
                self.sugg_idx = (self.sugg_idx + (1 if key == "DOWN" else -1)) % min(len(matches), 6)
            return True
        if key == "\t":
            self._complete()
            return True
        if key == "\x1b":
            if self.input.startswith("/"):
                self.input = ""
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
        matches = self.suggestions()
        if matches and self.input.strip() != matches[min(self.sugg_idx, len(matches) - 1)][0]:
            self._complete()
            if self.input.endswith(" "):
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
        elif cmd == "deploy":
            if len(parts) > 1 and parts[1] in analysts.ROSTER:
                self._say("system", f"→ deploying {parts[1]} …")
                self.action_deploy(parts[1])
            else:
                self.status = ui.warn("deploy who? " + " ".join(analysts.ROSTER))
        elif cmd == "diligence":
            if len(parts) > 1:
                self.action_diligence(" ".join(parts[1:]))
            else:
                self.status = ui.warn("diligence on which deal? /diligence <id or company>")
        elif cmd in ("dash", "dashboard"):
            self._goto("dash")
        elif cmd == "chat":
            self._goto("chat")
        elif cmd == "hybrid":
            self._goto("hybrid")
        else:
            self.status = ui.warn(
                f"unknown command /{cmd} — /run /deploy /diligence /dash /hybrid /chat /quit")
        return True

    # -------- lifecycle (identical to v2)

    def _draw(self, rows):
        sys.stdout.write("\033[H" + "\r\n".join(row + "\033[K" for row in rows))
        sys.stdout.flush()

    def _animate(self, width, height):
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
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()


def run():
    if not sys.stdin.isatty():
        print("the airbank terminal needs a TTY — try `airbank status`")
        return
    Terminal().run()
