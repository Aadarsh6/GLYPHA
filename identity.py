import os

from nacl.public import PrivateKey
from nacl.secret import SecretBox
from nacl.utils import random


def key_exists(filename):
    return os.path.exists(__path(filename))

def load_or_create_key(filename):
    if os.path.exists(filename):
        with open(filename, "rb") as file:
            key_bytes = file.read()

        return PrivateKey(key_bytes)

    privateKey = PrivateKey.generate() 

    with open(filename, "wb") as file:
        file.write(bytes(privateKey))

    return privateKey

def load_or_create_secret_key(filename):
    if os.path.exists(filename):
        with open(filename, "rb") as file:
            return file.read()

    key = random(SecretBox.KEY_SIZE)

    with open(filename, "wb") as file:
        file.write(key)

    return key