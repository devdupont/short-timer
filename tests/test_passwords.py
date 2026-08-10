from argon2 import PasswordHasher

from shortimer.auth import passwords
from shortimer.auth.passwords import hash_password, needs_rehash, verify_password


def test_hash_then_verify() -> None:
    stored = hash_password("correct horse battery staple")
    assert verify_password(stored, "correct horse battery staple") is True


def test_wrong_password_does_not_verify() -> None:
    stored = hash_password("correct horse battery staple")
    assert verify_password(stored, "Correct horse battery staple") is False


def test_hash_is_salted() -> None:
    """Two accounts with the same password must not share a hash."""
    assert hash_password("same-password") != hash_password("same-password")


def test_hash_does_not_contain_the_password() -> None:
    assert "hunter2" not in hash_password("hunter2")


def test_uses_argon2id_at_the_owasp_floor() -> None:
    """These are a deliberate choice, not the library's defaults."""
    stored = hash_password("whatever")
    assert stored.startswith("$argon2id$")
    assert f"m={19 * 1024}" in stored
    assert "t=2" in stored
    assert "p=1" in stored


def test_long_passwords_are_not_truncated() -> None:
    """The bcrypt failure mode this algorithm choice avoids.

    bcrypt ignores everything past 72 bytes, so two long passphrases sharing a
    prefix authenticate each other. Argon2 has no such limit.
    """
    base = "x" * 72
    stored = hash_password(base + "-alpha")
    assert verify_password(stored, base + "-beta") is False


def test_garbage_stored_hash_is_a_mismatch_not_a_crash() -> None:
    """A corrupted record must authenticate nobody, not raise."""
    assert verify_password("not-a-real-hash", "anything") is False


def test_needs_rehash_is_false_for_a_current_hash() -> None:
    assert needs_rehash(hash_password("whatever")) is False


def test_needs_rehash_is_true_for_a_weaker_hash() -> None:
    """A hash made under old parameters is upgraded at next login."""
    weak = PasswordHasher(memory_cost=8 * 1024, time_cost=1, parallelism=1)
    assert needs_rehash(weak.hash("whatever")) is True


def test_needs_rehash_tolerates_a_garbage_hash() -> None:
    assert needs_rehash("not-a-real-hash") is False


def test_verify_still_accepts_a_weaker_legacy_hash() -> None:
    """Raising the cost must not lock existing users out."""
    weak = PasswordHasher(memory_cost=8 * 1024, time_cost=1, parallelism=1)
    stored = weak.hash("whatever")
    assert verify_password(stored, "whatever") is True
    assert needs_rehash(stored) is True


def test_parameters_are_stated_not_inherited() -> None:
    """Guards against a dependency bump silently changing the cost."""
    assert passwords._MEMORY_COST_KIB == 19 * 1024
    assert passwords._TIME_COST == 2
    assert passwords._PARALLELISM == 1
