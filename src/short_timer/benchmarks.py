"""The starter library of classic benchmark workouts.

These are the named benchmarks most gyms program against — the Girls (Fran,
Cindy, …), Hero WODs (Murph, DT, Nancy), and other standards (Chelsea,
Jackie). They're pre-parsed and committed by
`scripts/build_benchmark_seed.py`, so seeding a library costs no LLM calls.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from short_timer.models import Workout

_SEED_PATH = Path(__file__).parent / "data" / "benchmark_wods.json"


@lru_cache
def _seed_data() -> tuple[dict[str, object], ...]:
    return tuple(json.loads(_SEED_PATH.read_text()))


def benchmark_workouts() -> list[Workout]:
    """Fresh Workout instances for the benchmark library.

    Built per call so each seeding run gets its own ids/timestamps rather
    than reusing shared instances.
    """
    return [Workout(**dict(item)) for item in _seed_data()]
