# ui.py — presentation layer. peer.py emits facts; ui.py decides looks.

import shutil
import textwrap
from datetime import datetime

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI

__version__ = "2.1.0"
MARK = "◇"
NAME_W = 9        # fixed peer-name column
TS_W = 5          # "20:38"
PROMPT_VISIBLE = 2  # "❯ "


class C:
    CYAN = "\x1b[96m"; GREEN = "\x1b[92m"; YELLOW = "\x1b[93m"
    RED = "\x1b[91m"; GRAY = "\x1b[90m"; BOLD = "\x1b[1m"; RESET = "\x1b[0m"
    BLUE = "\x1b[94m"


def _w():
    return max(shutil.get_terminal_size().columns, 40)


def ui(text):
    """One styled line. Atomic under patch_stdout()."""
    print_formatted_text(ANSI(text))


def status(msg):  ui(f"  {C.BLUE}• {msg}{C.RESET}")
def ok(msg):      ui(f"  {C.GREEN}● {msg}{C.RESET}")
def warn(msg):    ui(f"  {C.YELLOW}⚠ {msg}{C.RESET}")
def err(msg):     ui(f"  {C.RED}✕ {msg}{C.RESET}")


def rule():
    ui(f"{C.GRAY}{'─' * min(_w(), 56)}{C.RESET}")


def banner():
    """The h1: spaced letters, bright cyan, heavy rules."""
    W = _w()
    ui("")
    ui(f"{C.BOLD}{C.CYAN}{'◇  G  L  Y  P  H  A'.center(W)}{C.RESET}")
    ui(f"{C.GRAY}{f'encrypted p2p chat · v{__version__}'.center(W)}{C.RESET}")
    ui(f"{C.GRAY}{'━' * min(W - 4, 56).center(W - 4)}{C.RESET}")
    ui("")


def event(text):
    ui(f"{C.GRAY}{datetime.now():%H:%M}  •  {text}{C.RESET}")


def message(who, text, mine):
    """One message, chat-app style:
    peer  → left,  dim leading time, green name column, neutral body
    own   → right, cyan body, dim trailing time
    Long text wraps; alignment holds on every wrapped line."""
    ts = f"{C.GRAY}{datetime.now():%H:%M}{C.RESET}"
    W = _w()
    lines = str(text).splitlines() or [""]
    if mine:
        avail = max(W - TS_W - 6, 20)
        wrapped = textwrap.wrap("\n".join(lines), avail) or [""]
        for i, ln in enumerate(wrapped):
            pad = " " * (avail - len(ln))
            if i == len(wrapped) - 1:
                ui(f"{pad}{C.CYAN}{ln}{C.RESET}  {ts}")
            else:
                ui(f"{pad}{C.CYAN}{ln}{C.RESET}")
    else:
        avail = max(W - TS_W - NAME_W - 8, 20)
        wrapped = textwrap.wrap("\n".join(lines), avail) or [""]
        ts_lead = f" {ts}  "
        cont = " " * (TS_W + 2 + NAME_W + 2)
        first = True
        for ln in wrapped:
            if first:
                ui(f"{ts_lead}{C.GREEN}{who[:NAME_W].ljust(NAME_W)}{C.RESET}  {ln}")
                first = False
            else:
                ui(cont + ln)


def erase_echo(msg):
    """Erase the raw prompt+input line prompt_toolkit leaves behind on
    Enter, so the formatted copy is the only one on screen. Empty Enter
    becomes fully silent."""
    used = max(1, -(-(PROMPT_VISIBLE + len(str(msg))) // _w()))  # ceil
    ui("\x1b[2K" + "\x1b[1A\x1b[2K" * used)


def fingerprint_block(fp, label="fingerprint"):
    groups = fp.split(":")
    ui(f"  {C.GRAY}{label}{C.RESET}")
    for i in range(0, 16, 8):
        ui(f"  {C.CYAN}{' '.join(groups[i:i+8])}{C.RESET}")


def identity_screen(own_fp):
    rule()
    ui(f"{C.BOLD}{C.CYAN}{'◇ GLYPHA — welcome'.center(_w())}{C.RESET}")
    ui("")
    ui("  Your identity has been created and stored locally.")
    rule()
    fingerprint_block(own_fp)
    rule()
    ui(f"  {C.GRAY}Share this fingerprint out-of-band — it is how{C.RESET}")
    ui(f"  {C.GRAY}your peers know 'you' is really you.{C.RESET}")
    ui("")


def connection_panel(meta):
    rule()
    ui(f"  {C.BOLD}{MARK} GLYPHA · CONNECTION{C.RESET}")
    rule()
    rows = [
        ("peer",       meta.get("peer", "—")),
        ("transport",  meta.get("via", "—")),
        ("endpoint",   meta.get("endpoint", "—")),
        ("identity",   "verified · pinned" if meta.get("pinned") else "verified this session"),
        ("encryption", "Box — X25519 + XSalsa20-Poly1305"),
        ("history",    "encrypted (SecretBox)"),
    ]
    for k, v in rows:
        ui(f"  {C.GRAY}{k:<11}{C.RESET}{v}")
    ui("")
    fingerprint_block(meta.get("fingerprint", ""))
    rule()
    ui(f"  {C.GREEN}● secure peer connection{C.RESET}")


def whoami_panel(name, own_fp):
    rule()
    ui(f"  {C.BOLD}{MARK} GLYPHA · IDENTITY{C.RESET}")
    rule()
    ui(f"  {C.GRAY}name{C.RESET}          {name}")
    ui(f"  {C.GRAY}identity key{C.RESET}  persistent (~/.glypha)")
    ui(f"  {C.GRAY}storage{C.RESET}       encrypted")
    ui("")
    fingerprint_block(own_fp)
    rule()


HELP_LINES = [
    "/status       connection details — transport, endpoint, identity",
    "/whoami       your identity and fingerprint",
    "/fingerprint  peer's fingerprint",
    "/clear        clear the screen",
    "/quit         leave the chat",
]


def help_panel():
    rule()
    ui(f"  {C.BOLD}commands{C.RESET}")
    for line in HELP_LINES:
        ui(f"  {C.CYAN}{line}{C.RESET}")
    rule()