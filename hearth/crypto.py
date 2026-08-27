"""Group password stretching, device identity, and message authentication."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def _b64(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    import base64

    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def generate_group_password() -> str:
    """Windows HomeGroup-style readable password."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    chunks = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(chunks)


def derive_group_key(password: str, group_id: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=210_000)
    material = f"{group_id}:{password}".encode("utf-8")
    return kdf.derive(material)


def derive_token_key(group_key: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"hearth-token", info=b"session").derive(group_key)


def hmac_hex(key: bytes, message: bytes) -> str:
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_hmac(key: bytes, message: bytes, digest: str) -> bool:
    expected = hmac_hex(key, message)
    return hmac.compare_digest(expected, digest)


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> str:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return _b64(nonce + ct)


def decrypt(key: bytes, blob: str, aad: bytes = b"") -> bytes:
    raw = _unb64(blob)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, aad)


@dataclass
class DeviceIdentity:
    private_key: Ed25519PrivateKey
    public_pem: bytes
    device_id: str

    @classmethod
    def create(cls) -> "DeviceIdentity":
        key = Ed25519PrivateKey.generate()
        pub = key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return cls(private_key=key, public_pem=pub, device_id=_b64(pub)[:22])

    @classmethod
    def from_bytes(cls, raw: bytes) -> "DeviceIdentity":
        key = Ed25519PrivateKey.from_private_bytes(raw)
        pub = key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return cls(private_key=key, public_pem=pub, device_id=_b64(pub)[:22])

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def public_b64(self) -> str:
        return _b64(self.public_pem)

    def sign(self, message: bytes) -> str:
        return _b64(self.private_key.sign(message))

    @staticmethod
    def verify(public_b64: str, message: bytes, signature: str) -> bool:
        try:
            pub = Ed25519PublicKey.from_public_bytes(_unb64(public_b64))
            pub.verify(_unb64(signature), message)
            return True
        except Exception:
            return False
