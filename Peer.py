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

from nacl.public import PrivateKey, PublicKey, Box
from nacl.secret import SecretBox
from protocol import recv_message, send_message
from identity import load_or_create_key, load_or_create_secret_key
from storage import init_db, save_message, load_messages

RENDEZVOUS_PORT = 7000
RENDEZVOUS_REFRESH = 30   # server TTL is 90s; refresh at 1/3 of TTL
RELAY_PORT = 7001


# ───────────────────────── helpers ─────────────────────────

def get_lan_ip():
    """Our LAN IP via a UDP route lookup — no packet is ever sent.
    (A TCP socket here would open a real connection to 8.8.8.8:80.)"""
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
    """SHA-256 of a public key, grouped 4-hex-chunks — the string
    two humans compare out-of-band to authenticate each other."""
    fp = hashlib.sha256(public_bytes).hexdigest()
    return ":".join(fp[i:i + 4] for i in range(0, len(fp), 4))


def punch_port_for(name):
    """Deterministic per-name port (20000-39999): two peers on one machine
    never collide, and an identity always punches from the same port."""
    h = int(hashlib.sha256(name.encode()).hexdigest(), 16)
    return 20000 + (h % 20000)


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
    print("Your fingerprint:", own_fp)
    print("Peer fingerprint:", peer_fp)
    answer = input("Confirm peer fingerprint matches out-of-band (yes/no): ")
    return answer.lower() in ("yes", "y")


KNOWN_PEERS_FILE = "known_peers.json"   # local trust pins — gitignored

