"""Terminal UI toolkit: themes, styled text, arrow-key menus, sparklines.
Pure stdlib. Contract assertions 29 and 34."""
import os
import sys

THEMES = {
    "midnight":  {"accent": "36", "accent2": "94", "good": "32", "bad": "31",
                  "warn": "33", "label": "Midnight — cool cyan, the default"},
    "terminal":  {"accent": "33", "accent2": "38;5;208", "good": "32", "bad": "31",
                  "warn": "33", "label": "Terminal — amber like a trading floor"},
    "matrix":    {"accent": "32", "accent2": "38;5;46", "good": "38;5;46", "bad": "31",
                  "warn": "33", "label": "Matrix — all green everything"},
    "mono":      {"accent": "", "accent2": "", "good": "", "bad": "",
                  "warn": "", "label": "Mono — no color at all"},
}

_theme_name = "midnight"


def set_theme(name):
    global _theme_name
    if name in THEMES:
        _theme_name = name


def theme():
    return THEMES[_theme_name]


def color_on():
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None \
        and _theme_name != "mono"


def _sgr(code, text):
    if not code or not color_on():
        return str(text)
    return f"\033[{code}m{text}\033[0m"


def bold(t):   return _sgr("1", t)
def dim(t):    return _sgr("2", t)
def accent(t): return _sgr(theme()["accent"], t)
def accent2(t): return _sgr(theme()["accent2"], t)
def good(t):   return _sgr(theme()["good"], t)
def bad(t):    return _sgr(theme()["bad"], t)
def warn(t):   return _sgr(theme()["warn"], t)


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


def select(title, options, descriptions=None, default=0):
    """Arrow-key menu on a TTY; numbered prompt otherwise. Returns index."""
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


def text(prompt, default=None, secret=False, validate=None):
    """Prompt for a value; re-ask until validate(value) passes (if given)."""
    import getpass
    suffix = dim(f" [{default}]") if default is not None else ""
    while True:
        reader = getpass.getpass if secret else input
        raw = reader(f"{prompt}{suffix}: ").strip()
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
