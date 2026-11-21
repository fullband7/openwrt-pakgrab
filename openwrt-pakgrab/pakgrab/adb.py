import hashlib
import struct
import zlib

from . import ecdsa
from .index import FILENAME_RE_APK, NAME_RE, Package

MAGIC = b"ADB"
SCHEMA_INDEX = 0x78646E69
BLOCK_ADB = 0
BLOCK_SIG = 1
BLOCK_DATA = 2
BLOCK_EXT = 3
DIGEST_SHA256 = 3
DIGEST_SHA512 = 4
DIGEST_SHA256_160 = 5
COMPRESSION_NONE = 0
COMPRESSION_DEFLATE = 1
DEP_CONFLICT = 16
TYPE_MASK = 0xF0000000
VALUE_MASK = 0x0FFFFFFF
TYPE_INT = 0x10000000
TYPE_INT_32 = 0x20000000
TYPE_INT_64 = 0x30000000
TYPE_BLOB_8 = 0x80000000
TYPE_BLOB_16 = 0x90000000
TYPE_BLOB_32 = 0xA0000000
TYPE_ARRAY = 0xD0000000
TYPE_OBJECT = 0xE0000000
SIGNATURE_PREFIX = 18
DEFAULT_NAME_SPEC = "${name}-${version}.apk"
CHUNK_SIZE = 65536
MAX_PACKAGE_COUNT = 500000


class AdbError(Exception):
    pass


class SignatureError(AdbError):
    pass


def _decompress(data, limit):
    decoder = zlib.decompressobj(-15)
    try:
        result = decoder.decompress(data, limit + 1)
    except zlib.error:
        raise AdbError("corrupt compressed data")
    if len(result) > limit or decoder.unconsumed_tail:
        raise AdbError("decompressed data too large")
    if not decoder.eof:
        raise AdbError("truncated compressed data")
    return result


def unpack(raw, limit):
    if len(raw) < 4 or raw[:3] != MAGIC:
        raise AdbError("invalid ADB header")
    flavor = raw[3:4]
    if flavor == b".":
        stream = raw
    elif flavor == b"d":
        stream = _decompress(raw[4:], limit)
    elif flavor == b"c":
        if len(raw) < 6 or raw[4] != COMPRESSION_DEFLATE:
            raise AdbError("unsupported ADB compression")
        stream = _decompress(raw[6:], limit)
    else:
        raise AdbError("invalid ADB header")
    if len(stream) < 8 or stream[:4] != b"ADB.":
        raise AdbError("invalid ADB header")
    return stream


def _block_header(stream, position):
    if position + 4 > len(stream):
        raise AdbError("truncated ADB block")
    word = struct.unpack_from("<I", stream, position)[0]
    if word >> 30 == BLOCK_EXT:
        if position + 16 > len(stream):
            raise AdbError("truncated ADB block")
        return word & 0x3FFFFFFF, struct.unpack_from("<Q", stream, position + 8)[0], 16
    return word >> 30, word & 0x3FFFFFFF, 4


def blocks(stream):
    position = 8
    first = True
    while position < len(stream):
        kind, raw_size, header = _block_header(stream, position)
        if raw_size < header or position + raw_size > len(stream):
            raise AdbError("invalid ADB block")
        if (first and kind != BLOCK_ADB) or (not first and kind == BLOCK_ADB) or kind > BLOCK_DATA:
            raise AdbError("unexpected ADB block")
        yield kind, stream[position + header:position + raw_size]
        first = False
        position += (raw_size + 7) & ~7


def digest(data, algorithm):
    if algorithm == DIGEST_SHA256:
        return hashlib.sha256(data).digest()
    if algorithm == DIGEST_SHA512:
        return hashlib.sha512(data).digest()
    if algorithm == DIGEST_SHA256_160:
        return hashlib.sha256(data).digest()[:20]
    raise AdbError("unsupported digest")


def verify_signatures(schema, payload, signatures, keys):
    seen = False
    for signature in signatures:
        if len(signature) < SIGNATURE_PREFIX or signature[0] != 0:
            continue
        point = keys.get(bytes(signature[2:SIGNATURE_PREFIX]))
        if point is None:
            continue
        seen = True
        try:
            message = struct.pack("<I", schema) + bytes(signature[:SIGNATURE_PREFIX]) + digest(payload, signature[1])
        except AdbError:
            continue
        if ecdsa.verify(point, message, bytes(signature[SIGNATURE_PREFIX:])):
            return
    if seen:
        raise SignatureError("UNTRUSTED signature (bad signature)")
    raise SignatureError("UNTRUSTED signature (no trusted key matches)")


def open_index(raw, keys, limit):
    stream = unpack(raw, limit)
    schema = struct.unpack_from("<I", stream, 4)[0]
    if schema != SCHEMA_INDEX:
        raise AdbError("not a package index")
    payload = None
    signatures = []
    for kind, body in blocks(stream):
        if kind == BLOCK_ADB:
            payload = body
        elif kind == BLOCK_SIG:
            signatures.append(body)
    if payload is None or len(payload) < 8 or payload[0] != 0:
        raise AdbError("invalid ADB payload")
    verify_signatures(schema, payload, signatures, keys)
    return payload


