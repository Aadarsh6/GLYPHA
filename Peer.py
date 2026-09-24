# peer.py — unified P2P encrypted chat peer.
# Modes: listen | connect | find | punch | relay | chat
# "chat" is the product command: direct -> punch -> relay, automatic.

import socket
import threading
import sys
import hashlib
import json
import base64
import time
import os
import shutil


import identity
from identity import load_or_create_key, load_or_create_secret_key
from nacl.public import PrivateKey, PublicKey, Box
from nacl.secret import SecretBox
from protocol import recv_message, send_message
from storage import init_db, save_message, load_messages
import ui
from ui import C

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import ANSI

RENDEZVOUS_PORT = 7000
RENDEZVOUS_REFRESH = 30   # server TTL is 90s; refresh at 1/3 of TTL
RELAY_PORT = 7001

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".glypha")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
KNOWN_PEERS_FILE = os.path.join(CONFIG_DIR, "known_peers.json")



def _known_peers_path():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    old = os.path.join(os.getcwd(), "known_peers.json")
    if not os.path.exists(KNOWN_PEERS_FILE) and os.path.exists(old):
        shutil.copy2(old, KNOWN_PEERS_FILE)
    return KNOWN_PEERS_FILE

# ───────────────────────── helpers ─────────────────────────

def get_lan_ip():
    """Our LAN IP via a UDP route lookup — no packet is ever sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def format_fingerprint(public_bytes):
    """SHA-256 of a public key, grouped 4-hex-chunks — the string two
    humans compare out-of-band to authenticate each other."""
    fp = hashlib.sha256(public_bytes).hexdigest()
    return ":".join(fp[i:i + 4] for i in range(0, len(fp), 4))


def punch_port_for(name):
    """Deterministic per-name port (20000-39999): two peers on one machine
    never collide, and an identity always punches from the same port."""
    h = int(hashlib.sha256(name.encode()).hexdigest(), 16)
    return 20000 + (h % 20000)


def load_default_rv():
    """The configured rendezvous host, if `glypha config` was ever run."""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f).get("rendezvous_host")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def handshake(sock, private_key, initiator):
    """Exchange public keys and build the Box.
    initiator=True  -> send ours first, then receive theirs
    initiator=False -> receive theirs first, then send ours
    Returns (box, peer_fingerprint) or (None, None) on disconnect."""
    own_public = bytes(private_key.public_key)
    if initiator:
        send_message(sock, own_public)
        peer_bytes = recv_message(sock)
    else:
        peer_bytes = recv_message(sock)
        send_message(sock, own_public)
    if peer_bytes is None:
        return None, None
    box = Box(private_key, PublicKey(peer_bytes))
    return box, format_fingerprint(peer_bytes)


def verify_fingerprints(own_fp, peer_fp):
    """Manual out-of-band verification (TOFU): the human compares the
    two fingerprints (e.g. on a phone call) and confirms."""
    ui.rule()
    ui.ui(f"  {C.BOLD}Verify identities{C.RESET} {C.GRAY}(first contact){C.RESET}")
    ui.ui(f"  {C.GRAY}yours:{C.RESET} {C.CYAN}{own_fp}{C.RESET}")
    ui.ui(f"  {C.GRAY}peer: {C.RESET} {C.GREEN}{peer_fp}{C.RESET}")
    ui.ui(f"  {C.GRAY}compare out-of-band — call, text — then confirm.{C.RESET}")
    answer = input("  match? (yes/no): ")
    return answer.lower() in ("yes", "y")


def load_known_peers():
    try:
        with open(_known_peers_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def pin_peer(name, fingerprint):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    known = load_known_peers()
    known[name] = fingerprint
    with open(_known_peers_path(), "w") as f:
        json.dump(known, f, indent=2)


def verify_or_reject(own_fp, peer_fp, expected_fp, peer_name=None):
    """Trust model (SSH known_hosts style): pinned → auto-verify against
    the PIN, key change = loud reject; first contact → human verifies,
    then we pin. expected_fp (registry) is a cross-check, never a trust
    source."""
    clean = peer_fp.replace(":", "")
    pinned = load_known_peers().get(peer_name) if peer_name else None

    if pinned:
        if clean == pinned.replace(":", ""):
            ui.ok(f"'{peer_name}' verified against pinned key")
            return True
        ui.err(f"KEY CHANGE for '{peer_name}'!")
        ui.ui(f"  {C.GRAY}pinned: {pinned}{C.RESET}")
        ui.ui(f"  {C.GRAY}got:    {peer_fp}{C.RESET}")
        ui.err("possible impersonation — rejecting.")
        return False

    if verify_fingerprints(own_fp, peer_fp):
        if peer_name:
            pin_peer(peer_name, clean)
            ui.ok(f"pinned '{peer_name}' for future auto-verification")
        return True
    return False


# ───────────────────────── chat (shared by every mode) ─────────────────────────
def chat(sock, box, peer_fp, name, peer_display=None, meta=None):
    """Conversation loop: chat-app alignment (peer left, you right),
    dim trailing times, atomic rendering, slash commands, E2E send loop."""
    peer_label = peer_display or "Them"
    storage_key = load_or_create_secret_key(f"{name}_storage_key.bin")
    secret_box = SecretBox(storage_key)
    db_filename = f"{name}_history.db"

    init_db(db_filename)

    session = PromptSession(ANSI(f"{C.GRAY}❯{C.RESET} "), erase_when_done=True)
    connected = True

    def handle_command(cmd):
        if cmd in ("/quit", "/exit"):
            return "quit"
        if cmd == "/help":
            ui.help_panel()
        elif cmd == "/status":
            m = dict(meta or {})
            m["peer"] = peer_display or m.get("peer", "—")
            m["fingerprint"] = peer_fp
            ui.connection_panel(m)
        elif cmd == "/fingerprint":
            ui.fingerprint_block(peer_fp, "peer fingerprint")
        elif cmd == "/whoami":
            own = load_or_create_key(f"{name}_key.bin")
            ui.whoami_panel(name, format_fingerprint(bytes(own.public_key)))
        elif cmd == "/clear":
            ui.ui("\x1b[2J\x1b[H")
            ui.banner()
        else:
            ui.err(f"unknown command {cmd} — /help")
        return None

    with patch_stdout():
        history = load_messages(db_filename, peer_fp, secret_box)
        if history:
            ui.rule()
            ui.status(f"{len(history)} earlier messages")
            for direction, text, _ts in history:
                ui.message("You" if direction == "sent" else peer_label,
                           text, direction == "sent")
            ui.rule()

        ui.ok("connected · encrypted" +
              (" · verified" if meta and meta.get("verified") else ""))
        ui.status("type a message · /help for commands")

        def receive_loop():
            nonlocal connected
            while True:
                data = recv_message(sock)
                if data is None:
                    if connected:
                        ui.event(f"{peer_label} disconnected")
                    connected = False
                    break
                try:
                    message = box.decrypt(data).decode()
                except Exception:
                    ui.event("received an undecryptable frame — ignored")
                    continue
                save_message(db_filename, peer_fp, "received", message, secret_box)
                ui.message(peer_label, message, mine=False)

        receiver = threading.Thread(target=receive_loop, daemon=True)
        receiver.start()

        while connected:
            try:
                message = session.prompt()
            except (KeyboardInterrupt, EOFError):
                break
            if not connected:
                ui.warn("peer is gone")
                break
            if message in ("quit", "/quit"):
                break
            if not message.strip():
                continue
            if message.startswith("/"):
                action = handle_command(message.strip())
                if action == "quit":
                    break
                continue
            try:
                send_message(sock, box.encrypt(message.encode()))
            except OSError:
                ui.warn("peer is gone")
                break
            save_message(db_filename, peer_fp, "sent", message, secret_box)
            ui.message("You", message, mine=True)

    connected = False
    sock.close()
    receiver.join(timeout=2)



# ───────────────────────── rendezvous client ─────────────────────────

def rendezvous_register(private_key, name, listen_port, rv_host):
    """Publish name + listen port + public key. Bound to our listen port
    so the server observes the NAT mapping for it. Response dict or None."""
    request = {
        "type": "register",
        "id": name,
        "listen_port": listen_port,
        "public_key": base64.b64encode(bytes(private_key.public_key)).decode(),
    }
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", listen_port))
        sock.settimeout(5)
        sock.connect((rv_host, RENDEZVOUS_PORT))
        send_message(sock, json.dumps(request).encode())
        resp = recv_message(sock)
        sock.close()
        return json.loads(resp.decode()) if resp else None
    except OSError:
        return None


def rendezvous_lookup(peer_id, rv_host):
    """Ask the rendezvous where a peer is. Response dict or None."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((rv_host, RENDEZVOUS_PORT))
        send_message(sock, json.dumps({"type": "lookup", "id": peer_id}).encode())
        resp = recv_message(sock)
        sock.close()
        return json.loads(resp.decode()) if resp else None
    except OSError:
        return None


