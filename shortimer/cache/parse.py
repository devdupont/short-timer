"""Shared pool of parsed workouts, keyed by normalized source text.

Parsing costs an LLM call, and the same workout text turns up across many
users — today's crossfit.com WOD most obviously, but also benchmarks and
whiteboard workouts people paste independently. Recording each distinct parse
once means the model is called once per distinct *workout*, not once per user.

The pool is deliberately immutable and separate from users' libraries. A user
who loads a workout gets their own copy to rename, cap, and edit freely; those
edits never flow back here. Copying another user's *record* would push their
customizations onto everyone else who pastes the same text — this shares only
the neutral parse.

Nothing here is private: an entry is only ever returned to someone who already
holds the identical source text.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from shortimer.cache.db import get_parse_cache_collection
from shortimer.model.workout import Workout
from shortimer.util.dedup import source_hash

logger = logging.getLogger(__name__)

#: Server-managed fields; each copy gets its own id, owner, and timestamps.
_DROPPED_FIELDS = ("id", "created_at", "updated_at", "owner_id", "source_hash")

#: Provenance, which decides how long an entry is kept.
SOURCE_CROSSFIT = "crossfit"
SOURCE_CONCEPT2 = "concept2"
SOURCE_HYBRID = "hybrid"
SOURCE_USER = "user"
#: A gym's own programming, from whichever platform it was pulled off.
#: Deliberately *not* permanent like crossfit.com: it belongs to one gym rather
#: than to a public commons, and only that gym's members ever hold the same
#: text, so the sharing win is bounded by a single gym rather than the whole
#: user base. Ageing it out costs at most one re-parse of a workout nobody has
#: looked at in a year, and means a gym's programming doesn't linger
#: indefinitely after someone leaves.
#:
#: Entries written before there was more than one gym platform carry the older
#: value "wodify". They need no migration: retention keys on *not* being in
#: `PERMANENT_SOURCES`, so both labels age out under exactly the same rule.
SOURCE_GYM = "gym"

#: Sources that publish to everyone, where the same text keeps coming back and
#: nobody's content is being held. Entries from these are kept indefinitely;
#: everything else ages out under `USER_RETENTION`. Concept2 in particular
#: re-runs its workouts across years, so an expiry would mean re-paying for a
#: parse of text we've already seen.
PERMANENT_SOURCES = frozenset({SOURCE_CROSSFIT, SOURCE_CONCEPT2, SOURCE_HYBRID})

#: How long a non-permanent entry survives. A pruned entry costs at most one
#: re-parse if that text ever shows up again.
USER_RETENTION = timedelta(days=365)


def _payload(workout: Workout) -> dict[str, object]:
    data = workout.model_dump(mode="json")
    for field in _DROPPED_FIELDS:
        data.pop(field, None)
    return data


async def remember_parse(workout: Workout, *, source: str = SOURCE_USER) -> bool:
    """Record a fresh parse so any user with the same text can reuse it."""
    if not workout.source_text:
        return False
    digest = source_hash(workout.source_text)
    set_fields: dict[str, object] = {"source_hash": digest, "parsed": _payload(workout)}
    insert_fields: dict[str, object] = {"created_at": datetime.now(UTC)}
    # Provenance only ever gets promoted: a parse from a permanent source marks
    # the entry permanent, while a user parse must not demote one that already
    # is. (Two permanent sources can't collide here — they'd have to publish
    # byte-identical text, and if they did, either label is correct.)
    if source in PERMANENT_SOURCES:
        set_fields["source"] = source
    else:
        insert_fields["source"] = source
    await get_parse_cache_collection().update_one(
        {"_id": digest},
        {"$set": set_fields, "$setOnInsert": insert_fields},
        upsert=True,
    )
    return True


async def mark_permanent(text: str, *, source: str = SOURCE_CROSSFIT) -> bool:
    """Promote an existing entry to permanent retention under `source`.

    A user may paste a workout before the daily task gets to that day. The text
    is still the feed's, so it shouldn't age out on the user retention clock —
    promote it without re-paying for a parse. An entry that's already permanent
    is left alone, so this reports whether it changed anything.
    """
    result = await get_parse_cache_collection().update_one(
        {"_id": source_hash(text), "source": {"$nin": list(PERMANENT_SOURCES)}},
        {"$set": {"source": source}},
    )
    return result.modified_count > 0


async def prune_expired_parses(*, now: datetime | None = None) -> int:
    """Drop user-submitted parses past the retention window.

    Entries from `PERMANENT_SOURCES` are exempt.
    """
    cutoff = (now or datetime.now(UTC)) - USER_RETENTION
    result = await get_parse_cache_collection().delete_many(
        {"source": {"$nin": list(PERMANENT_SOURCES)}, "created_at": {"$lt": cutoff}}
    )
    removed = int(result.deleted_count)
    if removed:
        logger.info("Pruned %d expired user parse(s).", removed)
    return removed


async def find_parse(text: str) -> Workout | None:
    """A previously-parsed workout for this text, as a fresh unsaved Workout."""
    doc = await get_parse_cache_collection().find_one({"_id": source_hash(text)})
    if doc is None:
        return None
    parsed = doc.get("parsed")
    if not isinstance(parsed, dict):
        return None
    # Rebuilt through the model so the copy gets its own id and timestamps.
    return Workout(**dict(parsed))
