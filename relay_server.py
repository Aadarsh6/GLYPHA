# Relay server: splices two peers' outbound connections into a pipe.
# Copies opaque bytes. No parsing, no decryption, no keys — the VM
# doesn't even have PyNaCl installed. Port 7001 (rendezvous is 7000).

import socket
import threading
import json

from protocol import send_message, recv_message

HOST = "0.0.0.0"
PORT = 7001

waiting = {}          # id -> socket (peer waiting for a target)
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
        # closing both unblocks the other pump's recv
        try: src.close()
        except OSError: pass
        try: dst.close()
        except OSError: pass


def splice(a, b):
    """Wire two sockets to each other and report to both peers."""
    for sock, status in ((a, b"matched"), (b, b"matched")):
        send_message(sock, json.dumps({"status": "matched"}).encode())
    threading.Thread(target=pump, args=(a, b), daemon=True).start()
    threading.Thread(target=pump, args=(b, a), daemon=True).start()
    print(f"[relay] spliced a pair")


def handle(conn, addr):
    try:
        data = recv_message(conn)
        if data is None:
            return
        req = json.loads(data.decode())

        if req.get("type") != "relay_join":
            send_message(conn, json.dumps({"status": "bad_request"}).encode())
            return

        my_id = req.get("id")
        target = req.get("target")

        if not my_id:
            send_message(conn, json.dumps({"status": "bad_request"}).encode())
            return

        if target is None:
            # first joiner: register and wait
            with lock:
                waiting[my_id] = conn
            send_message(conn, json.dumps({"status": "waiting"}).encode())
            print(f"[relay] {my_id} waiting")
            return

        # second joiner: find target, splice
        with lock:
            peer_conn = waiting.pop(target, None)
        if peer_conn is None:
            send_message(conn, json.dumps({"status": "target_not_waiting"}).encode())
            return

        print(f"[relay] {my_id} -> {target}: splicing")
        splice(peer_conn, conn)

    except (json.JSONDecodeError, UnicodeDecodeError):
        send_message(conn, json.dumps({"status": "bad_request"}).encode())
    except KeyError:
        pass
    finally:
        if conn.fileno() != -1:   # only close if never spliced
            conn.close()


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