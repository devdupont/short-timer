from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from short_timer.auth import require_session
from short_timer.db import get_workouts_collection
from short_timer.llm import WorkoutParseError, parse_workout_text
from short_timer.models import Workout, WorkoutCreateRequest, WorkoutParseRequest

router = APIRouter(
    prefix="/api/workouts", tags=["workouts"], dependencies=[Depends(require_session)]
)


def _to_document(workout: Workout) -> dict[str, Any]:
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    return doc


def _from_document(doc: dict[str, Any]) -> Workout:
    doc = dict(doc)
    doc["id"] = doc.pop("_id")
    return Workout(**doc)


@router.post("/parse", response_model=Workout)
async def parse_workout(body: WorkoutParseRequest) -> Workout:
    try:
        return await parse_workout_text(body.text, name_hint=body.name_hint)
    except WorkoutParseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("", response_model=Workout, status_code=status.HTTP_201_CREATED)
async def create_workout(body: WorkoutCreateRequest) -> Workout:
    collection = get_workouts_collection()
    await collection.insert_one(_to_document(body.workout))
    return body.workout


@router.get("", response_model=list[Workout])
async def list_workouts() -> list[Workout]:
    collection = get_workouts_collection()
    return [_from_document(doc) async for doc in collection.find().sort("created_at", -1)]


@router.get("/{workout_id}", response_model=Workout)
async def get_workout(workout_id: str) -> Workout:
    collection = get_workouts_collection()
    doc = await collection.find_one({"_id": workout_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    return _from_document(doc)


@router.put("/{workout_id}", response_model=Workout)
async def update_workout(workout_id: str, body: WorkoutCreateRequest) -> Workout:
    collection = get_workouts_collection()
    workout = body.workout.model_copy(update={"id": workout_id})
    result = await collection.replace_one({"_id": workout_id}, _to_document(workout))
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
    return workout


@router.delete("/{workout_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workout(workout_id: str) -> None:
    collection = get_workouts_collection()
    result = await collection.delete_one({"_id": workout_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workout not found")
