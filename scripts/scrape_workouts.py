#!/usr/bin/env python
"""Build test fixtures from real, published workouts.

This fetches a list of URLs, pulls the readable text out of each page, and
runs it through the same LLM parser the app uses (`short_timer.llm`) to
produce a structured `Workout`. Results are written to
`tests/fixtures/scraped_workouts.json` as `{name, url, source_text, workout}`
records that new parser tests can be parametrized over.

Usage:
    hatch run scrape https://example.com/workouts/murph https://example.com/wod/24-1

Notes:
  - Respects robots.txt for each host; skips (with a warning) any URL that
    disallows fetching.
  - Requires network access and a valid ANTHROPIC_API_KEY. Neither is
    available inside this sandboxed dev session (its egress policy only
    allows PyPI/npm/Anthropic-API traffic, not arbitrary websites) — run this
    from a normal machine or CI job with broader internet access.
  - This is a best-effort text extractor (strip scripts/styles, keep visible
    text), not a site-specific scraper, so it works across differently
    structured pages without per-site CSS selectors. Always check that the
    target site's terms of service permit scraping before pointing this at
    it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from short_timer.llm import WorkoutParseError, parse_workout_text

USER_AGENT = "short-timer-fixture-scraper/0.1 (+https://github.com/devdupont/short-timer)"
FIXTURES_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "scraped_workouts.json"
)
REQUEST_DELAY_SECONDS = 2.0


def _robots_allow(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except OSError:
        # If robots.txt is unreachable, err on the side of not scraping.
        return False
    return parser.can_fetch(USER_AGENT, url)


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


async def scrape_one(client: httpx.AsyncClient, url: str) -> dict | None:
    if not _robots_allow(url):
        print(f"skip (robots.txt disallows): {url}", file=sys.stderr)
        return None

    response = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    text = _extract_text(response.text)

    try:
        workout = await parse_workout_text(text)
    except WorkoutParseError as exc:
        print(f"parse failed for {url}: {exc}", file=sys.stderr)
        return None

    return {
        "name": workout.name,
        "url": url,
        "source_text": text,
        "workout": workout.model_dump(mode="json"),
    }


async def scrape_all(urls: list[str]) -> list[dict]:
    results: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for i, url in enumerate(urls):
            if i > 0:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
            record = await scrape_one(client, url)
            if record is not None:
                results.append(record)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+", help="Workout page URLs to scrape.")
    args = parser.parse_args()

    results = asyncio.run(scrape_all(args.urls))

    existing: list[dict] = []
    if FIXTURES_PATH.exists():
        existing = json.loads(FIXTURES_PATH.read_text())

    by_url = {record["url"]: record for record in existing}
    for record in results:
        by_url[record["url"]] = record

    FIXTURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURES_PATH.write_text(json.dumps(list(by_url.values()), indent=2))
    print(f"Wrote {len(by_url)} fixtures to {FIXTURES_PATH}")


if __name__ == "__main__":
    main()
