"""Create the first admin account.

Registration is invite-only and invites are minted by admins, so a fresh
database has a chicken-and-egg problem: nobody can invite the first person.
This breaks it, and it is the only way to create an account without an invite.

    hatch run python scripts/create_admin.py you@example.com

The password is read from the terminal without echoing, never from an
argument — anything on a command line lands in shell history and in the process
list, where other users on the box can read it.

Run it against whichever database `.env` points at, and check that first: a
local `.env` has historically pointed at the production cluster.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from short_timer.config import get_settings
from short_timer.db import ensure_indexes
from short_timer.models import Role
from short_timer.users import EmailAlreadyRegisteredError, create_user, get_user_by_email


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first admin account.")
    parser.add_argument("email")
    parser.add_argument("--name", default="", help="Display name.")
    parser.add_argument(
        "--role",
        default=Role.ADMIN.value,
        choices=[role.value for role in Role],
        help="Defaults to admin, which is the point of this script.",
    )
    args = parser.parse_args()

    settings = get_settings()
    # Say which database, so an accidental run against production is visible
    # before it happens rather than after. Credentials are stripped.
    host = settings.mongodb_uri.split("@")[-1]
    print(f"Database: {host} / {settings.mongodb_db_name}")
    if input("Create an account there? [y/N] ").strip().lower() != "y":
        print("Nothing was created.")
        return 1

    if await get_user_by_email(args.email) is not None:
        print(f"{args.email} already has an account.", file=sys.stderr)
        return 1

    password = getpass.getpass("Password: ")
    if len(password) < settings.password_min_length:
        print(
            f"Password must be at least {settings.password_min_length} characters.",
            file=sys.stderr,
        )
        return 1
    if password != getpass.getpass("Confirm: "):
        print("Passwords did not match.", file=sys.stderr)
        return 1

    # The unique index on `email` is what stops a duplicate account, so make
    # sure it exists before relying on it — a database that has never served
    # the app has no indexes yet.
    await ensure_indexes()

    try:
        user = await create_user(
            email=args.email,
            password=password,
            display_name=args.name,
            role=Role(args.role),
            # Nobody emailed this address a link, but whoever ran this command
            # had shell access to the server, which is a stronger claim.
            email_verified=True,
        )
    except EmailAlreadyRegisteredError:
        print(f"{args.email} already has an account.", file=sys.stderr)
        return 1

    print(f"Created {user.email} ({user.role.value}), id {user.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
