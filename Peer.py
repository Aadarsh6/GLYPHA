# !Combined the  server.py and client.py so their is no redundancy 

import socket
import threading
import sys
import hashlib
from nacl.public import PrivateKey, PublicKey, Box
from nacl.secret import SecretBox
from protocol import recv_message, send_message
from identity import load_or_create_key, load_or_create_secret_key
from storage import init_db, save_message, load_messages
import json
import base64
import time

def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  #!OS-side route lookup only
# !It's only a clever way to ask the operating system:
# !"Which network interface/IP would you use to reach this outside address?"
    try:
        # *8.8.8.8 is Google's public DNS server.

        s.connect(("8.8.8.8", 80))  # UDP "connect": no packet sent, OS just picks a route  
        ip = s.getsockname()[0]     #getsockname() asks: "What local address is this socket using?"
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip



def format_fingerprint(public_bytes):
    fp = hashlib.sha256(public_bytes).hexdigest()
    return ":".join(fp[i:i + 4] for i in range(0, len(fp), 4))


def handshake(sock, private_key, initiator):
    """Exchange public keys and build the Box.
    initiator=True  → send ours first, then receive theirs (connector)
    initiator=False → receive theirs first, then send ours (listener)
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
    print("Your fingerprint:", own_fp)
    print("Peer fingerprint:", peer_fp)
    answer = input("Confirm peer fingerprint matches out-of-band (yes/no): ")
    return answer.lower() in ("yes", "y")


def chat(sock, box, peer_fp, name):
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
        try:
            send_message(sock, box.encrypt(message.encode()))
        except OSError:
            print("Peer is gone.")
            break
        save_message(db_filename, peer_fp, "sent", message, secret_box)

    # Leaving: mark closed first so the receive thread doesn't announce
    # a phantom "Peer disconnected." caused by our own socket close.
    # Then wait for it to finish its last print — a daemon thread killed
    # mid-print at interpreter shutdown can deadlock stdout and crash
    # with _enter_buffered_busy.
    connected = False
    sock.close()
    receiver.join(timeout=2)

RENDEZVOUS_PORT = 7000
RENDEZVOUS_REFRESH = 30  # server TTL is 90s; refresh at 1/3 of TTL


def rendezvous_register(private_key, name, listen_port, rv_host):
    """One registration attempt. Returns response dict or None."""
    request = {
        "type": "register",
        "id": name,
        "listen_port": listen_port,
        "public_key": base64.b64encode(bytes(private_key.public_key)).decode(),
    }
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", listen_port)) # punch trick: register FROM our listen port,
                                            # so the server observes the NAT mapping for it
        sock.settimeout(5)
        sock.connect((rv_host, RENDEZVOUS_PORT))
        send_message(sock, json.dumps(request).encode())
        resp = recv_message(sock)
        sock.close()
        return json.loads(resp.decode()) if resp else None
    except OSError:
        return None

def rendezvous_lookup(peer_id, rv_host):
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


def punch_mode(my_id, peer_id, my_port, rv_host):
    private_key = load_or_create_key(f"{my_id}_key.bin")
    own_public = bytes(private_key.public_key)
    own_fp = format_fingerprint(own_public)

    if rendezvous_register(private_key, my_id, my_port, rv_host) is None:
        print("Rendezvous unreachable — cannot punch")
        return

    # wait up to 20s for the peer to appear — start order no longer matters
    target = None
    dl = time.time() + 20
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
    i_am_connector = own_fp.replace(":", "") < peer_fp_clean

    print(f"Punching {t_ip}:{t_port} for 60s — start the other side too!")
    print("My role:", "connector (I dial)" if i_am_connector else "accepter (I wait)")

    winner = None
    verified = []   # (conn, peer_public_bytes) once identity is proven
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", my_port))
    listener.listen()

    deadline = time.time() + 60

    def try_accept():
        # Accept connections until one PROVES it is the peer by sending
        # the public key whose fingerprint we looked up. Anything else
        # (stale attempt, duplicate process) is closed, not trusted.
        while time.time() < deadline and not verified:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            proof = recv_message(conn)
            if proof is not None and hashlib.sha256(proof).hexdigest() == peer_fp_clean:
                verified.append((conn, proof))
            else:
                conn.close()   # junk or stale connection — drop it

    threading.Thread(target=try_accept, daemon=True).start()

    while time.time() < deadline and winner is None:
        if i_am_connector:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", my_port))
            s.settimeout(2)
            try:
                s.connect((t_ip, t_port))
                send_message(s, own_public)   # identify ourselves IMMEDIATELY
                winner = s
            except OSError:
                pass   # fresh socket next iteration
        elif verified:
            winner = verified[0][0]
            break
        time.sleep(0.5)

    listener.close()

    if winner is None:
        print("Punch failed after 60s — that is the measured result. Screenshot it.")
        return

    winner.settimeout(None)   # chat sockets must block, not time out
    print("PUNCHED THROUGH!", winner.getsockname(), "->", winner.getpeername())

    if i_am_connector:
        peer_bytes = recv_message(winner)          # accepter answers with its key
        if peer_bytes is None or hashlib.sha256(peer_bytes).hexdigest() != peer_fp_clean:
            print("Peer identity mismatch after punch — closing.")
            winner.close()
            return
        peer_public = peer_bytes
    else:
        send_message(winner, own_public)           # answer the proof
        peer_public = verified[0][1]               # already verified at accept

    box = Box(private_key, PublicKey(peer_public))

    if not verify_fingerprints(own_fp, format_fingerprint(peer_public)):
        print("Fingerprint mismatch — closing.")
        winner.close()
        return
    chat(winner, box, format_fingerprint(peer_public), my_id)



def rendezvous_refresh_loop(private_key, name, listen_port, rv_host):
    while True:
        time.sleep(RENDEZVOUS_REFRESH)
        if rendezvous_register(private_key, name, listen_port, rv_host) is None:
            print("[rendezvous] refresh failed — will retry")


def find_mode(peer_id, rv_host):
    request = {"type": "lookup", "id": peer_id}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((rv_host, RENDEZVOUS_PORT))
        send_message(sock, json.dumps(request).encode())
        resp = recv_message(sock)
        sock.close()
    except OSError:
        print(f"Rendezvous server at {rv_host}:{RENDEZVOUS_PORT} unreachable")
        return

    if resp is None:
        print("Rendezvous server closed the connection")
        return

    try:
        result = json.loads(resp.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("Rendezvous sent a malformed response")
        return

    if result.get("status") == "found":
        print(f"Peer '{peer_id}' is at {result['ip']}:{result['port']}")
        print(f"Fingerprint: {result['fingerprint']}")
        print("Verify this fingerprint with the peer out-of-band, then:")
        print(f"  python peer.py connect {result['ip']} {result['port']} <your-name>")
    else:
        print(f"'{peer_id}' not found (never registered, or entry expired)")




def listen_mode(port, name, rv_host=None):
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
    server.close()   # one session per run in V1; accept-loop is V2
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

RELAY_PORT = 7001


def relay_mode(my_name, peer_name, relay_host):
    """Chat through the relay when direct P2P is impossible.
    Handshake and chat are identical to direct mode — the relay is a
    transparent byte pipe."""
    private_key = load_or_create_key(f"{my_name}_key.bin")
    own_fp = format_fingerprint(bytes(private_key.public_key))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect((relay_host, RELAY_PORT))
    except OSError:
        print(f"Relay at {relay_host}:{RELAY_PORT} unreachable")
        return

    send_message(sock, json.dumps(
        {"type": "relay_join", "id": my_name, "target": peer_name}
    ).encode())

    resp_raw = recv_message(sock)
    if resp_raw is None:
        print("Relay closed the connection")
        return
    status = json.loads(resp_raw.decode()).get("status")
    print("[relay]", status)

    if status not in ("waiting", "matched"):
        sock.close()
        return

    if status == "waiting":
        print(f"Waiting for '{peer_name}' to join the relay...")

    # waiting  -> we joined first  -> handshake responder
    # matched  -> we joined second -> handshake initiator
    sock.settimeout(120)   # bounded wait for the peer; not forever

    box, peer_fp = handshake(sock, private_key, initiator=(status == "matched"))
    if box is None:
        print("Peer never joined the relay (or timed out)")
        return

    sock.settimeout(None)   # chat must block again

    if not verify_fingerprints(own_fp, peer_fp):
        print("Fingerprint not verified — closing.")
        sock.close()
        return
    chat(sock, box, peer_fp, my_name)

    
def main():
    if len(sys.argv) < 2:
        print("usage:")
        print("  python peer.py listen [port] [name]")
        print("  python peer.py connect <host> [port] [name]")
        print(" python peer.py find <peer_id> [rendezvous_host]")
        sys.exit(1)

    if sys.argv[1] == "listen":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 9999
        name = sys.argv[3] if len(sys.argv) > 3 else "peer"
        rv_host = sys.argv[4] if len(sys.argv) > 4 else None
        listen_mode(port, name, rv_host)
    elif sys.argv[1] == "connect":
        if len(sys.argv) < 3:
            print("connect requires a host")
            sys.exit(1)
        host = sys.argv[2]
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 9999
        name = sys.argv[4] if len(sys.argv) > 4 else "peer"
        connect_mode(host, port, name)
    elif sys.argv[1] == "find":
        if len(sys.argv) < 3:
            print("find requires a peer id")
            sys.exit(1)
        find_mode(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1")
    elif sys.argv[1] == "punch":
        if len(sys.argv) < 4:
            print("usage: python peer.py punch <my_id> <peer_id> [my_port] [rv_host]")
            sys.exit(1)
        my_id = sys.argv[2]
        peer_id = sys.argv[3]
        my_port = int(sys.argv[4]) if len(sys.argv) > 4 else 9999
        rv = sys.argv[5] if len(sys.argv) > 5 else "127.0.0.1"
        punch_mode(my_id, peer_id, my_port, rv)

    elif sys.argv[1] == "relay":
        # usage: python peer.py relay <my_id> <peer_id> <relay_host>
        if len(sys.argv) < 5:
            print("usage: python peer.py relay <my_id> <peer_id> <relay_host>")
            sys.exit(1)
        relay_mode(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print("unknown mode:", sys.argv[1])
        sys.exit(1)




if __name__ == "__main__":
    main()