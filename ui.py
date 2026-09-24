# ui.py — presentation layer. peer.py emits facts; ui.py decides looks.

import shutil
import textwrap
from datetime import datetime

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI

__version__ = "2.2.0"
MARK = "◇"
NAME_W = 12         # peer-name display cap
TS_W = 5            # "20:38"
PROMPT_VISIBLE = 2  # "❯ "


class C:
    CYAN = "\x1b[96m"; GREEN = "\x1b[92m"; YELLOW = "\x1b[93m"
    RED = "\x1b[91m"; GRAY = "\x1b[90m"; BOLD = "\x1b[1m"; RESET = "\x1b[0m"
    BLUE = "\x1b[94m"; WHITE = "\x1b[97m"; DIM = "\x1b[2m"


# ─────────────────────── accent (used sparingly, on info panels only) ───
ACCENT = (56, 189, 248)      # sky-400 — panel headers, fingerprints

BUBBLE_MAX_TEXT = 48   # a bubble never grows wider than this, however wide the terminal
SIDE_MARGIN = 2


def _rgb(r, g, b, bg=False):
    return f"\x1b[{'48' if bg else '38'};2;{r};{g};{b}m"


# ─────────────────────── wordmark (banner) ───────────────────────
# Hand-built 5x5 block glyphs — no rendering deps, no fonts to load.
GLYPH_FONT = {
    "G": [" ███ ", "█    ", "█  ██", "█   █", " ███ "],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "Y": ["█   █", "█   █", " █ █ ", "  █  ", "  █  "],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
}


def _scale_rows(rows, sx, sy):
    """Repeat each cell sx times horizontally and each row sy times
    vertically — lets one small hand-built glyph serve any banner size."""
    scaled = []
    for row in rows:
        wide = "".join(ch * sx for ch in row)
        for _ in range(sy):
            scaled.append(wide)
    return scaled


def _wordmark(word, gap=2, sx=2, sy=1):
    letters = [_scale_rows(GLYPH_FONT[ch], sx, sy) for ch in word if ch in GLYPH_FONT]
    if not letters:
        return [word]
    height = len(letters[0])
    gap_str = " " * gap
    return [gap_str.join(letter[row] for letter in letters) for row in range(height)]


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
    W = _w()
    # full double-width glyphs need ~70 cols; narrower terminals fall back
    # to single-width rather than wrapping mid-letter.
    sx = 2 if W >= 74 else 1
    lines = _wordmark("GLYPHA", gap=2, sx=sx, sy=1)
    block_w = len(lines[0])
    pad = " " * max((W - block_w) // 2, 0)
    subtitle = "ENCRYPTED · PEER-TO-PEER · LOCAL-FIRST"

    ui("")
    for line in lines:
        ui(pad + f"{C.BOLD}{line}{C.RESET}")
    ui("")
    ui(f"{C.GRAY}{subtitle.center(W)}{C.RESET}")
    ui(f"{C.GRAY}{f'v{__version__} · sockets up · keys local · relay when NATs say no'.center(W)}{C.RESET}")
    ui(f"{C.GRAY}{'─' * min(W, 60)}{C.RESET}")
    ui("")


def event(text):
    ui(f"{C.GRAY}{datetime.now():%H:%M}  •  {text}{C.RESET}")


def _outline_bubble(lines, strong=False):
    """Plain outlined chat bubble — no fill, no text color. `strong` (used
    for your own messages) makes the border bold instead of dim, so sent
    and received read apart by weight rather than by color.
    Returns (rendered_rows, total_visible_width)."""
    b = C.BOLD if strong else C.GRAY
    content_w = max(len(l) for l in lines)
    top = f"{b}╭{'─' * (content_w + 2)}╮{C.RESET}"
    bot = f"{b}╰{'─' * (content_w + 2)}╯{C.RESET}"
    body = [f"{b}│{C.RESET} {l.ljust(content_w)} {b}│{C.RESET}" for l in lines]
    return [top] + body + [bot], content_w + 4


def message(who, text, mine):
    """One message, plain outlined bubble:
    received → left,  thin gray border, name above
    sent     → right, bold border, 'You' above
    Timestamp sits on its own line below the bubble, dim and tucked into
    the same corner as the bubble it belongs to.
    Bubbles hug their content (they don't stretch to the terminal edge)
    and wrap cleanly at any width."""
    ts = f"{C.GRAY}{C.DIM}{datetime.now():%H:%M}{C.RESET}"
    ts_len = 5  # "20:38"
    W = _w()
    max_text = min(BUBBLE_MAX_TEXT, max(W - SIDE_MARGIN * 2 - 4, 16))

    wrapped = []
    for line in (str(text).splitlines() or [""]):
        wrapped.extend(textwrap.wrap(line, max_text) or [""])
    wrapped = wrapped or [""]

    rows, bw = _outline_bubble(wrapped, strong=mine)

    if mine:
        header = f"{C.BOLD}You{C.RESET}"
        ui(" " * max(W - 3 - SIDE_MARGIN, 0) + header)
        for row in rows:
            ui(" " * max(W - bw - SIDE_MARGIN, 0) + row)
        ui(" " * max(W - ts_len - SIDE_MARGIN, 0) + ts)
    else:
        name = who[:NAME_W]
        header = f"{C.BOLD}{name}{C.RESET}"
        ui(" " * SIDE_MARGIN + header)
        for row in rows:
            ui(" " * SIDE_MARGIN + row)
        ui(" " * SIDE_MARGIN + ts)
    ui("")


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
        ui(f"  {_rgb(*ACCENT)}{' '.join(groups[i:i + 8])}{C.RESET}")


def identity_screen(own_fp):
    rule()
    ui(f"{C.BOLD}{_rgb(*ACCENT)}{'◇ GLYPHA — welcome'.center(_w())}{C.RESET}")
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
    ui(f"  {C.BOLD}{_rgb(*ACCENT)}{MARK} GLYPHA · CONNECTION{C.RESET}")
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
    ui(f"  {C.BOLD}{_rgb(*ACCENT)}{MARK} GLYPHA · IDENTITY{C.RESET}")
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
    ui(f"  {C.BOLD}{_rgb(*ACCENT)}commands{C.RESET}")
    for line in HELP_LINES:
        ui(f"  {C.CYAN}{line}{C.RESET}")
    rule()