def rendezvous_refresh_loop(private_key, name, listen_port, rv_host):
    """Re-register every 30s so the 90s TTL never expires while we run."""
    while True:
        time.sleep(RENDEZVOUS_REFRESH)
        if rendezvous_register(private_key, name, listen_port, rv_host) is None:
            ui.status("rendezvous refresh failed — will retry")


# ───────────────────────── explicit modes (internals) ─────────────────────────

def listen_mode(port, name, rv_host=None):
    """Wait for one inbound peer, then chat."""
    private_key = load_or_create_key(f"{name}_key.bin")
    own_fp = format_fingerprint(bytes(private_key.public_key))

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", port))
    server.listen()

    ui.banner()
    ui.ui(f"  {C.GRAY}listening on 0.0.0.0:{port}{C.RESET}")
    ui.fingerprint_block(own_fp, "your fingerprint")
    ui.ui(f"  {C.GRAY}other peer connects with: glypha connect {get_lan_ip()} {port} <their-name>{C.RESET}")

    if rv_host:
        resp = rendezvous_register(private_key, name, port, rv_host)
        if resp:
            ui.ok(f"registered — discoverable as '{name}'")
        else:
            ui.warn("registration failed (continuing without discovery)")
        threading.Thread(
            target=rendezvous_refresh_loop,
            args=(private_key, name, port, rv_host),
            daemon=True,
        ).start()

    sock, address = server.accept()
    server.close()   # one session per run; multi-session accept loop is future work
    ui.status(f"peer connected: {address}")

    box, peer_fp = handshake(sock, private_key, initiator=False)
    if box is None:
        ui.warn("peer disconnected during handshake.")
        return
    if not verify_fingerprints(own_fp, peer_fp):
        ui.err("fingerprint not verified — closing.")
        sock.close()
        return
    chat(sock, box, peer_fp, name)


