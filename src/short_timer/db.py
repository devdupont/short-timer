"""MongoDB access via PyMongo's native async client.

Motor is no longer maintained now that PyMongo ships its own asyncio
support (`pymongo.AsyncMongoClient`, 4.9+); this talks to Mongo directly
through that instead.
"""

from functools import lru_cache
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from short_timer.config import get_settings


@lru_cache
def get_client() -> AsyncMongoClient[dict[str, Any]]:
    return AsyncMongoClient(get_settings().mongodb_uri)


def get_database() -> AsyncDatabase[dict[str, Any]]:
    return get_client()[get_settings().mongodb_db_name]


def get_workouts_collection() -> AsyncCollection[dict[str, Any]]:
    return get_database()["workouts"]
