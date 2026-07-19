from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from short_timer.auth import current_owner, require_session
from short_timer.benchmarks import benchmark_workouts
from short_timer.db import get_workouts_collection
from short_timer.dedup import source_hash
from short_timer.llm import WorkoutParseError, parse_workout_text
from short_timer.models import (
    SeedResponse,
    Workout,
    WorkoutCreateRequest,
    WorkoutParseRequest,
)

router = APIRouter(
    prefix="/api/workouts", tags=["workouts"], dependencies=[Depends(require_session)]
)


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


async def _parse_or_cached(text: str, name_hint: str | None, owner_id: str) -> Workout:
    """Parse `text` into a Workout, reusing a saved match to avoid an LLM call."""
    cached = await _find_by_source_text(text, owner_id)
    if cached is not None:
        return cached
    try:
        return await parse_workout_text(text, name_hint=name_hint)
    except WorkoutParseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/parse", response_model=Workout)
async def parse_workout(
    body: WorkoutParseRequest, owner_id: str = Depends(current_owner)
) -> Workout:
    """Parse pasted text into a preview Workout (not saved)."""
    return await _parse_or_cached(body.text, body.name_hint, owner_id)


@router.post("/from-text", response_model=Workout, status_code=status.HTTP_201_CREATED)
async def create_from_text(
    body: WorkoutParseRequest, owner_id: str = Depends(current_owner)
) -> Workout:
    """Get-or-create: return a saved workout matching the text, else parse and save it."""
    existing = await _find_by_source_text(body.text, owner_id)
    if existing is not None:
        return existing
    workout = await _parse_or_cached(body.text, body.name_hint, owner_id)
    await get_workouts_collection().insert_one(_to_document(workout, owner_id))
    return workout


@router.post("/seed", response_model=SeedResponse)
async def seed_benchmarks(owner_id: str = Depends(current_owner)) -> SeedResponse:
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
    body: WorkoutCreateRequest, owner_id: str = Depends(current_owner)
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
    await collection.insert_one(_to_document(body.workout, owner_id))
    return body.workout


@router.get("", response_model=list[Workout])
async def list_workouts(owner_id: str = Depends(current_owner)) -> list[Workout]:
    collection = get_workouts_collection()
    cursor = collection.find({"owner_id": owner_id}).sort("created_at", -1)
    return [_from_document(doc) async for doc in cursor]


@router.get("/{workout_id}", response_model=Workout)
async def get_workout(workout_id: str, owner_id: str = Depends(current_owner)) -> Workout:
    collection = get_workouts_collection()
    doc = await collection.find_one({"_id": workout_id, "owner_id": owner_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    return _from_document(doc)


@router.put("/{workout_id}", response_model=Workout)
async def update_workout(
    workout_id: str, body: WorkoutCreateRequest, owner_id: str = Depends(current_owner)
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
async def delete_workout(workout_id: str, owner_id: str = Depends(current_owner)) -> None:
    collection = get_workouts_collection()
    result = await collection.delete_one({"_id": workout_id, "owner_id": owner_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
