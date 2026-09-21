# Relay server: splices two peers' outbound connections into a pipe.
# Copies opaque bytes. No parsing, no decryption, no keys — the VM
# doesn't even have PyNaCl installed. Port 7001 (rendezvous is 7000).
# Known limitation (V2 hardening): a waiting entry whose peer never
# joins stays in memory until the relay restarts.

import socket
import threading
import json

from protocol import send_message, recv_message

HOST = "0.0.0.0"
PORT = 7001

waiting = {}          # id -> (socket, target_they_are_waiting_for)
lock = threading.Lock()


def pump(src, dst):
    """Copy raw bytes src -> dst until either side dies."""
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try: src.close()
        except OSError: pass
        try: dst.close()
        except OSError: pass


def handle(conn, addr):
    spliced = False
    registered = False
    try:
        data = recv_message(conn)
        if data is None:
            return
        req = json.loads(data.decode())
        if req.get("type") != "relay_join" or not req.get("id") or not req.get("target"):
            send_message(conn, json.dumps({"status": "bad_request"}).encode())
            return

        my_id, target = req["id"], req["target"]

        with lock:
            rec = waiting.get(target)
            if rec and rec[1] == my_id:
                # mutual match: they were waiting for ME by name
                waiting.pop(target)
                peer_conn = rec[0]
            else:
                # no match yet — register and wait (keeps socket open)
                waiting[my_id] = (conn, target)
                registered = True
                peer_conn = None

        if peer_conn is not None:
            spliced = True
            # tell ONLY the second joiner; the first joiner's next bytes
            # are the second's handshake key (transparent pipe from here)
            send_message(conn, json.dumps({"status": "matched"}).encode())
            print(f"[relay] spliced {target} <-> {my_id}")
            threading.Thread(target=pump, args=(peer_conn, conn), daemon=True).start()
            threading.Thread(target=pump, args=(conn, peer_conn), daemon=True).start()
        else:
            send_message(conn, json.dumps({"status": "waiting"}).encode())
            print(f"[relay] {my_id} waiting for {target}")

    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        try:
            send_message(conn, json.dumps({"status": "bad_request"}).encode())
        except OSError:
            pass
    finally:
        # close ONLY sockets we never handed off. Waiting sockets belong
        # to the registry; spliced sockets belong to the pump threads.
        if not (spliced or registered):
            try:
                conn.close()
            except OSError:
                pass


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    print(f"Relay server on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nRelay stopped.")