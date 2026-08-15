"""Password hashing.

Argon2id, at the parameters OWASP currently gives as the floor: 19 MiB of
memory, two iterations, one lane. Memory-hardness is the whole point — it's
what makes a GPU or ASIC farm no cheaper per guess than the server that did
the hashing, which is the property bcrypt and PBKDF2 give up.

Two smaller reasons this isn't bcrypt: bcrypt silently truncates at 72 bytes,
so every call site needs a length guard it's easy to forget, and the usual
Python wrapper for it (`passlib`) hasn't shipped a release since 2020.

`needs_rehash` exists so the cost can be raised later without a reset: on a
successful login the stored hash is compared against today's parameters, and
rewritten in place if it was made under weaker ones. That's the only moment
the plaintext is available to re-hash with, so it's the only moment it can
happen.
"""

import logging

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions

logger = logging.getLogger(__name__)

#: OWASP's minimum for Argon2id, stated explicitly rather than left to the
#: library's defaults so that raising it is a visible commit rather than an
#: invisible consequence of a dependency bump.
_MEMORY_COST_KIB = 19 * 1024
_TIME_COST = 2
_PARALLELISM = 1

_hasher = PasswordHasher(
    memory_cost=_MEMORY_COST_KIB,
    time_cost=_TIME_COST,
    parallelism=_PARALLELISM,
)


def hash_password(plaintext: str) -> str:
    """Hash a password for storage. Returns a PHC string carrying its own parameters."""
    return _hasher.hash(plaintext)


def verify_password(stored_hash: str, plaintext: str) -> bool:
    """Whether the password matches, without raising on the ordinary "no" case.

    argon2-cffi signals a mismatch by exception, which reads badly at call
    sites that just want a boolean. A malformed *stored* hash is also treated
    as a mismatch rather than an error: it means a corrupted or hand-edited
    record, and the safe reading of "this doesn't parse" is "this doesn't
    authenticate anyone".
    """
    try:
        return _hasher.verify(stored_hash, plaintext)
    except argon2_exceptions.VerifyMismatchError:
        return False
    except argon2_exceptions.InvalidHashError:
        logger.warning("Stored password hash is unreadable; treating it as no match.")
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Whether this hash was made with weaker parameters than we now use."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except argon2_exceptions.InvalidHashError:
        return False
