"""
Lightweight symmetric encryption for small secrets stored at rest
(e.g. the persisted MSAL token cache in UserMailToken).

Key is derived from Django's SECRET_KEY so no new env var / secret
management is required. This is "good enough" encryption-at-rest for
a refresh-token cache in our own DB — it is NOT a substitute for a
proper KMS/secrets-manager if that's ever required by a compliance need.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    # Derive a stable 32-byte key from SECRET_KEY, base64-urlsafe encode it
    # the way Fernet expects.
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_str(plaintext: str) -> str:
    """Encrypt a string, returning a base64 token safe to store in a TextField."""
    if plaintext is None:
        return ""
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(ciphertext: str) -> str | None:
    """
    Decrypt a string previously produced by encrypt_str().
    Returns None if the value is empty or can't be decrypted
    (e.g. SECRET_KEY rotated) rather than raising — callers should
    treat None the same as "no token available".
    """
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
