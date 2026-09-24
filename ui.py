# ui.py — Glypha's presentation layer. peer.py emits facts; ui.py decides
# how they look. No networking code ever touches a color code.

import threading
from datetime import datetime

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI

__version__ = "2.1.0"
MARK = "◇"


class C:
    CYAN = "\x1b[96m"; GREEN = "\x1b[92m"; YELLOW = "\x1b[93m"
    RED = "\x1b[91m"; GRAY = "\x1b[90m"; BOLD = "\x1b[1m"; RESET = "\x1b[0m"


def ui(text):
    """One styled line. Atomic under patch_stdout() — incoming messages
    repaint the input line instead of interrupting your typing."""
    print_formatted_text(ANSI(text))


def status(msg):  ui(f"  {C.GRAY}• {msg}{C.RESET}")
def ok(msg):      ui(f"  {C.GREEN}✔ {msg}{C.RESET}")
def warn(msg):    ui(f"  {C.YELLOW}⚠ {msg}{C.RESET}")
def err(msg):     ui(f"  {C.RED}✕ {msg}{C.RESET}")


def rule():
    ui(f"{C.GRAY}{'─' * 56}{C.RESET}")


def banner():
    ui(f"{C.BOLD}{MARK} GLYPHA{C.RESET} {C.GRAY}· v{__version__} · encrypted p2p chat{C.RESET}")


def fingerprint_block(fp, label="fingerprint"):
    groups = fp.split(":")
    ui(f"  {C.GRAY}{label}{C.RESET}")
    for i in range(0, 16, 8):
        ui(f"  {C.CYAN}{' '.join(groups[i:i+8])}{C.RESET}")


def identity_screen(own_fp):
    """First-run welcome: shown once, when the identity key is born."""
    rule()
    ui(f"{C.BOLD}{MARK} GLYPHA{C.RESET} {C.GRAY}— welcome{C.RESET}")
    ui("")
    ui("  Your identity has been created and stored locally.")
    rule()
    fingerprint_block(own_fp)
    rule()
    ui(f"  {C.GRAY}Share this fingerprint with your peers out-of-band —{C.RESET}")
    ui(f"  {C.GRAY}it is how they know 'you' is really you.{C.RESET}")
    ui("")


def connection_panel(meta):
    """The /status panel — Glypha's technical transparency moment.
    Every field here is true data from the live connection."""
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
    ui(f"  {C.GRAY}name{C.RESET}       {name}")
    ui(f"  {C.GRAY}identity key{C.RESET}  persistent (~/.glypha)")
    ui(f"  {C.GRAY}storage{C.RESET}     encrypted")
    ui("")
    fingerprint_block(own_fp)
    rule()


HELP_LINES = [
    ("/status       connection details — transport, endpoint, identity"),
    ("/whoami       your identity and fingerprint"),
    ("/fingerprint  peer's fingerprint"),
    ("/clear        clear the screen"),
    ("/quit         leave the chat"),
]


def help_panel():
    rule()
    ui(f"  {C.BOLD}commands{C.RESET}")
    for line in HELP_LINES:
        ui(f"  {C.CYAN}{line}{C.RESET}")
    rule()


class MessageStream:
    """Grouped chat rendering: a dim timestamp + sender header only when
    the sender changes; consecutive messages indent underneath. Shared by
    the send loop and the receive thread — hence the lock."""

    def __init__(self):
        self._owner = None
        self._lock = threading.Lock()

    def _break(self):
        if self._owner is not None:
            ui("")

    def message(self, who, text, color):
        with self._lock:
            self._break()
            if who != self._owner:
                ui(f"{C.GRAY}{datetime.now():%H:%M}{C.RESET}  "
                   f"{C.BOLD}{color}{who}{C.RESET}")
                self._owner = who
            ui(f"       {text}")

    def event(self, text):
        with self._lock:
            self._break()
            self._owner = None   # events end a block
            ui(f"{C.GRAY}{datetime.now():%H:%M}  •  {text}{C.RESET}")