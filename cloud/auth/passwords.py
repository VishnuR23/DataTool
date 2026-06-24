"""Password hashing with Argon2id (spec §"Security and trust").

Argon2 is the PHC winner and the current OWASP-recommended default for password
storage. We never store or compare plaintext; ``verify_password`` is constant-time
via the library and treats any mismatch/parse error as a failed verification.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, Exception):  # noqa: BLE001 — any failure = not verified
        return False
