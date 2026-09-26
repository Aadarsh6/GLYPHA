from identity import load_or_create_key
from peer import format_fingerprint
print(format_fingerprint(bytes(load_or_create_key("aadarsh_key.bin").public_key)))