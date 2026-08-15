"""Mint a signup invite from the shell.

The admin API already does this, but it needs an admin session, and an admin
who can't sign in can't invite anyone — including a replacement admin for
themselves. This is the same operation with shell access as the credential,
which is the same trade `create_admin.py` makes.

    hatch run python scripts/create_invite.py                    # open code
    hatch run python scripts/create_invite.py friend@example.com # bound to one address
    hatch run python scripts/create_invite.py you@example.com --role admin

An address-bound invite may only be redeemed by that address, and the account
it creates is already verified. An open invite may be redeemed by anyone
holding the link, so those accounts still have to confirm their address.

The link is printed and never stored — the token lives in the database only as
a hash, so a lost link means minting a new invite, not recovering this one.

Run it against whichever database `.env` points at, and check that first: a
local `.env` has historically pointed at the production cluster.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError

from shortimer.auth.invites import create_invite, invite_link
from shortimer.cache.db import ensure_indexes, init_documents
from shortimer.config import get_settings
from shortimer.model.status import Role
from shortimer.util import email as email_module

#: The same check `RegisterRequest` applies, so a bound invite can't be issued
#: to an address the register endpoint would then refuse.
_EMAIL = TypeAdapter(EmailStr)


def _created_by() -> str:
    """What the admin screen shows as the issuer of this invite.

    `created_by` is a user id everywhere else, and there's no user here. Naming
    the shell account that ran the command is more use than a bare "script"
    when an admin later asks where an invite came from, and it can't collide
    with a real id.
    """
    try:
        return f"script:{getpass.getuser()}"
    except OSError:
        # No passwd entry and no USER in the environment — containers do this.
        return "script"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Mint a signup invite and print its URL.")
    parser.add_argument(
        "email",
        nargs="?",
        help="Bind the invite to this address. Omit for a code anyone may redeem.",
    )
    parser.add_argument(
        "--role",
        default=Role.USER.value,
        choices=[role.value for role in Role],
        help="Role the new account gets. Defaults to user.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Also email the invite. Needs an address, and EMAIL_ENABLED.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the database confirmation.",
    )
    args = parser.parse_args()

    if args.email is not None:
        try:
            _EMAIL.validate_python(args.email)
        except ValidationError as exc:
            # One line, not pydantic's full report: the caller typed one address.
            reason = exc.errors()[0]["msg"].removeprefix("value is not a valid email address: ")
            print(f"{args.email} is not an address this app can accept: {reason}", file=sys.stderr)
            return 1
    elif args.send:
        print("--send needs an address to send to.", file=sys.stderr)
        return 1

    settings = get_settings()
    # Say which database, so an accidental run against production is visible
    # before it happens rather than after. Credentials are stripped.
    host = settings.mongodb_uri.split("@")[-1]
    print(f"Database: {host} / {settings.mongodb_db_name}")
    if not args.yes and input("Mint an invite there? [y/N] ").strip().lower() != "y":
        print("Nothing was created.")
        return 1

    # Bind the document models before the first query. The app does this in its
    # lifespan, which never runs here.
    await init_documents()
    # Redemption finds the invite by the hash of the token presented, and the
    # unique index on it is what stops two invites sharing a hash. A database
    # that has never served the app has no indexes yet.
    await ensure_indexes()

    token, invite = await create_invite(
        created_by=_created_by(), email=args.email, role=Role(args.role)
    )

    if args.send:
        try:
            await email_module.send(email_module.invite_message(args.email, token))
        except email_module.EmailError as exc:
            # The invite exists and the link is printed below either way, so
            # this is a warning rather than a failure: hand the link over
            # yourself instead.
            print(f"Could not email it: {exc}", file=sys.stderr)
        else:
            sent = "Emailed" if settings.email_enabled else "Email is off; link logged instead"
            print(f"{sent} to {args.email}")

    bound = args.email if invite.email is not None else "anyone with the link"
    print(f"Invite {invite.id} for {bound} as {invite.role.value}")
    print(f"Expires {invite.expires_at:%Y-%m-%d %H:%M} UTC")
    print(invite_link(token))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
