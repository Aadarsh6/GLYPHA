import sqlite3

import pytest
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.utils import random as random_bytes

from storage import init_db, save_message, load_messages
from identity import load_or_create_secret_key


@pytest.fixture
def env(tmp_path):
    """Fresh db + storage key per test, in pytest's temp dir.
    No test ever touches real project files."""
    db = str(tmp_path / "history.db")
    key = random_bytes(SecretBox.KEY_SIZE)
    box = SecretBox(key)
    init_db(db)
    return db, box


def test_roundtrip(env):
    db, box = env
    save_message(db, "FP1", "sent", "hello from the test", box)
    msgs = load_messages(db, "FP1", box)
    assert msgs == [("sent", "hello from the test", msgs[0][2])]


def test_insertion_order_preserved(env):
    # history must replay in the order it happened. This locks the
    # ORDER BY id contract (the timestamp-ordering bug we fixed —
    # CURRENT_TIMESTAMP has second granularity and scrambles fast chats)
    db, box = env
    for text in ["first", "second", "third", "fourth"]:
        save_message(db, "FP1", "sent", text, box)
    loaded = [t for _, t, _ in load_messages(db, "FP1", box)]
    assert loaded == ["first", "second", "third", "fourth"]


def test_per_fingerprint_isolation(env):
    # history is keyed by fingerprint — one peer's messages must never
    # appear in another peer's history (M8 design decision)
    db, box = env
    save_message(db, "FP_ALICE", "sent", "for alice", box)
    save_message(db, "FP_BOB", "sent", "for bob", box)
    alice = [t for _, t, _ in load_messages(db, "FP_ALICE", box)]
    bob = [t for _, t, _ in load_messages(db, "FP_BOB", box)]
    assert alice == ["for alice"]
    assert bob == ["for bob"]


def test_direction_preserved(env):
    db, box = env
    save_message(db, "FP1", "sent", "me", box)
    save_message(db, "FP1", "received", "them", box)
    dirs = [d for d, _, _ in load_messages(db, "FP1", box)]
    assert dirs == ["sent", "received"]


def test_ciphertext_at_rest(env):
    # THE storage guarantee: the db file contains NO plaintext.
    # We bypass our own API and read the raw column with plain SQL —
    # exactly what an attacker with the .db file would do.
    db, box = env
    secret = "the launch codes are 1234"
    save_message(db, "FP1", "sent", secret, box)
    raw = sqlite3.connect(db).execute("SELECT text FROM messages").fetchall()
    assert len(raw) == 1
    assert secret.encode() not in raw[0][0]      # plaintext absent
    assert secret not in raw[0][0].decode("latin-1", errors="ignore")


def test_wrong_key_raises_crypto_error(env):
    # REGRESSION TEST for the M12 incident: regenerating a storage key
    # orphaned the old history. The correct behavior is a LOUD failure
    # (CryptoError), never silent garbage.
    db, box = env
    save_message(db, "FP1", "sent", "encrypted with the real key", box)
    wrong_box = SecretBox(random_bytes(SecretBox.KEY_SIZE))
    with pytest.raises(CryptoError):
        load_messages(db, "FP1", wrong_box)