def connect_mode(host, port, name):
    """Dial a known host:port directly, then chat."""
    private_key = load_or_create_key(f"{name}_key.bin")
    own_fp = format_fingerprint(bytes(private_key.public_key))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    ui.status(f"connected to {host}:{port}")

    box, peer_fp = handshake(sock, private_key, initiator=True)
    if box is None:
        ui.warn("peer disconnected during handshake.")
        return
    if not verify_fingerprints(own_fp, peer_fp):
        ui.err("fingerprint not verified — closing.")
        sock.close()
        return
    chat(sock, box, peer_fp, name)


def find_mode(peer_id, rv_host):
    """One-shot discovery: print where a peer is + its fingerprint."""
    result = rendezvous_lookup(peer_id, rv_host)
    if result is None:
        ui.err(f"rendezvous server at {rv_host}:{RENDEZVOUS_PORT} unreachable")
        return
    if result.get("status") == "found":
        ui.ui(f"  {C.GRAY}peer{C.RESET}       {peer_id}")
        ui.ui(f"  {C.GRAY}endpoint{C.RESET}   {result['ip']}:{result['port']}")
        ui.fingerprint_block(result["fingerprint"], "fingerprint")
        ui.ui(f"  {C.GRAY}verify out-of-band, then: glypha connect {result['ip']} {result['port']} <your-name>{C.RESET}")
    else:
        ui.status(f"'{peer_id}' not found (never registered, or entry expired)")


