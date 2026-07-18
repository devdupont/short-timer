"""MongoDB access via Motor."""

from functools import lru_cache
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase

from short_timer.config import get_settings


@lru_cache
def get_client() -> AsyncIOMotorClient[dict[str, Any]]:
    return AsyncIOMotorClient(get_settings().mongodb_uri)


def get_database() -> AsyncIOMotorDatabase[dict[str, Any]]:
    return get_client()[get_settings().mongodb_db_name]


def get_workouts_collection() -> AsyncIOMotorCollection[dict[str, Any]]:
    return get_database()["workouts"]
