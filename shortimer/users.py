"""Accounts and the per-user configuration hanging off them.

A user record is what owns workouts (via `owner_id`), holds per-user
integration credentials, and carries the role every privileged check reads.
Creating one is the only way an owner id comes into existence — there is no
default account and no way to arrive at an id that has no record behind it.

The dependencies at the bottom (`current_user`, `require_role`) live here
rather than in `auth.py` because they need records, and `auth.py` deliberately
knows only about session tokens. That keeps the import direction one-way.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError

from shortimer.auth.passwords import hash_password, needs_rehash
from shortimer.auth.session import current_owner
from shortimer.cache.crypto import SecretBox, SecretStatus, encrypt, is_configured
from shortimer.model.feed import normalize_feeds
from shortimer.model.gym import GymConnection, GymConnectionUpdate, GymConnectionView, GymProvider
from shortimer.model.status import AccountStatus, Role
from shortimer.model.user import MeResponse, User, UserConfig, UserConfigUpdate, UserConfigView

logger = logging.getLogger(__name__)


def normalize_email(email: str) -> str:
    """The stored form of an address.

    Case-folded and trimmed, so uniqueness is enforced by the index rather
    than by every caller remembering to lowercase first. Nothing more
    aggressive than that: stripping dots or `+tags` is a Gmail-specific rule,
    and applying it to every provider would silently merge addresses that
    really are different people.
    """
    return email.strip().lower()


async def get_user(user_id: str) -> User | None:
    """The user with this id, or None."""
    return await User.get(user_id)


async def get_user_by_email(email: str) -> User | None:
    """The user with this address, or None. Case-insensitive, via `normalize_email`."""
    return await User.find_one(User.email == normalize_email(email))


# --- Dependencies -----------------------------------------------------------
# These live here rather than in `auth.py` because they need user *records*,
# and `auth.py` deliberately knows only about session tokens. Keeping the
# import one-way (users -> auth) is what stops the two becoming a cycle.


async def current_user(owner_id: Annotated[str, Depends(current_owner)]) -> User:
    """The signed-in account.

    A valid session for an account that no longer exists is treated as no
    session at all: 401, not 404, because from the caller's point of view
    their credential simply doesn't authenticate anything any more.
    """
    user = await get_user(owner_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if user.status is not AccountStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    return user


def require_role(*allowed: Role) -> Callable[[User], Awaitable[User]]:
    """Dependency factory gating a route on the caller's role.

    Returns 404 rather than 403 on refusal. An endpoint you may not use
    shouldn't confirm that it exists — that's the existing reasoning behind
    the operator metrics gate, kept here so every privileged surface answers
    the same way.
    """
    permitted = frozenset(allowed)

    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        """The signed-in user, if their role is one of `permitted`."""
        if user.role not in permitted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return user

    return dependency


# --- Creating and changing accounts -----------------------------------------


class EmailAlreadyRegisteredError(RuntimeError):
    """Raised when an address already has an account."""


async def create_user(
    *,
    email: str,
    password: str | None,
    display_name: str = "",
    role: Role = Role.USER,
    email_verified: bool = False,
) -> User:
    """Create an account, or raise if the address is taken.

    The uniqueness check is the *index*, not a prior read: two registrations
    racing on the same address would both find it free and both insert. Letting
    the write fail and translating the error is the only version of this that
    is actually atomic.

    `password` may be None for an account that will only ever sign in with a
    passkey.
    """
    user = User(
        email=normalize_email(email),
        email_verified=email_verified,
        password_hash=hash_password(password) if password is not None else None,
        role=role,
        display_name=display_name.strip(),
    )
    try:
        await user.insert()
    except DuplicateKeyError as exc:
        raise EmailAlreadyRegisteredError(email) from exc
    logger.info("Created account %s with role %s.", user.id, user.role.value)
    return user


async def set_password(user_id: str, password: str) -> None:
    """Hash and store a new password for this user."""
    await _update_fields(user_id, {"password_hash": hash_password(password)})


async def mark_email_verified(user_id: str) -> None:
    """Flip `email_verified` on for this user."""
    await _update_fields(user_id, {"email_verified": True})


async def rehash_if_needed(user: User, password: str) -> None:
    """Upgrade a stored hash to current parameters, on a successful login.

    This is the only moment the plaintext exists to re-hash with, which is why
    raising the work factor has to be done here rather than in a migration.
    """
    if user.password_hash is None or not needs_rehash(user.password_hash):
        return
    await set_password(user.id, password)
    logger.info("Upgraded the password hash for %s to current parameters.", user.id)


async def _update_fields(user_id: str, fields: dict[str, Any]) -> None:
    """`$set` `fields` on a user, stamping `updated_at` alongside them."""
    await User.find_one(User.id == user_id).update(
        {"$set": {**fields, "updated_at": datetime.now(UTC)}}
    )


# --- Reading config out to a client -----------------------------------------


def _status(box: SecretBox | None) -> SecretStatus:
    """Describe a stored credential without decrypting it."""
    if box is None:
        return SecretStatus(is_set=False)
    return SecretStatus(is_set=True, masked=box.masked())


def _connection_view(connection: GymConnection) -> GymConnectionView:
    """One stored connection with its credential reduced to a `SecretStatus`."""
    return GymConnectionView(
        provider=connection.provider,
        credential=_status(connection.credential),
        location=connection.location,
        program=connection.program,
        enabled=connection.enabled,
    )


def to_view(config: UserConfig) -> UserConfigView:
    """Config with every credential reduced to set/not-set plus a mask."""
    return UserConfigView(
        gyms=[_connection_view(connection) for connection in config.gyms],
        # Normalized on the way out so a record written before a feed kind
        # existed still reports a complete list.
        feeds=normalize_feeds(config.feeds),
    )


def to_me(user: User) -> MeResponse:
    """A user as `GET /api/me` returns them: credentials masked, config viewed."""
    return MeResponse(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        role=user.role,
        display_name=user.display_name,
        config=to_view(user.config),
        secrets_available=is_configured(),
    )


# --- Applying a requested change --------------------------------------------


def _apply_secret(current: SecretBox | None, submitted: str | None) -> SecretBox | None:
    """Resolve the three-way credential update: keep, clear, or replace.

    Absent (None) keeps what's stored, so a client can toggle `enabled` or fix
    a typo'd location without holding the credential to send back. An explicit
    empty string clears it. Anything else is encrypted and replaces it.
    """
    if submitted is None:
        return current
    if not submitted.strip():
        return None
    return encrypt(submitted.strip())


def _apply_text(current: str | None, submitted: str | None) -> str | None:
    """Same keep/clear/replace rule as credentials, for plain fields."""
    if submitted is None:
        return current
    return submitted.strip() or None


def _apply_connection(
    config: UserConfig, provider: GymProvider, update: GymConnectionUpdate
) -> None:
    """Merge one provider's change into `config.gyms`, in place.

    A connection is created on first write and *removed* once it holds nothing
    worth keeping — clearing the credential on a connection the user has also
    switched off is how you disconnect a gym, and leaving an empty husk behind
    would make the settings screen claim a connection that can never fetch.
    """
    current = config.connection(provider) or GymConnection(provider=provider)
    merged = GymConnection(
        provider=provider,
        credential=_apply_secret(current.credential, update.credential),
        location=_apply_text(current.location, update.location),
        program=_apply_text(current.program, update.program),
        enabled=current.enabled if update.enabled is None else update.enabled,
    )
    config.gyms = [c for c in config.gyms if c.provider != provider]
    if merged.credential is not None or merged.location or merged.program:
        config.gyms.append(merged)


async def update_config(user: User, update: UserConfigUpdate) -> User:
    """Merge a partial config change into the user's stored config."""
    config = user.config.model_copy(deep=True)

    if update.gyms is not None:
        for provider, connection_update in update.gyms.items():
            _apply_connection(config, provider, connection_update)

    if update.feeds is not None:
        config.feeds = normalize_feeds(update.feeds)

    updated: User = user.model_copy(update={"config": config, "updated_at": datetime.now(UTC)})
    await User.find_one(User.id == updated.id).update(
        {"$set": {"config": config.model_dump(mode="json"), "updated_at": updated.updated_at}}
    )
    return updated