def load_known_peers():
    try:
        with open(KNOWN_PEERS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def pin_peer(name, fingerprint):
    known = load_known_peers()
    known[name] = fingerprint
    with open(KNOWN_PEERS_FILE, "w") as f:
        json.dump(known, f, indent=2)

def verify_or_reject(own_fp, peer_fp, expected_fp, peer_name=None):
    """Trust model (SSH known_hosts style):
    - pinned before?  auto-verify against the PIN, not the registry.
      A changed key = loud warning + reject (possible impersonation).
    - first contact?  the human verifies out-of-band — the real TOFU
      moment — then we pin for all future contacts.
    expected_fp (registry) is only a cross-check, never a trust source."""
    clean = peer_fp.replace(":", "")
    pinned = load_known_peers().get(peer_name) if peer_name else None

    if pinned:
        if clean == pinned.replace(":", ""):
            print(f"[chat] '{peer_name}' verified against pinned key")
            return True
        print(f"[chat] KEY CHANGE for '{peer_name}'!")
        print(f"       pinned: {pinned}")
        print(f"       got:    {peer_fp}")
        print("[chat] possible impersonation — rejecting.")
        return False

    # first contact: the human decides, then we pin
    if verify_fingerprints(own_fp, peer_fp):
        if peer_name:
            pin_peer(peer_name, clean)
            print(f"[chat] pinned '{peer_name}' for future auto-verification")
        return True
    return False


# ───────────────────────── chat (shared by every mode) ─────────────────────────

def chat(sock, box, peer_fp, name):
    """The conversation loop: load history, run a receive thread and an
    input/send loop, store every message encrypted, exit cleanly."""
    storage_key = load_or_create_secret_key(f"{name}_storage_key.bin")
    secret_box = SecretBox(storage_key)
    db_filename = f"{name}_history.db"

    init_db(db_filename)

    for direction, text, timestamp in load_messages(db_filename, peer_fp, secret_box):
        print(f"{'You' if direction == 'sent' else 'Them'}: {text}")

    print("Secure connection established!")

    connected = True

    def receive_loop():
        nonlocal connected
        while True:
            data = recv_message(sock)
            if data is None:
                if connected:   # announce only if WE didn't initiate the close
                    print("\nPeer disconnected.")
                connected = False
                break
            try:
                message = box.decrypt(data).decode()
            except Exception:
                print("\nReceived an undecryptable frame — ignored.")
                continue
            save_message(db_filename, peer_fp, "received", message, secret_box)
            print("Them:", message)

    receiver = threading.Thread(target=receive_loop, daemon=True)
    receiver.start()

    while connected:
        try:
            message = input(f"{name}: ")
        except KeyboardInterrupt:
            break
        if not connected:
            print("Peer is gone.")
            break
        if message == "quit":
            break
        if not message.strip():    # bare Enter / whitespace: send nothing
            continue
        try:
            send_message(sock, box.encrypt(message.encode()))
        except OSError:
            print("Peer is gone.")
            break
        save_message(db_filename, peer_fp, "sent", message, secret_box)

    # Mark closed before closing the socket (no phantom "Peer disconnected."
    # from our own close), then join the receiver: a daemon thread killed
    # mid-print at shutdown can deadlock stdout (_enter_buffered_busy).
    connected = False
    sock.close()
    receiver.join(timeout=2)


# ───────────────────────── rendezvous client ─────────────────────────

def rendezvous_register(private_key, name, listen_port, rv_host):
    """Publish our name + listen port + public key. The socket is bound
    to our listen port so the server observes the NAT mapping for it.
    Returns the response dict, or None if unreachable."""
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
    """Ask the rendezvous where a peer is. Returns response dict or None."""
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
            print("[rendezvous] refresh failed — will retry")


# ───────────────────────── explicit modes (internals) ─────────────────────────

def listen_mode(port, name, rv_host=None):
    """Wait for one inbound peer, then chat. Optional rendezvous
    registration makes us discoverable by name."""
    private_key = load_or_create_key(f"{name}_key.bin")
    own_fp = format_fingerprint(bytes(private_key.public_key))

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", port))
    server.listen()

    print("Your fingerprint:", own_fp)
    print(f"Listening on 0.0.0.0:{port}")
    print(f"Other peer connects with: python peer.py connect {get_lan_ip()} {port} <their-name>")

    if rv_host:
        # discovery is optional infrastructure: if it's down, chat still works
        resp = rendezvous_register(private_key, name, port, rv_host)
        print(f"[rendezvous] registered — discoverable as '{name}'") if resp \
            else print("[rendezvous] registration failed (continuing without discovery)")
        threading.Thread(
            target=rendezvous_refresh_loop,
            args=(private_key, name, port, rv_host),
            daemon=True,
        ).start()

    sock, address = server.accept()
    server.close()   # one session per run; multi-session accept loop is future work
    print("Peer connected:", address)

    box, peer_fp = handshake(sock, private_key, initiator=False)
    if box is None:
        print("Peer disconnected during handshake.")
        return
    if not verify_fingerprints(own_fp, peer_fp):
        print("Fingerprint not verified — closing.")
        sock.close()
        return
    chat(sock, box, peer_fp, name)


def connect_mode(host, port, name):
    """Dial a known host:port directly, then chat."""
    private_key = load_or_create_key(f"{name}_key.bin")
    own_fp = format_fingerprint(bytes(private_key.public_key))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    print(f"Connected to {host}:{port}")

    box, peer_fp = handshake(sock, private_key, initiator=True)
    if box is None:
        print("Peer disconnected during handshake.")
        return
    if not verify_fingerprints(own_fp, peer_fp):
        print("Fingerprint not verified — closing.")
        sock.close()
        return
    chat(sock, box, peer_fp, name)


def find_mode(peer_id, rv_host):
    """One-shot discovery: print where a peer is + its fingerprint."""
    result = rendezvous_lookup(peer_id, rv_host)
    if result is None:
        print(f"Rendezvous server at {rv_host}:{RENDEZVOUS_PORT} unreachable")
        return
    if result.get("status") == "found":
        print(f"Peer '{peer_id}' is at {result['ip']}:{result['port']}")
        print(f"Fingerprint: {result['fingerprint']}")
        print("Verify this fingerprint with the peer out-of-band, then:")
        print(f"  python peer.py connect {result['ip']} {result['port']} <your-name>")
    else:
        print(f"'{peer_id}' not found (never registered, or entry expired)")


def punch_mode(my_id, peer_id, my_port, rv_host):
    """Manual NAT-traversal attempt (debug/teaching mode): register,
    find the peer, run the simultaneous open, chat on success."""
    private_key = load_or_create_key(f"{my_id}_key.bin")
    own_public = bytes(private_key.public_key)
    own_fp = format_fingerprint(own_public)

    if rendezvous_register(private_key, my_id, my_port, rv_host) is None:
        print("Rendezvous unreachable — cannot punch")
        return

    target = None
    dl = time.time() + 20   # peer may still be registering
    while time.time() < dl:
        target = rendezvous_lookup(peer_id, rv_host)
        if target and target.get("status") == "found":
            break
        time.sleep(1)
    if target is None or target.get("status") != "found":
        print(f"'{peer_id}' never registered within 20s")
        return

    t_ip = target["ip"]
    t_port = target.get("punch_port") or target["port"]
    peer_fp_clean = target["fingerprint"].replace(":", "")
    i_am_connector = own_fp.replace(":", "") < peer_fp_clean   # deterministic roles

    print(f"Punching {t_ip}:{t_port} for 60s — start the other side too!")
    print("My role:", "connector (I dial)" if i_am_connector else "accepter (I wait)")

    result = _punch_socket(private_key, own_public, t_ip, t_port,
                           peer_fp_clean, i_am_connector, my_port, 60)
    if result is None:
        print("Punch failed after 60s — that is the measured result. Screenshot it.")
        return

    winner, peer_public = result
    print("PUNCHED THROUGH!", winner.getsockname(), "->", winner.getpeername())
    chat(winner, Box(private_key, PublicKey(peer_public)),
         format_fingerprint(peer_public), my_id)


def _punch_socket(private_key, own_public, t_ip, t_port, peer_fp_clean,
                  i_am_connector, my_port, window_s):
    """Simultaneous-open core. Connector repeatedly dials the peer's public
    punch endpoint FROM our punch port; accepter waits for inbound on it.
    Identity-proof at accept: a connection must present the public key
    matching the registry fingerprint, or it is dropped. Returns
    (sock, peer_public_bytes) or None."""
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
                conn.close()   # junk, stale attempt, or duplicate process

    threading.Thread(target=try_accept, daemon=True).start()

    winner = None
    while time.time() < deadline and winner is None:
        if i_am_connector:
            # fresh socket per attempt: Windows retires a socket after a
            # failed connect, and retrying on it would fail forever
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", my_port))   # THE punch: same local port every attempt
            s.settimeout(2)
            try:
                s.connect((t_ip, t_port))
                send_message(s, own_public)   # identify ourselves IMMEDIATELY
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

    winner.settimeout(None)   # chat sockets must block, not time out

    if i_am_connector:
        peer_bytes = recv_message(winner)   # accepter answers with its key
        if peer_bytes is None or hashlib.sha256(peer_bytes).hexdigest() != peer_fp_clean:
            winner.close()
            return None
        peer_public = peer_bytes
    else:
        send_message(winner, own_public)    # answer the proof
        peer_public = verified[0][1]        # already verified at accept

    return winner, peer_public


def relay_mode(my_name, peer_name, relay_host):
    """Manual relay chat (debug/teaching mode)."""
    result = _relay_socket(my_name, peer_name, relay_host)
    if result is None:
        return
    sock, box, peer_fp = result
    private_key = load_or_create_key(f"{my_name}_key.bin")
    if not verify_fingerprints(format_fingerprint(bytes(private_key.public_key)), peer_fp):
        print("Fingerprint not verified — closing.")
        sock.close()
        return
    chat(sock, box, peer_fp, my_name)


def _relay_socket(my_name, peer_name, relay_host):
    """Join the relay and handshake through the pipe. Join protocol:
    both peers send {id, target}; when they name each other mutually the
    relay splices the sockets. Only the SECOND joiner gets a 'matched'
    reply — the first joiner's very next bytes are the peer's handshake
    key through the pipe (transparent from splice onward).
    Returns (sock, box, peer_fp) or None."""
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

    # waiting -> joined first  -> handshake responder
    # matched -> joined second -> handshake initiator
    sock.settimeout(120)   # bounded wait for the peer; not forever
    box, peer_fp = handshake(sock, private_key, initiator=(status == "matched"))
    if box is None:
        sock.close()
        return None
    sock.settimeout(None)  # chat must block again
    return sock, box, peer_fp


# ───────────────────── V2.3: chat mode (the product command) ─────────────────────

def try_direct(host, port, private_key, own_fp, expected_fp, peer_name):
    """Ladder rung 1: dial the rendezvous-published endpoint directly
    (works on LAN / public endpoints). Returns (sock, box, peer_fp) or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(8)   # fast fail — a dead NAT path must not stall the ladder
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
    """Ladder rung 2: coordinated simultaneous open with shortened windows
    so hostile NATs reach the relay quickly. Returns (sock, box, peer_fp) or None."""
    my_port = punch_port_for(my_name)
    own_public = bytes(private_key.public_key)

    if rendezvous_register(private_key, my_name, my_port, rv_host) is None:
        return None

    target = None
    dl = time.time() + 10   # peer is walking the same ladder; give them time to register
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
    """Ladder rung 3: the always-works path (V2.1). Returns (sock, box, peer_fp) or None."""
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
    private_key = load_or_create_key(f"{my_name}_key.bin")
    own_fp = format_fingerprint(bytes(private_key.public_key))

    print(f"[chat] reaching '{peer_name}' via {rv_host}...")

    # ONE lookup up front: drives rung 1 AND rendezvous-assisted verification
    info = rendezvous_lookup(peer_name, rv_host)
    expected_fp = None
    result = None

    if info is not None and info.get("status") == "found":
        expected_fp = info.get("fingerprint")
        print(f"[chat] trying direct connection to {info['ip']}:{info['port']}...")
        result = try_direct(info["ip"], info["port"], private_key, own_fp, expected_fp, peer_name)
        if result is None:
            print("[chat] direct failed — trying NAT traversal...")
    else:
        print(f"[chat] '{peer_name}' not in rendezvous — skipping direct, trying traversal...")

    if result is None:
        result = try_punch(my_name, peer_name, private_key, own_fp, rv_host, expected_fp)
        if result is None:
            print("[chat] traversal failed — falling back to relay...")

    if result is None:
        result = try_relay(my_name, peer_name, private_key, own_fp, rv_host, expected_fp)
        if result is None:
            print(f"[chat] could not reach '{peer_name}'.")
            print(f"       Are they running:  python peer.py chat {peer_name} {my_name} {rv_host}")
            return

    sock, box, peer_fp = result
    print("[chat] connected!")
    chat(sock, box, peer_fp, my_name)


# ───────────────────────── dispatch ─────────────────────────

def main():
    if len(sys.argv) < 2:
        print("usage:")
        print("  python peer.py chat <my_name> <peer_name> [rv_host]   # product command")
        print("  python peer.py listen [port] [name] [rv_host]")
        print("  python peer.py connect <host> [port] [name]")
        print("  python peer.py find <peer_id> [rv_host]")
        print("  python peer.py punch <my_id> <peer_id> [my_port] [rv_host]")
        print("  python peer.py relay <my_id> <peer_id> <relay_host>")
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
            print("usage: python peer.py punch <my_id> <peer_id> [my_port] [rv_host]")
            sys.exit(1)
        my_id, peer_id = sys.argv[2], sys.argv[3]
        my_port = int(sys.argv[4]) if len(sys.argv) > 4 else 9999
        rv = sys.argv[5] if len(sys.argv) > 5 else "127.0.0.1"
        punch_mode(my_id, peer_id, my_port, rv)

    elif mode == "relay":
        if len(sys.argv) < 5:
            print("usage: python peer.py relay <my_id> <peer_id> <relay_host>")
            sys.exit(1)
        relay_mode(sys.argv[2], sys.argv[3], sys.argv[4])

    elif mode == "chat":
        if len(sys.argv) < 4:
            print("usage: python peer.py chat <my_name> <peer_name> [rv_host]")
            sys.exit(1)
        rv = sys.argv[4] if len(sys.argv) > 4 else "127.0.0.1"
        chat_mode(sys.argv[2], sys.argv[3], rv)

    else:
        print("unknown mode:", mode)
        sys.exit(1)


if __name__ == "__main__":
    main()