class _Reader:
    def __init__(self, payload):
        self.payload = payload
        self.size = len(payload)

    def _offset(self, value, length):
        offset = value & VALUE_MASK
        if offset + length > self.size:
            raise AdbError("ADB value out of range")
        return offset

    def number(self, value):
        kind = value & TYPE_MASK
        if kind == TYPE_INT:
            return value & VALUE_MASK
        if kind == TYPE_INT_32:
            return struct.unpack_from("<I", self.payload, self._offset(value, 4))[0]
        if kind == TYPE_INT_64:
            return struct.unpack_from("<Q", self.payload, self._offset(value, 8))[0]
        return 0

    def blob(self, value):
        kind = value & TYPE_MASK
        if kind == TYPE_BLOB_8:
            offset = self._offset(value, 1)
            length, header = self.payload[offset], 1
        elif kind == TYPE_BLOB_16:
            offset = self._offset(value, 2)
            length, header = struct.unpack_from("<H", self.payload, offset)[0], 2
        elif kind == TYPE_BLOB_32:
            offset = self._offset(value, 4)
            length, header = struct.unpack_from("<I", self.payload, offset)[0], 4
        else:
            return b""
        start = offset + header
        if start + length > self.size:
            raise AdbError("ADB value out of range")
        return self.payload[start:start + length]

    def text(self, value):
        return self.blob(value).decode("utf-8", "replace")

    def entries(self, value):
        kind = value & TYPE_MASK
        if kind != TYPE_ARRAY and kind != TYPE_OBJECT:
            return ()
        offset = self._offset(value, 4)
        count = struct.unpack_from("<I", self.payload, offset)[0]
        if count < 2:
            return ()
        self._offset(value, 4 * count)
        return struct.unpack_from("<%dI" % (count - 1), self.payload, offset + 4)


def _field(entries, number):
    return entries[number - 1] if number <= len(entries) else 0


def _dependencies(reader, value):
    names = []
    for item in reader.entries(value):
        fields = reader.entries(item)
        if reader.number(_field(fields, 3)) & DEP_CONFLICT:
            continue
        name = reader.text(_field(fields, 1))
        if name:
            names.append(name)
    return tuple(names)


def _filename(spec, name, version, arch, identity):
    if "://" in spec:
        return ""
    values = {"name": name, "version": version, "arch": arch, "hash": identity}
    result = spec
    for key, value in values.items():
        result = result.replace("${%s}" % key, value)
    return result if FILENAME_RE_APK.match(result) else ""


def parse_index(raw, keys, feed, limit):
    payload = open_index(raw, keys, limit)
    reader = _Reader(payload)
    root = struct.unpack_from("<I", payload, 4)[0]
    fields = reader.entries(root)
    spec = reader.text(_field(fields, 3)) or DEFAULT_NAME_SPEC
    items = reader.entries(_field(fields, 2))
    if len(items) > MAX_PACKAGE_COUNT:
        raise AdbError("too many packages in index")
    packages = []
    for item in items:
        info = reader.entries(item)
        name = reader.text(_field(info, 1))
        version = reader.text(_field(info, 2))
        if not NAME_RE.match(name) or not version:
            continue
        identity = reader.blob(_field(info, 3)).hex()
        arch = reader.text(_field(info, 5))
        provides = _dependencies(reader, _field(info, 16))
        packages.append(Package(
            name=name,
            version=version,
            depends=tuple((dependency,) for dependency in _dependencies(reader, _field(info, 15))),
            provides=provides,
            filename=_filename(spec, name, version, arch, identity),
            size=reader.number(_field(info, 13)),
            sha256="",
            architecture=arch,
            section="",
            description=reader.text(_field(info, 4)),
            feed=feed,
            identity=identity,
        ))
    return packages, reader.text(_field(fields, 1))


def verify_package_file(path, identity, limit):
    expected = bytes.fromhex(identity)
    algorithm = {32: DIGEST_SHA256, 64: DIGEST_SHA512}.get(len(expected))
    if algorithm is None:
        return
    with open(path, "rb") as handle:
        head = handle.read(4)
        decoder = None
        if head == b"ADBd":
            decoder = zlib.decompressobj(-15)
        elif head == b"ADBc":
            spec = handle.read(2)
            if len(spec) != 2 or spec[0] != COMPRESSION_DEFLATE:
                raise AdbError("unsupported ADB compression")
            decoder = zlib.decompressobj(-15)
        elif head != b"ADB.":
            raise AdbError("invalid ADB header")
        buffer = bytearray(head if decoder is None else b"")
        while True:
            if len(buffer) >= 24:
                kind, raw_size, header = _block_header(buffer, 8)
                if kind != BLOCK_ADB or raw_size < header or raw_size > limit:
                    raise AdbError("invalid ADB block")
                end = 8 + raw_size
                if len(buffer) >= end:
                    break
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                raise AdbError("truncated ADB data")
            if decoder is None:
                buffer.extend(chunk)
                continue
            try:
                while chunk:
                    buffer.extend(decoder.decompress(chunk, CHUNK_SIZE * 16))
                    if len(buffer) > limit + 16:
                        raise AdbError("ADB block too large")
                    chunk = decoder.unconsumed_tail
            except zlib.error:
                raise AdbError("corrupt compressed data")
    if digest(bytes(buffer[8 + header:end]), algorithm) != expected:
        raise AdbError("package integrity check failed")
