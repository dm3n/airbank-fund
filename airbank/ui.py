"""Terminal UI toolkit: the Airbank standard color profile, styled text,
arrow-key menus, sparklines. Pure stdlib. Contract assertions 29 and 34.

Profile: body text is the terminal's own white; the brand is shark blue —
dark Finsider blue (256c 25) for the cursor, accents, and the breathing
widget, a lighter shade (256c 75) for submitted text; links are blue and
underlined; code renders as Claude-style inline chips. Market data (tickers,
sparklines, P&L) stays multicolored."""
import os
import re
import sys

SHARK = "38;5;25"           # dark finsider blue — the brand
SHARK_LIGHT = "38;5;75"     # lighter shark — pasted/submitted text
SHARK_BG = "48;5;25"        # the block cursor
SHARK_DIM_BG = "48;5;17"    # slight highlight behind user messages
LINK = "4;38;5;33"          # links: blue + underline
CODE = "48;5;236;38;5;186"  # inline code chip: warm text on a dark slab
# one full breath, in and out — stepped through slowly, never flashing
BREATH_SHADES = ["24", "25", "26", "32", "38", "75", "111", "75", "38", "32", "26", "25"]


def color_on():
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _sgr(code, text):
    if not code or not color_on():
        return str(text)
    return f"\033[{code}m{text}\033[0m"


def bold(t):   return _sgr("1", t)
def dim(t):    return _sgr("2", t)
def accent(t): return _sgr(SHARK, t)
def accent2(t): return _sgr(SHARK_LIGHT, t)
def good(t):   return _sgr("32", t)
def bad(t):    return _sgr("31", t)
def warn(t):   return _sgr("33", t)
def link(t):   return _sgr(LINK, t)


def breath(t, step):
    """One shade of the breathing ramp — callers advance `step` slowly."""
    return _sgr(f"38;5;{BREATH_SHADES[step % len(BREATH_SHADES)]}", t)


def cursor_block():
    return _sgr(SHARK_BG, " ")


_CODE_RE = re.compile(r"`([^`\n]+)`")
_URL_RE = re.compile(r"(https?://[^\s)\"']+)")


def rich(t):
    """Inline styling for chat text: `code` chips and blue links."""
    if not color_on():
        return t
    t = _CODE_RE.sub(lambda m: f"\033[{CODE}m {m.group(1)} \033[0m", t)
    return _URL_RE.sub(lambda m: f"\033[{LINK}m{m.group(1)}\033[0m", t)


def money(v):
    return f"${v:,.2f}"


def pnl(v, pct=None):
    text = f"{v:+,.2f}" if pct is None else f"{v:+.2f}%"
    return good(text) if v >= 0 else bad(text)


BANNER = r"""
    _   ___ ___ ___   _   _  _ _  __
   /_\ |_ _| _ \ _ ) /_\ | \| | |/ /
  / _ \ | ||   / _ \/ _ \| .` | ' <
 /_/ \_\___|_|_\___/_/ \_\_|\_|_|\_\
"""

SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values, width=48):
    if len(values) < 2:
        return dim("·" * 8 + " collecting history")
    values = values[-width:]
    lo, hi = min(values), max(values)
    if hi == lo:
        return accent(SPARK[3] * len(values))
    return accent("".join(
        SPARK[int((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in values))


# --------------------------------------------------------------- key input

def _read_key():
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # escape sequence (arrows)
            ch += sys.stdin.read(2)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


UP, DOWN, ENTER = ("\x1b[A", "k"), ("\x1b[B", "j"), ("\r", "\n")


def select(title, options, descriptions=None, default=0, preview=None):
    """Arrow-key menu on a TTY; numbered prompt otherwise. Returns index.
    `preview(idx)` runs whenever the highlight moves — used for live theme
    previews (contract assertion 41)."""
    descriptions = descriptions or [""] * len(options)
    print(bold(title))
    if not sys.stdin.isatty():
        for i, (opt, desc) in enumerate(zip(options, descriptions), 1):
            print(f"  {i}. {opt}" + (dim(f" — {desc}") if desc else ""))
        while True:
            raw = input(f"choose [1-{len(options)}] (default {default + 1}): ").strip()
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1

    idx = default
    rendered = 0
    while True:
        if rendered:
            sys.stdout.write(f"\033[{rendered}A")
        if preview:
            preview(idx)  # cursor is at the menu's first line here
        rendered = 0
        for i, (opt, desc) in enumerate(zip(options, descriptions)):
            marker = accent(bold("  ▸ ")) if i == idx else "    "
            line = marker + (bold(opt) if i == idx else opt)
            if desc:
                line += dim(f"  {desc}")
            sys.stdout.write("\033[2K" + line + "\n")
            rendered += 1
        sys.stdout.write("\033[2K" + dim("    ↑/↓ move · enter select") + "\n")
        rendered += 1
        sys.stdout.flush()
        key = _read_key()
        if key in ("\x03", "\x04"):  # ctrl-c / ctrl-d
            print()
            raise KeyboardInterrupt
        if key in UP:
            idx = (idx - 1) % len(options)
        elif key in DOWN:
            idx = (idx + 1) % len(options)
        elif key in ENTER or key == "\r\n":
            return idx
        elif key.isdigit() and 1 <= int(key) <= len(options):
            return int(key) - 1


def text(prompt, default=None, secret=False, validate=None, prefix=""):
    """Prompt for a value; re-ask until validate(value) passes (if given).
    `prefix` sits right after the colon so the user types after it, e.g.
    `Starting cash: $` (contract assertion 41)."""
    import getpass
    suffix = dim(f" [{prefix}{default}]") if default is not None else ""
    while True:
        reader = getpass.getpass if secret else input
        raw = reader(f"{prompt}{suffix}: {accent(prefix)}").strip()
        value = raw or ("" if default is None else str(default))
        if validate is None:
            return value
        ok, why = validate(value)
        if ok:
            return value
        print(warn(f"  {why}"))


def clear():
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def hr(width=56):
    print(dim("─" * width))
