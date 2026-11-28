import base64
import binascii
import hashlib
import re
from pathlib import Path

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
OID_EC_PUBLIC_KEY = bytes.fromhex("2a8648ce3d0201")
OID_PRIME256V1 = bytes.fromhex("2a8648ce3d030107")
PEM_RE = re.compile(r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", re.S)
MAX_KEY_FILE_SIZE = 4096
KEY_ID_SIZE = 16
POINT_SIZE = 65


class KeyFormatError(Exception):
    pass


def _inv(value, modulus):
    return pow(value, modulus - 2, modulus)


def _double(point):
    x, y, z = point
    if not z or not y:
        return (0, 1, 0)
    yy = y * y % P
    s = 4 * x * yy % P
    m = (3 * x * x + A * pow(z, 4, P)) % P
    nx = (m * m - 2 * s) % P
    ny = (m * (s - nx) - 8 * yy * yy) % P
    nz = 2 * y * z % P
    return (nx, ny, nz)


def _add(p1, p2):
    if not p1[2]:
        return p2
    if not p2[2]:
        return p1
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    z1z1 = z1 * z1 % P
    z2z2 = z2 * z2 % P
    u1 = x1 * z2z2 % P
    u2 = x2 * z1z1 % P
    s1 = y1 * z2 * z2z2 % P
    s2 = y2 * z1 * z1z1 % P
    if u1 == u2:
        if s1 != s2:
            return (0, 1, 0)
        return _double(p1)
    h = (u2 - u1) % P
    r = (s2 - s1) % P
    hh = h * h % P
    hhh = h * hh % P
    v = u1 * hh % P
    nx = (r * r - hhh - 2 * v) % P
    ny = (r * (v - nx) - s1 * hhh) % P
    nz = h * z1 * z2 % P
    return (nx, ny, nz)


def _combine(k1, p1, k2, p2):
    both = _add(p1, p2)
    result = (0, 1, 0)
    for bit in range(max(k1.bit_length(), k2.bit_length()) - 1, -1, -1):
        result = _double(result)
        first = (k1 >> bit) & 1
        second = (k2 >> bit) & 1
        if first and second:
            result = _add(result, both)
        elif first:
            result = _add(result, p1)
        elif second:
            result = _add(result, p2)
    return result


def _affine_x(point):
    x, _, z = point
    if not z:
        return None
    inverse = pow(z, P - 2, P)
    return x * inverse * inverse % P


def _on_curve(x, y):
    if not (0 <= x < P and 0 <= y < P):
        return False
    return (y * y - (x * x * x + A * x + B)) % P == 0


def _read(data, position):
    if position + 2 > len(data):
        raise KeyFormatError("truncated DER")
    tag = data[position]
    length = data[position + 1]
    position += 2
    if length & 0x80:
        count = length & 0x7F
        if not count or count > 4 or position + count > len(data):
            raise KeyFormatError("bad DER length")
        length = int.from_bytes(data[position:position + count], "big")
        position += count
    end = position + length
    if end > len(data):
        raise KeyFormatError("truncated DER")
    return tag, data[position:end], end


def _parse_signature(signature):
    tag, body, end = _read(signature, 0)
    if tag != 0x30 or end != len(signature):
        raise KeyFormatError("bad signature")
    values = []
    position = 0
    for _ in range(2):
        tag, value, position = _read(body, position)
        if tag != 0x02 or not value or value[0] & 0x80:
            raise KeyFormatError("bad signature")
        values.append(int.from_bytes(value, "big"))
    if position != len(body):
        raise KeyFormatError("bad signature")
    return values[0], values[1]


def decode_public_key(der):
    tag, body, end = _read(der, 0)
    if tag != 0x30 or end != len(der):
        raise KeyFormatError("bad public key")
    tag, algorithm, position = _read(body, 0)
    if tag != 0x30:
        raise KeyFormatError("bad public key")
    tag, oid, inner = _read(algorithm, 0)
    if tag != 0x06 or oid != OID_EC_PUBLIC_KEY:
        raise KeyFormatError("not an EC key")
    tag, curve, _ = _read(algorithm, inner)
    if tag != 0x06 or curve != OID_PRIME256V1:
        raise KeyFormatError("unsupported curve")
    tag, bits, _ = _read(body, position)
    if tag != 0x03 or len(bits) != POINT_SIZE + 1 or bits[0] != 0 or bits[1] != 4:
        raise KeyFormatError("unsupported point encoding")
    point = bits[1:]
    x = int.from_bytes(point[1:33], "big")
    y = int.from_bytes(point[33:], "big")
    if not _on_curve(x, y):
        raise KeyFormatError("point is not on the curve")
    return bytes(point)


def key_id(point):
    return hashlib.sha512(point).digest()[:KEY_ID_SIZE]


def parse_key(data):
    try:
        text = data.decode("ascii")
        match = PEM_RE.search(text)
        if not match:
            raise KeyFormatError("no public key")
        point = decode_public_key(base64.b64decode("".join(match.group(1).split()), validate=True))
    except (UnicodeDecodeError, binascii.Error, ValueError):
        raise KeyFormatError("unusable key")
    return key_id(point), point


def load_keys(directory):
    keys = {}
    skipped = 0
    root = Path(directory)
    if not root.is_dir():
        return keys, skipped
    for entry in sorted(root.iterdir()):
        if entry.suffix.lower() != ".pem":
            continue
        try:
            if not entry.is_file() or entry.stat().st_size > MAX_KEY_FILE_SIZE:
                raise KeyFormatError("unusable file")
            identifier, point = parse_key(entry.read_bytes())
        except (KeyFormatError, OSError):
            skipped += 1
            continue
        keys[identifier] = point
    return keys, skipped


def verify(point, message, signature):
    try:
        r, s = _parse_signature(signature)
    except KeyFormatError:
        return False
    if not (1 <= r < N and 1 <= s < N) or len(point) != POINT_SIZE or point[0] != 4:
        return False
    qx = int.from_bytes(point[1:33], "big")
    qy = int.from_bytes(point[33:], "big")
    if not _on_curve(qx, qy):
        return False
    z = int.from_bytes(hashlib.sha512(message).digest()[:32], "big")
    w = _inv(s, N)
    result = _combine(z * w % N, (GX, GY, 1), r * w % N, (qx, qy, 1))
    x = _affine_x(result)
    return x is not None and x % N == r
