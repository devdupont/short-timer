"""The Mongo client's own configuration, as opposed to what's stored through it."""

import importlib
import pkgutil

from beanie import Document

import shortimer
from shortimer.cache.db import DOCUMENT_MODELS
from shortimer.config import get_settings
from shortimer.model.base import MongoDocument


def test_database_client_fails_fast_rather_than_hanging() -> None:
    """A slow failure is worse than a fast one when every request pays for it."""
    assert get_settings().mongodb_timeout_ms <= 10_000


def _documents_in_package() -> set[type[Document]]:
    """Every concrete Beanie document defined anywhere in `shortimer`.

    The whole package is imported first: a class only shows up in
    `__subclasses__()` once the module defining it has run, so a model nobody
    happened to import would look like it doesn't exist — which is exactly the
    model this needs to catch.
    """
    for info in pkgutil.walk_packages(shortimer.__path__, f"{shortimer.__name__}."):
        importlib.import_module(info.name)

    found: set[type[Document]] = set()

    def walk(cls: type[Document]) -> None:
        for subclass in cls.__subclasses__():
            if subclass not in found:
                found.add(subclass)
                walk(subclass)

    walk(Document)
    # `MongoDocument` is the shared base (see model/base.py), not a collection.
    return {c for c in found if c.__module__.startswith("shortimer.") and c is not MongoDocument}


def test_every_document_model_is_registered() -> None:
    """`init_documents` must know about every document, or queries on it fail.

    This is the guard for a bug that already happened once: the models were
    listed by hand in two places, a third caller (`scripts/create_admin.py`)
    initialised none of them, and its first query raised a bare
    `AttributeError: email` — which reads like a typo in the field name rather
    than a missing init. A model added to the package but not to
    `DOCUMENT_MODELS` fails the same unhelpful way, so catch it here instead.
    """
    missing = _documents_in_package() - set(DOCUMENT_MODELS)
    assert not missing, (
        f"{sorted(c.__name__ for c in missing)} inherit from Document but are not in "
        "DOCUMENT_MODELS, so init_documents() never binds them and the first query "
        "against one raises a bare AttributeError on the field name."
    )


def test_no_registered_model_has_been_deleted() -> None:
    """The reverse: a stale entry naming a document that no longer exists.

    Beanie would fail at startup rather than silently, but it fails for the
    whole app — including every test — which is a slow way to learn that a
    list needed editing.
    """
    stale = set(DOCUMENT_MODELS) - _documents_in_package()
    assert not stale, (
        f"{sorted(c.__name__ for c in stale)} are in DOCUMENT_MODELS but are no longer "
        "documents defined in this package."
    )
