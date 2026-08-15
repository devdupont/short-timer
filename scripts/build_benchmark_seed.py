#!/usr/bin/env python
"""Pre-parse the curated benchmark WODs into a committed seed file.

The app ships a starter library of classic benchmark workouts (the Girls,
Hero WODs, and other named benchmarks). Parsing them costs an LLM call each,
so we do it once here at dev time and commit the structured result — seeding
a library at runtime is then instant, free, and deterministic.

Source texts come from `tests/fixtures/workouts.json`; the category recorded
there is authoritative (it's curated, unlike the model's guess).

Usage:
    hatch run seed-data          # requires ANTHROPIC_API_KEY + network
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from shortimer.service.llm import WorkoutParseError, parse_workout_text

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = ROOT / "tests" / "fixtures" / "workouts.json"
SEED_PATH = ROOT / "shortimer" / "data" / "benchmark_wods.json"

# "custom" entries are illustrative examples, not real named benchmarks.
# The named WODs are one uniform class — we deliberately don't split them by
# gender ("girl"/"boy"); scaling differences live in each movement's reps and
# loads, not in a category label.
SEED_CATEGORIES = {"benchmark"}

# Server-managed fields; the API fills these in on insert.
DROPPED_FIELDS = ("id", "created_at", "updated_at", "source_hash")


async def build() -> list[dict]:
    fixtures: list[dict] = json.loads(FIXTURES_PATH.read_text())
    selected = [f for f in fixtures if f.get("category") in SEED_CATEGORIES]
    print(f"Parsing {len(selected)} benchmark workouts…")

    seeds: list[dict] = []
    for fixture in selected:
        try:
            workout = await parse_workout_text(fixture["source_text"], name_hint=fixture["name"])
        except WorkoutParseError as exc:
            print(f"  ! {fixture['name']}: {exc}")
            continue

        data = workout.model_dump(mode="json")
        for field in DROPPED_FIELDS:
            data.pop(field, None)
        # Trust the curated name/category over the model's interpretation.
        data["name"] = fixture["name"]
        data["category"] = fixture["category"]
        seeds.append(data)
        print(f"  ✓ {fixture['name']:<14} {data['mode']}")

    return seeds


def main() -> None:
    seeds = asyncio.run(build())
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_text(json.dumps(seeds, indent=2) + "\n")
    print(f"\nWrote {len(seeds)} workouts to {SEED_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
