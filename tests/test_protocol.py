import os
import socket
import struct
import threading

import pytest

from protocol import send_message, recv_message


def make_pair():
    """Two connected sockets, no network needed. recv/send behave exactly
    like real sockets — same syscalls, same buffering, same errors."""
    a, b = socket.socketpair()
    return a, b


def frame(data):
    """Build a wire frame by hand, INDEPENDENT of send_message.
    Tests the receiver against bytes it didn't get from our own sender."""
    return struct.pack("!I", len(data)) + data


def test_roundtrip_small():
    a, b = make_pair()
    send_message(a, b"hello")
    assert recv_message(b) == b"hello"
    a.close(); b.close()


def test_roundtrip_empty_message():
    # length-0 frames are legal and must come back as b"", not None.
    # None means "connection dead" — confusing the two would corrupt
    # every layer above. (b"" is falsy in Python; this edge is a classic bug.)
    a, b = make_pair()
    send_message(a, b"")
    assert recv_message(b) == b""
    a.close(); b.close()


def test_roundtrip_binary_payload():
    # the protocol must be binary-safe — ciphertext isn't UTF-8
    a, b = make_pair()
    payload = bytes(range(256))
    send_message(a, payload)
    assert recv_message(b) == payload
    a.close(); b.close()


def test_multiple_messages_back_to_back():
    # two frames sent in ONE burst must arrive as two messages —
    # this is the entire reason framing exists (TCP is a byte stream)
    a, b = make_pair()
    a.sendall(frame(b"first") + frame(b"second"))   # raw, no sender involved
    assert recv_message(b) == b"first"
    assert recv_message(b) == b"second"
    a.close(); b.close()


def test_fragmented_delivery():
    # a receiver must reassemble a message that arrives in pieces.
    # The header itself is split — recv_message's inner loops must
    # accumulate until complete, never assume one recv() == one message.
    a, b = make_pair()
    f = frame(b"reassembly test message")
    a.sendall(f[:2])          # half the length header
    a.sendall(f[2:5])         # rest of header + a bit
    a.sendall(f[5:])          # remainder
    assert recv_message(b) == b"reassembly test message"
    a.close(); b.close()


def test_large_message():
    # 1 MiB — bigger than any kernel socket buffer, so sendall MUST
    # block until the reader drains, and the receiver must reassemble.
    # Runs the receive in a thread exactly like our real receive thread.
    a, b = make_pair()
    payload = os.urandom(1024 * 1024)
    result = {}
    t = threading.Thread(target=lambda: result.update(data=recv_message(b)))
    t.start()
    send_message(a, payload)
    t.join(timeout=15)
    assert result["data"] == payload
    a.close(); b.close()


def test_clean_disconnect_returns_none():
    # peer closes politely (FIN) → b"" on recv → recv_message → None.
    # None is our ONE failure convention.
    a, b = make_pair()
    a.close()
    assert recv_message(b) is None
    b.close()


def test_reset_disconnect_returns_none():
    # REGRESSION TEST for the M13 bug: on Windows, Ctrl+C kills a peer
    # with a TCP RST (not FIN). recv() then raises ConnectionResetError,
    # which used to crash the receive thread. SO_LINGER(0) forces close()
    # to send RST, reproducing that exact scenario.
    a, b = make_pair()
    a.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    a.close()   # abrupt: RST, no FIN
    assert recv_message(b) is None   # OSError-as-EOF contract
    b.close()