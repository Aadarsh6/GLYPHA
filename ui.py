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
    BLUE = "\x1b[94m"; WHITE = "\x1b[97m"


# ─────────────────────── brand palette (truecolor) ───────────────────────
# One palette, reused everywhere, so the whole app reads as one product.
BRAND_A = (88, 28, 135)      # deep violet — banner gradient start
BRAND_B = (6, 182, 212)      # cyan        — banner gradient end
ACCENT = (56, 189, 248)      # sky-400     — headers, "You" label, fingerprints

RECV_BG = (51, 58, 74)       # slate — their message bubble fill
RECV_FG = (226, 232, 240)
RECV_BORDER = (110, 124, 148)

SENT_BG = (11, 105, 134)     # teal — your message bubble fill
SENT_FG = (240, 253, 250)
SENT_BORDER = (56, 189, 211)

BUBBLE_MAX_TEXT = 48   # a bubble never grows wider than this, however wide the terminal
SIDE_MARGIN = 2


def _rgb(r, g, b, bg=False):
    return f"\x1b[{'48' if bg else '38'};2;{r};{g};{b}m"


def _lerp(a, b, t):
    return int(a + (b - a) * t)


def _gradient_text_line(width, text, c1, c2, fg="\x1b[97m\x1b[1m"):
    """A full-width bar, background interpolated c1 -> c2, `text` centered on top."""
    pad = max(width - len(text), 0)
    left = pad // 2
    chars = []
    for i in range(width):
        t = i / max(width - 1, 1)
        bg = _rgb(_lerp(c1[0], c2[0], t), _lerp(c1[1], c2[1], t), _lerp(c1[2], c2[2], t), bg=True)
        if left <= i < left + len(text):
            chars.append(f"{bg}{fg}{text[i - left]}")
        else:
            chars.append(f"{bg} ")
    return "".join(chars) + C.RESET


def _filled_bubble(lines, bg_rgb, fg_rgb, border_rgb):
    """Rounded, solid-fill chat bubble around pre-wrapped `lines`.
    Returns (rendered_rows, total_visible_width)."""
    bg = _rgb(*bg_rgb, bg=True)
    fg = _rgb(*fg_rgb)
    bfg = _rgb(*border_rgb)
    content_w = max(len(l) for l in lines)
    top = f"{bg}{bfg}╭{'─' * (content_w + 2)}╮{C.RESET}"
    bot = f"{bg}{bfg}╰{'─' * (content_w + 2)}╯{C.RESET}"
    body = [f"{bg}{bfg}│ {fg}{l.ljust(content_w)}{bfg} │{C.RESET}" for l in lines]
    return [top] + body + [bot], content_w + 4


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
    bar_w = min(W, 64)
    left_pad = " " * ((W - bar_w) // 2)

    title = "◆   G L Y P H A   ◆"
    subtitle = "ENCRYPTED · PEER-TO-PEER · LOCAL-FIRST"

    ui("")
    ui(left_pad + _gradient_text_line(bar_w, title, BRAND_A, BRAND_B))
    ui(left_pad + _gradient_text_line(bar_w, subtitle, BRAND_A, BRAND_B, fg="\x1b[97m"))
    ui(f"{C.GRAY}{f'v{__version__} · sockets up · keys local · relay when NATs say no'.center(W)}{C.RESET}")
    ui(f"{C.GRAY}{'─' * min(W, 60)}{C.RESET}")
    ui("")


def event(text):
    ui(f"{C.GRAY}{datetime.now():%H:%M}  •  {text}{C.RESET}")


def message(who, text, mine):
    """One message, real chat-bubble style:
    received → left,  slate filled bubble, name + time above
    sent     → right, teal  filled bubble, time + 'You' above
    Bubbles hug their content (they don't stretch to the terminal edge)
    and wrap cleanly at any width."""
    ts = f"{datetime.now():%H:%M}"
    W = _w()
    max_text = min(BUBBLE_MAX_TEXT, max(W - SIDE_MARGIN * 2 - 4, 16))

    wrapped = []
    for line in (str(text).splitlines() or [""]):
        wrapped.extend(textwrap.wrap(line, max_text) or [""])
    wrapped = wrapped or [""]

    if mine:
        rows, bw = _filled_bubble(wrapped, SENT_BG, SENT_FG, SENT_BORDER)
        header = f"{C.GRAY}{ts}{C.RESET}  {C.BOLD}{_rgb(*ACCENT)}You{C.RESET}"
        header_len = len(ts) + 2 + 3
        ui(" " * max(W - header_len - SIDE_MARGIN, 0) + header)
        for row in rows:
            ui(" " * max(W - bw - SIDE_MARGIN, 0) + row)
    else:
        rows, bw = _filled_bubble(wrapped, RECV_BG, RECV_FG, RECV_BORDER)
        name = who[:NAME_W]
        header = f"{C.BOLD}{C.GREEN}{name}{C.RESET}  {C.GRAY}{ts}{C.RESET}"
        ui(" " * SIDE_MARGIN + header)
        for row in rows:
            ui(" " * SIDE_MARGIN + row)
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