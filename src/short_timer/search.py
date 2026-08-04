"""Server-side search and filtering over the saved workout library.

Queries match a `search_text` field derived at write time rather than the
fields it came from. Two reasons: the searchable content is spread across
nested arrays (`segments[].movements[].name`), and a workout's mode is stored
as an enum value (`amrap`) while what a user types is its label (`AMRAP`).
Flattening both into one lowercased string at write time turns any query into
a single substring match against a single field.
"""

from __future__ import annotations

import re
from typing import Any

from short_timer.models import MODE_LABELS, Workout, WorkoutMode

#: Ceiling on the terms one query may contain. Each term costs its own
#: unindexed pass over the owner's rows, so an unbounded count of them is an
#: unbounded scan.
MAX_TERMS = 8


def search_text(workout: Workout) -> str:
    """The lowercased haystack that queries are matched against.

    Deliberately excludes `source_text`: the raw paste repeats the movement
    names already indexed here, and including it would make every workout
    imported from the same gym's template match on that boilerplate.
    """
    parts = [workout.name, workout.category or "", MODE_LABELS[workout.mode]]
    for segment in workout.segments:
        if segment.label:
            parts.append(segment.label)
        parts.extend(m.name for m in segment.movements if m.name)
    return " ".join(parts).lower()


def library_query(
    owner_id: str,
    *,
    q: str | None = None,
    mode: WorkoutMode | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Build the Mongo filter behind one owner's library view.

    Owner scoping is not optional and not a caller's choice — it's the first
    key in every query this module produces.
    """
    query: dict[str, Any] = {"owner_id": owner_id}
    if mode is not None:
        query["mode"] = mode.value
    if category:
        query["category"] = category

    terms = (q or "").lower().split()[:MAX_TERMS]
    if terms:
        # Every term has to appear, in any order, anywhere in the haystack —
        # the same "all terms match" rule the library has always used.
        # Escaped because the query is user input, and an unescaped `(a+)+`
        # is a denial of service rather than a search.
        query["$and"] = [{"search_text": {"$regex": re.escape(term)}} for term in terms]
    return query
