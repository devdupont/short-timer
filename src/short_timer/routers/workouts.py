import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from short_timer.auth import current_owner, require_session
from short_timer.benchmarks import benchmark_workouts
from short_timer.db import get_workouts_collection
from short_timer.dedup import source_hash
from short_timer.llm import WorkoutParseError, parse_workout_text
from short_timer.metrics import (
    ParseOutcome,
    record_parse,
    record_workout_completed,
    record_workout_started,
)
from short_timer.models import (
    SeedResponse,
    Workout,
    WorkoutCompletedRequest,
    WorkoutCreateRequest,
    WorkoutMode,
    WorkoutPage,
    WorkoutParseRequest,
)
from short_timer.parse_cache import find_parse, remember_parse
from short_timer.ratelimit import (
    enforce,
    llm_global_limit,
    llm_subject_limit,
    subject_for,
    writes_allowed,
)

router = APIRouter(
    prefix="/api/workouts", tags=["workouts"], dependencies=[Depends(require_session)]
)

#: Default page size for the library listing, and the ceiling a client may ask
#: for. Small enough that the first page is quick on a phone, large enough that
#: most libraries are one or two pages.
_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100

#: How each mode reads in the UI, so searching "for time" or "amrap" finds the
#: workouts the user sees labelled that way. Mirrors `MODE_LABELS` in
#: `web/src/types.ts`; the stored value ("for_time") is matched too.
_MODE_LABELS = {
    WorkoutMode.FOR_TIME: "For Time",
    WorkoutMode.AMRAP: "AMRAP",
    WorkoutMode.EMOM: "EMOM",
    WorkoutMode.TABATA: "Tabata",
    WorkoutMode.INTERVAL: "Interval",
    WorkoutMode.CUSTOM: "Custom",
}

#: The fields a library search looks at — the same ones the row displays, plus
#: what's inside it, so "thruster" finds Fran.
_SEARCH_FIELDS = (
    "name",
    "description",
    "category",
    "segments.label",
    "segments.movements.name",
)


def _term_clause(term: str) -> dict[str, Any]:
    """Match a single search term against any searchable field."""
    # Escaped: the term is user input, and an unescaped regex is both a way to
    # match things the user didn't mean and a way to hand Mongo a pathological
    # pattern.
    pattern = re.escape(term)
    clauses: list[dict[str, Any]] = [
        {field: {"$regex": pattern, "$options": "i"}} for field in _SEARCH_FIELDS
    ]
    modes = [
        mode.value
        for mode, label in _MODE_LABELS.items()
        if term in label.lower() or term in mode.value
    ]
    if modes:
        clauses.append({"mode": {"$in": modes}})
    return {"$or": clauses}