def punch_mode(my_id, peer_id, my_port, rv_host):
    """Manual NAT-traversal attempt (debug/teaching mode)."""
    private_key = load_or_create_key(f"{my_id}_key.bin")
    own_public = bytes(private_key.public_key)
    own_fp = format_fingerprint(own_public)

    if rendezvous_register(private_key, my_id, my_port, rv_host) is None:
        ui.err("rendezvous unreachable — cannot punch")
        return

    target = None
    dl = time.time() + 20
    while time.time() < dl:
        target = rendezvous_lookup(peer_id, rv_host)
        if target and target.get("status") == "found":
            break
        time.sleep(1)
    if target is None or target.get("status") != "found":
        ui.status(f"'{peer_id}' never registered within 20s")
        return

    t_ip = target["ip"]
    t_port = target.get("punch_port") or target["port"]
    peer_fp_clean = target["fingerprint"].replace(":", "")
    i_am_connector = own_fp.replace(":", "") < peer_fp_clean

    ui.status(f"punching {t_ip}:{t_port} for 60s — start the other side too!")
    ui.ui(f"  {C.GRAY}my role: {'connector (I dial)' if i_am_connector else 'accepter (I wait)'}{C.RESET}")

    result = _punch_socket(private_key, own_public, t_ip, t_port,
                           peer_fp_clean, i_am_connector, my_port, 60)
    if result is None:
        ui.err("punch failed after 60s — that is the measured result.")
        return

    winner, peer_public = result
    ui.ok(f"punched through! {winner.getsockname()} -> {winner.getpeername()}")
    chat(winner, Box(private_key, PublicKey(peer_public)),
         format_fingerprint(peer_public), my_id)


def _punch_socket(private_key, own_public, t_ip, t_port, peer_fp_clean,
                  i_am_connector, my_port, window_s):
    """Simultaneous-open core. Connector dials the peer's public punch
    endpoint FROM our punch port; accepter waits for inbound on it.
    Identity-proof at accept: a connection must present the public key
    matching the registry fingerprint, or it is dropped."""
    verified = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", my_port))
    listener.listen()

    deadline = time.time() + window_s

    def try_accept():
        while time.time() < deadline and not verified:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            proof = recv_message(conn)
            if proof is not None and hashlib.sha256(proof).hexdigest() == peer_fp_clean:
                verified.append((conn, proof))
            else:
                conn.close()

    threading.Thread(target=try_accept, daemon=True).start()

    winner = None
    while time.time() < deadline and winner is None:
        if i_am_connector:
            # fresh socket per attempt: Windows retires a socket after a
            # failed connect, and retrying on it would fail forever
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", my_port))
            s.settimeout(2)
            try:
                s.connect((t_ip, t_port))
                send_message(s, own_public)
                winner = s
            except OSError:
                pass
        elif verified:
            winner = verified[0][0]
            break
        time.sleep(0.5)

    listener.close()
    if winner is None:
        return None

    winner.settimeout(None)
    if i_am_connector:
        peer_bytes = recv_message(winner)
        if peer_bytes is None or hashlib.sha256(peer_bytes).hexdigest() != peer_fp_clean:
            winner.close()
            return None
        peer_public = peer_bytes
    else:
        send_message(winner, own_public)
        peer_public = verified[0][1]
    return winner, peer_public


def relay_mode(my_name, peer_name, relay_host):
    """Manual relay chat (debug/teaching mode)."""
    result = _relay_socket(my_name, peer_name, relay_host)
    if result is None:
        return
    sock, box, peer_fp = result
    private_key = load_or_create_key(f"{my_name}_key.bin")
    if not verify_fingerprints(format_fingerprint(bytes(private_key.public_key)), peer_fp):
        ui.err("fingerprint not verified — closing.")
        sock.close()
        return
    chat(sock, box, peer_fp, my_name)


