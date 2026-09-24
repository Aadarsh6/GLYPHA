# ui.py — presentation layer. peer.py emits facts; ui.py decides looks.

import threading
from datetime import datetime

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI

__version__ = "2.1.0"
MARK = "◇"
NAME_W = 9            # fixed name column — every message aligns here
TS_W = 5              # "17:25"
_CONT_PAD = " " * (TS_W + 2 + NAME_W + 2)   # continuation indent


class C:
    CYAN = "\x1b[96m"; GREEN = "\x1b[92m"; YELLOW = "\x1b[93m"
    RED = "\x1b[91m"; GRAY = "\x1b[90m"; BOLD = "\x1b[1m"; RESET = "\x1b[0m"


def ui(text):
    """One styled line. Atomic under patch_stdout()."""
    print_formatted_text(ANSI(text))


def status(msg):  ui(f"{C.GRAY}  • {msg}{C.RESET}")
def ok(msg):      ui(f"{C.GREEN}  ● {msg}{C.RESET}")
def warn(msg):    ui(f"{C.YELLOW}  ⚠ {msg}{C.RESET}")
def err(msg):     ui(f"{C.RED}  ✕ {msg}{C.RESET}")


def rule():
    ui(f"{C.GRAY}{'─' * 56}{C.RESET}")


def banner():
    """Centered brand header — the h1."""
    ui("")
    ui(f"{C.BOLD}{C.CYAN}{'◇ GLYPHA'.center(56)}{C.RESET}")
    ui(f"{C.GRAY}{f'encrypted p2p chat · v{__version__}'.center(56)}{C.RESET}")
    ui(f"{C.GRAY}{'─' * 44}".center(0) + f"{C.RESET}")
    ui("")


def _center(text):
    return text.center(56)


def event(text):
    """System events: one dim line, never competing with messages."""
    ui(f"{C.GRAY}{datetime.now():%H:%M}  •  {text}{C.RESET}")


def message(who, text, color):
    """One message = one line: HH:MM  NAME  text. Neutral body, colored
    name, continuation lines indent under the text column."""
    label = who[:NAME_W].ljust(NAME_W)
    ts = f"{C.GRAY}{datetime.now():%H:%M}{C.RESET}"
    lines = str(text).splitlines() or [""]
    ui(f" {ts}  {color}{label}{C.RESET}  {lines[0]}")
    for extra in lines[1:]:
        ui(_CONT_PAD + extra)


def fingerprint_block(fp, label="fingerprint"):
    groups = fp.split(":")
    ui(f"  {C.GRAY}{label}{C.RESET}")
    for i in range(0, 16, 8):
        ui(f"  {C.CYAN}{' '.join(groups[i:i+8])}{C.RESET}")


def identity_screen(own_fp):
    rule()
    ui(f"{C.BOLD}{C.CYAN}{'◇ GLYPHA — welcome'.center(56)}{C.RESET}")
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