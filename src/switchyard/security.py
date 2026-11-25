"""GitHub webhook signatures: HMAC-SHA256 over the exact bytes received."""

from __future__ import annotations

import hashlib
import hmac

PREFIX = "sha256="


def sign(secret: str, body: bytes) -> str:
    """The header value GitHub would send for this body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{PREFIX}{digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of ``X-Hub-Signature-256``."""
    if not secret or not header or not header.startswith(PREFIX):
        return False
    expected = sign(secret, body)
    return hmac.compare_digest(expected, header)
