"""Optional local Ed25519 identities for simulated node messages."""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .models import NetworkMessage


def deterministic_private_key(seed: int, node_id: str) -> Ed25519PrivateKey:
    material = hashlib.sha256(f"dnhacks26:{seed}:{node_id}".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(material)


def public_key_text(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()


class MessageAuthenticator:
    def __init__(
        self,
        public_keys: dict[str, str],
        private_key: Ed25519PrivateKey | None = None,
        allow_unsigned_senders: set[str] | None = None,
    ) -> None:
        self.public_keys = public_keys
        self.private_key = private_key
        self.allow_unsigned_senders = allow_unsigned_senders or {"mission-control"}

    @staticmethod
    def canonical(message: NetworkMessage) -> bytes:
        return json.dumps(
            message.model_dump(mode="json", exclude={"signature"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def sign(self, message: NetworkMessage) -> NetworkMessage:
        if self.private_key is None:
            raise RuntimeError("private key unavailable")
        signature = base64.b64encode(self.private_key.sign(self.canonical(message))).decode()
        return message.model_copy(update={"signature": signature})

    def verify(self, message: NetworkMessage) -> bool:
        if message.sender_id in self.allow_unsigned_senders and not message.signature:
            return True
        encoded_key = self.public_keys.get(message.sender_id)
        if not encoded_key or not message.signature:
            return False
        try:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key))
            key.verify(base64.b64decode(message.signature), self.canonical(message))
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True
