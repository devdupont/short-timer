"""Per-user API tokens, for clients that have no session to present.

The MCP server is the one that needs this. It's a local stdio process, not an
HTTP caller — there is no browser, no cookie and nobody to redirect to a login
screen — so it authenticates with a long-lived token from its environment.
That is also what the MCP specification says a stdio server should do: the
OAuth flow it defines is for HTTP transports, and stdio servers are told to
take credentials from the environment instead.

This replaces `MCP_OWNER_ID`, which was configuration standing in for identity:
naming an owner id asserted who the server was acting as without proving it, so
anyone who could edit the environment could point it at any library. A token
has to have been *issued* to that account, and can be revoked without changing
the account it belongs to.

Stored hashed, like every other token here, and shown exactly once — at
creation. Scopes are checked per tool so a read-only integration can be exactly
that.
"""

import logging
from datetime import UTC, datetime

from shortimer.auth.tokens import hash_token, new_token, token_prefix
from shortimer.model.token import ApiToken, ApiTokenScope

logger = logging.getLogger(__name__)

#: Marks a string as one of ours. Costs nothing, and means a token pasted into
#: a config file or a commit is recognisable — to a secret scanner, and to a
#: person wondering what they're looking at.
_PREFIX = "st_"


async def create_token(
    *, user_id: str, name: str, scopes: list[ApiTokenScope]
) -> tuple[str, ApiToken]:
    """Mint a token. Returns the raw value and the record describing it.

    The raw value is unrecoverable afterwards, so the caller's response is the
    only chance to show it. `token_hash` is written in a second step, straight
    to the collection, because it deliberately isn't a field on `ApiToken` —
    see the model's docstring.
    """
    raw = f"{_PREFIX}{new_token()}"
    token = ApiToken(
        user_id=user_id,
        name=name.strip() or "Unnamed token",
        scopes=scopes,
        prefix=token_prefix(raw, len(_PREFIX) + 6),
    )
    await token.insert()
    await ApiToken.get_pymongo_collection().update_one(
        {"_id": token.id}, {"$set": {"token_hash": hash_token(raw)}}
    )
    logger.info("Issued API token %s for %s.", token.id, user_id)
    return raw, token


async def resolve_token(raw: str) -> ApiToken | None:
    """The token a string names, or None.

    Records the use, best-effort: a "last used" column is how someone decides
    which of four tokens is safe to revoke, and failing the *call* because the
    bookkeeping write failed would be the wrong trade.
    """
    doc = await ApiToken.get_pymongo_collection().find_one({"token_hash": hash_token(raw)})
    if doc is None:
        return None

    token: ApiToken = ApiToken.model_validate({**doc, "id": doc["_id"]})
    try:
        await ApiToken.find_one(ApiToken.id == token.id).update(
            {"$set": {"last_used_at": datetime.now(UTC)}}
        )
    except Exception:
        logger.warning("Could not record use of API token %s.", token.id)
    return token


async def list_tokens(user_id: str) -> list[ApiToken]:
    """A user's issued tokens, newest first."""
    return await ApiToken.find(ApiToken.user_id == user_id).sort("-created_at").to_list()


async def revoke_token(user_id: str, token_id: str) -> bool:
    """Delete one of this user's tokens. False if it isn't theirs.

    Scoped to the owner in the *query* rather than checked after loading it,
    so there's no path where a mistake reads one user's token id and deletes
    another's.
    """
    result = await ApiToken.find_one(ApiToken.id == token_id, ApiToken.user_id == user_id).delete()
    return result is not None and result.deleted_count == 1
