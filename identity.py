# identity.py — persistent keys. Everything lives in ~/.glypha/ so moving
# the project folder never rebirths an identity.

import os

from nacl.public import PrivateKey
from nacl.secret import SecretBox
from nacl.utils import random

_KEY_DIR = os.path.join(os.path.expanduser("~"), ".glypha")


def _path(filename):
    os.makedirs(_KEY_DIR, exist_ok=True)
    return os.path.join(_KEY_DIR, filename)


def key_exists(filename):
    return os.path.exists(_path(filename))


def load_or_create_key(filename):
    path = _path(filename)
    if os.path.exists(path):
        with open(path, "rb") as file:
            return PrivateKey(file.read())
    privateKey = PrivateKey.generate()
    with open(path, "wb") as file:
        file.write(bytes(privateKey))
    return privateKey


def load_or_create_secret_key(filename):
    path = _path(filename)
    if os.path.exists(path):
        with open(path, "rb") as file:
            return file.read()
    key = random(SecretBox.KEY_SIZE)
    with open(path, "wb") as file:
        file.write(key)
    return key