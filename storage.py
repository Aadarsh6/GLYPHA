# storage.py — encrypted SQLite history. Databases live in ~/.glypha/
# alongside identity keys; pre-2.1.0 files in the working directory are
# migrated once, automatically.

import os
import shutil
import sqlite3

_KEY_DIR = os.path.join(os.path.expanduser("~"), ".glypha")


def _path(filename):
    # absolute paths (tests, tools) are used as-is; bare runtime names
    # resolve into ~/.glypha, with a one-time upgrade migration from CWD
    if os.path.isabs(filename):
        return filename
    os.makedirs(_KEY_DIR, exist_ok=True)
    new = os.path.join(_KEY_DIR, filename)
    old = os.path.join(os.getcwd(), filename)
    if not os.path.exists(new) and os.path.exists(old):
        shutil.copy2(old, new)
    return new


def init_db(filename):
    connection = sqlite3.connect(_path(filename))
    connection.execute("""
        CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL,
        direction TEXT NOT NULL,
        text TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.commit()
    connection.close()


def save_message(filename, fingerprint, direction, text, box):
    connection = sqlite3.connect(_path(filename))
    encrypted = box.encrypt(text.encode())
    connection.execute(
        "INSERT INTO messages (fingerprint, direction, text) VALUES(?, ?, ?)",
        (fingerprint, direction, encrypted)
    )
    connection.commit()
    connection.close()


def load_messages(filename, fingerprint, box):
    connection = sqlite3.connect(_path(filename))
    cursor = connection.execute(
        "SELECT direction, text, timestamp FROM messages WHERE fingerprint = ? ORDER BY id",
        (fingerprint,)
    )
    rows = cursor.fetchall()
    connection.close()
    messages = []
    for direction, encrypted_text, timestamp in rows:
        text = box.decrypt(encrypted_text).decode()
        messages.append((direction, text, timestamp))
    return messages