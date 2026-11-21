import base64
import binascii
from pathlib import Path

from . import ed25519

ALGORITHM = b"Ed"
KEY_BLOB_SIZE = 42
SIGNATURE_BLOB_SIZE = 74
MAX_FILE_SIZE = 4096
COMMENT_PREFIX = "untrusted comment:"


class SignatureError(Exception):
    pass


def _decode_blob(data, expected_size):
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise SignatureError("malformed usign file")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].startswith(COMMENT_PREFIX):
        raise SignatureError("malformed usign file")
    try:
        blob = base64.b64decode(lines[1], validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("malformed usign file")
    if len(blob) != expected_size or blob[:2] != ALGORITHM:
        raise SignatureError("unsupported usign file")
    return blob


def load_keys(directory):
    keys = {}
    skipped = 0
    root = Path(directory)
    if not root.is_dir():
        return keys, skipped
    for entry in sorted(root.iterdir()):
        if not entry.is_file() or entry.stat().st_size > MAX_FILE_SIZE:
            skipped += 1
            continue
        try:
            blob = _decode_blob(entry.read_bytes(), KEY_BLOB_SIZE)
        except (SignatureError, OSError):
            skipped += 1
            continue
        keys[blob[2:10]] = blob[10:]
    return keys, skipped


def verify(message, signature_file, keys):
    if len(signature_file) > MAX_FILE_SIZE:
        raise SignatureError("signature file too large")
    blob = _decode_blob(signature_file, SIGNATURE_BLOB_SIZE)
    fingerprint = blob[2:10]
    public_key = keys.get(fingerprint)
    if public_key is None:
        raise SignatureError("no trusted key " + fingerprint.hex())
    if not ed25519.verify(public_key, message, blob[10:]):
        raise SignatureError("bad signature")
    return fingerprint.hex()
