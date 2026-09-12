import hashlib

from nacl.public import PrivateKey
from nacl.secret import SecretBox

from identity import load_or_create_key, load_or_create_secret_key
from peer import format_fingerprint


def test_key_created_and_persists(tmp_path):
    # the WHOLE point of M6: identity survives restarts.
    # Second load must return the SAME key, not a new one.
    f = str(tmp_path / "k.bin")
    k1 = load_or_create_key(f)
    k2 = load_or_create_key(f)
    assert bytes(k1) == bytes(k2)


def test_different_files_different_identities(tmp_path):
    ka = load_or_create_key(str(tmp_path / "a.bin"))
    kb = load_or_create_key(str(tmp_path / "b.bin"))
    assert bytes(ka) != bytes(kb)


def test_fingerprint_stable_across_reloads(tmp_path):
    # a peer's fingerprint must be identical every run — it IS the identity
    f = str(tmp_path / "k.bin")
    fp1 = format_fingerprint(bytes(load_or_create_key(f).public_key))
    fp2 = format_fingerprint(bytes(load_or_create_key(f).public_key))
    assert fp1 == fp2


def test_fingerprint_format():
    # contract: 64 hex chars → 16 groups of 4, colon-separated.
    # This format is what two humans compare out-of-band — it must not drift.
    # Stripping colons must yield exactly the raw SHA-256 of the key bytes:
    # grouping is cosmetic, the underlying hash is the identity.
    pub = bytes(PrivateKey.generate().public_key)
    fp = format_fingerprint(pub)
    groups = fp.split(":")
    assert len(groups) == 16
    assert all(len(g) == 4 for g in groups)
    assert fp.replace(":", "") == hashlib.sha256(pub).hexdigest()


def test_secret_key_size_and_persistence(tmp_path):
    f = str(tmp_path / "s.bin")
    s1 = load_or_create_secret_key(f)
    s2 = load_or_create_secret_key(f)
    assert s1 == s2
    assert len(s1) == SecretBox.KEY_SIZE   # 32 bytes — a wrong size would fail at runtime