def _library_filter(
    owner_id: str,
    query: str | None,
    mode: WorkoutMode | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Build the Mongo filter for one owner's library, narrowed by the view.

    Terms are AND-ed: "amrap cindy" means both, in any field, matching how the
    search box behaved when it filtered an already-loaded list. `mode` and
    `category` narrow further and are exact — a dropdown picks a value that
    exists, so there's nothing to fuzzy-match.
    """
    mongo_filter: dict[str, Any] = {"owner_id": owner_id}
    if mode is not None:
        mongo_filter["mode"] = mode.value
    if category:
        mongo_filter["category"] = category
    terms = (query or "").lower().split()
    if terms:
        mongo_filter["$and"] = [_term_clause(term) for term in terms]
    return mongo_filter


def _to_document(workout: Workout, owner_id: str) -> dict[str, Any]:
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    # Derive the dedup hash from source_text at write time so it's always
    # consistent, regardless of how the model instance was constructed.
    doc["source_hash"] = source_hash(workout.source_text) if workout.source_text else None
    # Ownership comes from the session, never from the request body.
    doc["owner_id"] = owner_id
    return doc


def _from_document(doc: dict[str, Any]) -> Workout:
    doc = dict(doc)
    doc["id"] = doc.pop("_id")
    return Workout(**doc)


async def _find_by_source_text(text: str, owner_id: str) -> Workout | None:
    """Return this owner's previously-saved workout with matching text, if any."""
    doc = await get_workouts_collection().find_one(
        {"owner_id": owner_id, "source_hash": source_hash(text)}
    )
    return _from_document(doc) if doc is not None else None


async def _guard_llm_call(request: Request, owner_id: str) -> None:
    """Charge a caller (and the deployment) for an impending model call.

    Only applied on a cache miss — reusing an existing parse is free, so it
    shouldn't count against anyone's budget.
    """
    subject = subject_for(request, owner_id)
    await enforce(llm_subject_limit(), subject)
    # Backstop on total spend, independent of how many callers there are.
    await enforce(llm_global_limit(), "all")


async def _parse_or_cached(
    text: str, name_hint: str | None, owner_id: str, request: Request
) -> Workout:
    """Parse `text` into a Workout, reusing any existing parse to avoid an LLM call.

    Checked in order: this owner's own library, then the shared pool of parses
    anyone has already paid for (including pre-warmed crossfit.com WODs), and
    only then the model. A fresh parse is added to the pool so the next user
    with the same text gets it for free.
    """
    cached = await _find_by_source_text(text, owner_id)
    if cached is not None:
        await record_parse(outcome=ParseOutcome.LIBRARY_HIT, owner_id=owner_id)
        return cached
    shared = await find_parse(text)
    if shared is not None:
        # The interesting one: somebody else already paid for this parse. It's
        # the only direct evidence that pooling parses across users is worth
        # the complexity it costs.
        await record_parse(outcome=ParseOutcome.POOL_HIT, owner_id=owner_id)
        return shared
    await _guard_llm_call(request, owner_id)
    try:
        workout = await parse_workout_text(text, name_hint=name_hint, owner_id=owner_id)
    except WorkoutParseError as exc:
        await record_parse(outcome=ParseOutcome.FAILED, owner_id=owner_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await record_parse(outcome=ParseOutcome.MODEL_CALL, owner_id=owner_id)
    await remember_parse(workout)
    return workout


@router.post("/parse", response_model=Workout)
async def parse_workout(
    body: WorkoutParseRequest,
    request: Request,
    owner_id: str = Depends(current_owner),
) -> Workout:
    """Parse pasted text into a preview Workout (not saved)."""
    return await _parse_or_cached(body.text, body.name_hint, owner_id, request)


@router.post("/from-text", response_model=Workout, status_code=status.HTTP_201_CREATED)
async def create_from_text(
    body: WorkoutParseRequest,
    request: Request,
    owner_id: str = Depends(writes_allowed),
) -> Workout:
    """Get-or-create: return a saved workout matching the text, else parse and save it."""
    existing = await _find_by_source_text(body.text, owner_id)
    if existing is not None:
        return existing
    workout = await _parse_or_cached(body.text, body.name_hint, owner_id, request)
    await get_workouts_collection().insert_one(_to_document(workout, owner_id))
    return workout


@router.post("/seed", response_model=SeedResponse)
async def seed_benchmarks(owner_id: str = Depends(writes_allowed)) -> SeedResponse:
    """Add the classic benchmark workouts, skipping any already in the library."""
    collection = get_workouts_collection()
    added = 0
    skipped = 0
    for workout in benchmark_workouts():
        assert workout.source_text is not None  # every seeded workout carries its text
        existing = await collection.find_one(
            {"owner_id": owner_id, "source_hash": source_hash(workout.source_text)}
        )
        if existing is not None:
            skipped += 1
            continue
        await collection.insert_one(_to_document(workout, owner_id))
        added += 1
    return SeedResponse(added=added, skipped=skipped)


@router.post("", response_model=Workout, status_code=status.HTTP_201_CREATED)
async def create_workout(
    body: WorkoutCreateRequest, owner_id: str = Depends(writes_allowed)
) -> Workout:
    collection = get_workouts_collection()
    # Upsert by source text: if this workout was already saved (same text),
    # return the existing record instead of creating a duplicate.
    if body.workout.source_text:
        existing = await collection.find_one(
            {"owner_id": owner_id, "source_hash": source_hash(body.workout.source_text)}
        )
        if existing is not None:
            return _from_document(existing)
    # The id is server-assigned, like `owner_id`. Taking it from the body lets
    # a caller name another owner's key: the insert fails on the duplicate
    # `_id` rather than overwriting it, but that failure is itself an oracle
    # for which ids exist, and it surfaces as "the database is unavailable" —
    # a client-caused error that reads like an outage.
    workout = body.workout.model_copy(update={"id": uuid.uuid4().hex})
    await collection.insert_one(_to_document(workout, owner_id))
    return workout


@router.get("", response_model=WorkoutPage)
async def list_workouts(
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
    mode: WorkoutMode | None = None,
    category: str | None = Query(None, max_length=200),
    owner_id: str = Depends(current_owner),
) -> WorkoutPage:
    """One page of the owner's library, newest first, narrowed by the filters.

    Searching server-side rather than in the browser is what keeps paging
    honest: a filter applied to the current page alone would hide matches
    sitting on page three.
    """
    collection = get_workouts_collection()
    mongo_filter = _library_filter(owner_id, q, mode, category)
    total = await collection.count_documents(mongo_filter)
    # `_id` breaks ties. Paging the same sort twice has to agree on the order,
    # and `created_at` alone doesn't guarantee that when rows share a timestamp
    # — seeding writes the benchmark set in one tight loop.
    cursor = (
        collection.find(mongo_filter)
        .sort([("created_at", -1), ("_id", -1)])
        .skip(offset)
        .limit(limit)
    )
    items = [_from_document(doc) async for doc in cursor]
    return WorkoutPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/categories", response_model=list[str])
async def list_categories(owner_id: str = Depends(current_owner)) -> list[str]:
    """The categories present in this owner's library, for the filter dropdown.

    Declared ahead of `/{workout_id}` so the literal path wins the match.
    """
    values = await get_workouts_collection().distinct("category", {"owner_id": owner_id})
    return sorted(str(value) for value in values if value)


@router.post("/{workout_id}/started", status_code=status.HTTP_204_NO_CONTENT)
async def mark_started(workout_id: str, owner_id: str = Depends(current_owner)) -> None:
    """Record that the clock actually started on this workout.

    Its own call rather than something inferred from a read, because loading a
    workout and *running* it are very different signals — the library gets
    browsed, and browsing isn't training. Deliberately not behind
    `writes_allowed`: this is idempotent-ish telemetry, and having a limit
    reject it would silently bias the numbers toward light users.

    Owner-scoped like every other by-id route, so this can't be used to probe
    whether another user's workout exists.
    """
    doc = await get_workouts_collection().find_one(
        {"_id": workout_id, "owner_id": owner_id}, {"mode": 1}
    )
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    await record_workout_started(
        owner_id=owner_id, workout_id=workout_id, mode=str(doc.get("mode") or "")
    )


@router.post("/{workout_id}/completed", status_code=status.HTTP_204_NO_CONTENT)
async def mark_completed(
    workout_id: str,
    body: WorkoutCompletedRequest,
    owner_id: str = Depends(current_owner),
) -> None:
    """Record that the clock stopped, and how long it ran for.

    The counterpart to `/started`. On its own a start count can't distinguish a
    workout people finish from one they abandon halfway, which is the more
    interesting number — and it's the difference between programming that fits
    and programming that doesn't.

    This is *not* a result. It says the clock ran, not what was actually
    lifted or how many rounds landed; that needs a model this app doesn't have
    (see `docs/exports.md`). When one exists, this is the moment an export to
    the athlete's own training log fires.
    """
    doc = await get_workouts_collection().find_one(
        {"_id": workout_id, "owner_id": owner_id}, {"mode": 1}
    )
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    await record_workout_completed(
        owner_id=owner_id,
        workout_id=workout_id,
        mode=str(doc.get("mode") or ""),
        elapsed_seconds=body.elapsed_seconds,
    )


@router.get("/{workout_id}", response_model=Workout)
async def get_workout(workout_id: str, owner_id: str = Depends(current_owner)) -> Workout:
    collection = get_workouts_collection()
    doc = await collection.find_one({"_id": workout_id, "owner_id": owner_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    return _from_document(doc)


@router.put("/{workout_id}", response_model=Workout)
async def update_workout(
    workout_id: str, body: WorkoutCreateRequest, owner_id: str = Depends(writes_allowed)
) -> Workout:
    collection = get_workouts_collection()
    workout = body.workout.model_copy(update={"id": workout_id})
    result = await collection.replace_one(
        {"_id": workout_id, "owner_id": owner_id}, _to_document(workout, owner_id)
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    return workout


@router.delete("/{workout_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workout(workout_id: str, owner_id: str = Depends(writes_allowed)) -> None:
    collection = get_workouts_collection()
    result = await collection.delete_one({"_id": workout_id, "owner_id": owner_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
