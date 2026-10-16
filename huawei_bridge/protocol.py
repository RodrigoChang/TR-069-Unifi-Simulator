"""TNBU framing adapted under MIT; attribution is in THIRD_PARTY_LICENSES.md."""

import json
import secrets
import struct
import zlib
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Public protocol bootstrap constant (MD5 of 'ubnt'), NOT a private adoption key.
BOOTSTRAP_KEY = bytes.fromhex("ba86f2bbe107c7c57eb5f2690775c712")
HEADER = struct.Struct(">4sI6sH16sII")
MAX_PAYLOAD = 1024 * 1024


class ProtocolError(ValueError):
    """Invalid or unsupported wire data; messages never contain payloads or keys."""


@dataclass(frozen=True)
class Packet:
    mac: str
    body: dict
    version: int
    mode: str


def encode(
    body: dict,
    mac: str,
    key: bytes = BOOTSTRAP_KEY,
    *,
    mode: str = "cbc",
    version: int = 1,
    iv: bytes | None = None,
) -> bytes:
    if len(key) != 16 or version not in (0, 1) or mode not in ("cbc", "gcm"):
        raise ProtocolError("Unsupported key, version or encryption mode")
    try:
        raw_mac = bytes.fromhex(mac.replace(":", ""))
    except ValueError:
        raise ProtocolError("Invalid MAC") from None
    if len(raw_mac) != 6:
        raise ProtocolError("Invalid MAC")
    plain = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
    if len(plain) > MAX_PAYLOAD:
        raise ProtocolError("Payload too large")
    payload = zlib.compress(plain)
    iv = secrets.token_bytes(16) if iv is None else iv
    if len(iv) != 16:
        raise ProtocolError("Invalid IV")
    if mode == "cbc":
        padder = padding.PKCS7(128).padder()
        padded = padder.update(payload) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        payload = encryptor.update(padded) + encryptor.finalize()
        header = HEADER.pack(b"TNBU", version, raw_mac, 3, iv, 1, len(payload))
    else:
        header = HEADER.pack(b"TNBU", version, raw_mac, 11, iv, 1, len(payload) + 16)
        payload = AESGCM(key).encrypt(iv, payload, header)
    return header + payload


def decode(data: bytes, key: bytes = BOOTSTRAP_KEY) -> Packet:
    if len(data) < HEADER.size or len(data) > MAX_PAYLOAD + HEADER.size:
        raise ProtocolError("Invalid packet size")
    magic, version, mac, flags, iv, data_version, size = HEADER.unpack_from(data)
    if magic != b"TNBU" or version not in (0, 1) or data_version != 1:
        raise ProtocolError("Unsupported header")
    if size != len(data) - HEADER.size:
        raise ProtocolError("Payload length mismatch")
    if flags & ~15 or not flags & 1 or flags & 4:
        raise ProtocolError("Unsupported flags (encryption required; snappy unsupported)")
    if len(key) != 16:
        raise ProtocolError("Invalid key length")
    payload = data[HEADER.size :]
    try:
        if flags & 8:
            payload = AESGCM(key).decrypt(iv, payload, data[: HEADER.size])
        else:
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            padded = decryptor.update(payload) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            payload = unpadder.update(padded) + unpadder.finalize()
        if flags & 2:
            inflater = zlib.decompressobj()
            payload = inflater.decompress(payload, MAX_PAYLOAD + 1)
            if len(payload) > MAX_PAYLOAD or not inflater.eof or inflater.unused_data:
                raise ProtocolError("Invalid or oversized compressed payload")
        body = json.loads(payload)
        if not isinstance(body, dict):
            raise ProtocolError("Expected JSON object")
    except (ValueError, InvalidTag, zlib.error, UnicodeError, RecursionError):
        raise ProtocolError("Invalid encrypted, compressed or JSON payload") from None
    return Packet(":".join(f"{b:02x}" for b in mac), body, version, "gcm" if flags & 8 else "cbc")
