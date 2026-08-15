"""Content-based deduplication for workout source text.

Parsing a workout costs an LLM call, so we key saved workouts by a normalized
hash of their source text. Two pastes that differ only in whitespace or case
map to the same hash, letting us reuse an existing parse instead of asking the
model again.
"""

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")


def normalize_source_text(text: str) -> str:
    """Collapse insignificant formatting so equivalent pastes match.

    Trims each line, collapses internal whitespace runs, drops blank lines, and
    lowercases — enough to absorb copy/paste noise without merging genuinely
    different workouts.
    """
    lines = (_WHITESPACE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line).lower()


def source_hash(text: str) -> str:
    """A stable hex digest of the normalized source text."""
    return hashlib.sha256(normalize_source_text(text).encode("utf-8")).hexdigest()