def _relay_socket(my_name, peer_name, relay_host):
    """Join the relay and handshake through the pipe. Only the SECOND
    joiner gets 'matched'; the first joiner's next bytes are the peer's
    handshake key through the pipe (transparent from splice onward)."""
    private_key = load_or_create_key(f"{my_name}_key.bin")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect((relay_host, RELAY_PORT))
    except OSError:
        return None

    send_message(sock, json.dumps(
        {"type": "relay_join", "id": my_name, "target": peer_name}).encode())

    resp_raw = recv_message(sock)
    if resp_raw is None:
        sock.close()
        return None
    status = json.loads(resp_raw.decode()).get("status")
    if status not in ("waiting", "matched"):
        sock.close()
        return None

    sock.settimeout(120)   # bounded wait for the peer; not forever
    box, peer_fp = handshake(sock, private_key, initiator=(status == "matched"))
    if box is None:
        sock.close()
        return None
    sock.settimeout(None)  # chat must block again
    return sock, box, peer_fp


# ───────────────────── chat mode (the product command) ─────────────────────

def try_direct(host, port, private_key, own_fp, expected_fp, peer_name):
    """Ladder rung 1: dial the rendezvous-published endpoint directly."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(8)
    try:
        sock.connect((host, port))
    except OSError:
        return None
    sock.settimeout(None)

    box, peer_fp = handshake(sock, private_key, initiator=True)
    if box is None:
        return None
    if not verify_or_reject(own_fp, peer_fp, expected_fp, peer_name):
        sock.close()
        return None
    return sock, box, peer_fp


def try_punch(my_name, peer_name, private_key, own_fp, rv_host, expected_fp):
    """Ladder rung 2: coordinated simultaneous open, shortened windows."""
    my_port = punch_port_for(my_name)
    own_public = bytes(private_key.public_key)

    if rendezvous_register(private_key, my_name, my_port, rv_host) is None:
        return None

    target = None
    dl = time.time() + 10
    while time.time() < dl:
        target = rendezvous_lookup(peer_name, rv_host)
        if target and target.get("status") == "found":
            break
        time.sleep(1)
    if target is None or target.get("status") != "found":
        return None

    t_ip = target["ip"]
    t_port = target.get("punch_port") or target["port"]
    peer_fp_clean = target["fingerprint"].replace(":", "")
    i_am_connector = own_fp.replace(":", "") < peer_fp_clean

    result = _punch_socket(private_key, own_public, t_ip, t_port,
                           peer_fp_clean, i_am_connector, my_port, 15)
    if result is None:
        return None

    winner, peer_public = result
    peer_fp = format_fingerprint(peer_public)
    if not verify_or_reject(own_fp, peer_fp, expected_fp, peer_name):
        winner.close()
        return None
    return winner, Box(private_key, PublicKey(peer_public)), peer_fp


def try_relay(my_name, peer_name, private_key, own_fp, rv_host, expected_fp):
    """Ladder rung 3: the always-works path."""
    result = _relay_socket(my_name, peer_name, rv_host)
    if result is None:
        return None
    sock, box, peer_fp = result
    if not verify_or_reject(own_fp, peer_fp, expected_fp, peer_name):
        sock.close()
        return None
    return sock, box, peer_fp


def chat_mode(my_name, peer_name, rv_host):
    """The product command: one command in, a verified encrypted chat out.
    Ladder: direct -> punch -> relay. The user never chooses a transport
    and never sees a traceback."""
    ui.banner()

    first_run = not identity.key_exists(f"{my_name}_key.bin")
    private_key = load_or_create_key(f"{my_name}_key.bin")
    own_fp = format_fingerprint(bytes(private_key.public_key))
    if first_run:
        ui.identity_screen(own_fp)

    ui.status(f"reaching '{peer_name}' via {rv_host}...")

    info = rendezvous_lookup(peer_name, rv_host)
    expected_fp = None
    result = None
    via = None

    if info is None:
        ui.warn(f"rendezvous unreachable at {rv_host} — trying relay path anyway")
    elif info.get("status") == "found":
        expected_fp = info.get("fingerprint")
        ui.status(f"trying direct to {info['ip']}:{info['port']}...")
        result = try_direct(info["ip"], info["port"], private_key, own_fp, expected_fp, peer_name)
        if result:
            via = "direct"
        else:
            ui.status("direct failed — trying NAT traversal...")
    else:
        ui.status(f"'{peer_name}' not in rendezvous — skipping direct...")

    if result is None:
        result = try_punch(my_name, peer_name, private_key, own_fp, rv_host, expected_fp)
        if result:
            via = "punch"
        else:
            ui.status("traversal failed — falling back to relay...")

    if result is None:
        result = try_relay(my_name, peer_name, private_key, own_fp, rv_host, expected_fp)
        if result:
            via = "relay"
        else:
            ui.err(f"could not reach '{peer_name}'.")
            ui.ui(f"  {C.GRAY}are they running: glypha chat {peer_name} {my_name} {rv_host}{C.RESET}")
            return

    sock, box, peer_fp = result
    ui.ok(f"connected via {via}")
    pinned = load_known_peers().get(peer_name, "").replace(":", "") == peer_fp.replace(":", "")
    meta = {"via": via, "endpoint": sock.getpeername(), "pinned": pinned, "verified": True}
    chat(sock, box, peer_fp, my_name, peer_display=peer_name, meta=meta)


# ───────────────────────── dispatch ─────────────────────────

def main():
    if len(sys.argv) < 2:
        ui.banner()
        print("usage:")
        print("  glypha chat <my_name> <peer_name> [rv_host]   # product command")
        print("  glypha config <rv_host>                       # save default host")
        print("  glypha listen [port] [name] [rv_host]")
        print("  glypha connect <host> [port] [name]")
        print("  glypha find <peer_id> [rv_host]")
        print("  glypha punch <my_id> <peer_id> [my_port] [rv_host]")
        print("  glypha relay <my_id> <peer_id> <relay_host>")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "listen":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 9999
        name = sys.argv[3] if len(sys.argv) > 3 else "peer"
        rv_host = sys.argv[4] if len(sys.argv) > 4 else None
        listen_mode(port, name, rv_host)

    elif mode == "connect":
        if len(sys.argv) < 3:
            print("connect requires a host"); sys.exit(1)
        host = sys.argv[2]
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 9999
        name = sys.argv[4] if len(sys.argv) > 4 else "peer"
        connect_mode(host, port, name)

    elif mode == "find":
        if len(sys.argv) < 3:
            print("find requires a peer id"); sys.exit(1)
        find_mode(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1")

    elif mode == "punch":
        if len(sys.argv) < 4:
            print("usage: glypha punch <my_id> <peer_id> [my_port] [rv_host]")
            sys.exit(1)
        my_id, peer_id = sys.argv[2], sys.argv[3]
        my_port = int(sys.argv[4]) if len(sys.argv) > 4 else 9999
        rv = sys.argv[5] if len(sys.argv) > 5 else "127.0.0.1"
        punch_mode(my_id, peer_id, my_port, rv)

    elif mode == "relay":
        if len(sys.argv) < 5:
            print("usage: glypha relay <my_id> <peer_id> <relay_host>")
            sys.exit(1)
        relay_mode(sys.argv[2], sys.argv[3], sys.argv[4])

    elif mode == "config":
        if len(sys.argv) < 3:
            print("usage: glypha config <rv_host>")
            sys.exit(1)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump({"rendezvous_host": sys.argv[2]}, f, indent=2)
        ui.ok(f"saved default rendezvous: {sys.argv[2]}")

    elif mode == "chat":
        if len(sys.argv) < 4:
            print("usage: glypha chat <my_name> <peer_name> [rv_host]")
            sys.exit(1)
        rv = sys.argv[4] if len(sys.argv) > 4 else load_default_rv()
        if rv is None:
            ui.err("no rendezvous host given and none configured.")
            print("Run once:  glypha config <rv_host>")
            sys.exit(1)
        chat_mode(sys.argv[2], sys.argv[3], rv)

    else:
        print("unknown mode:", mode)
        sys.exit(1)


if __name__ == "__main__":
    main()