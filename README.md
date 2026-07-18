# short-timer

A programmable workout timer. Paste a workout from anywhere (or build one by
hand), and the server uses an LLM to turn it into a structured, runnable
timer — For Time, AMRAP, EMOM, Tabata, or interval — that the web app can
drive.

## Layout

```
src/short_timer/       FastAPI server, MongoDB models, LLM parser, MCP server
web/                   React + Vite + TypeScript frontend
tests/                 pytest suite, including fixtures/workouts.json (a
                        curated benchmark-WOD library — Murph, Fran, Cindy, ...)
scripts/scrape_workouts.py  Fetches real workout pages and adds them as fixtures
```

## How a workout is represented

Every workout (typed in, pasted, or scraped) becomes the same shape: a
`mode` (`for_time` / `amrap` / `emom` / `tabata` / `interval` / `custom`)
that tells the timer how to run the clock, plus an ordered list of
`segments` describing what to do. Segments can nest their own `rounds` and
`rep_scheme`, which is what lets something like Murph (a for-time chipper
with a 20-round partition sandwiched between two runs) and Fran (a flat
21-15-9 for time) share one schema. See `src/short_timer/models.py`.

## Requirements

- Python 3.14 (`uv python install 3.14` if you don't have it)
- Node 20+ for the frontend
- A MongoDB instance (`docker compose up -d mongo` works for local dev)
- An [Anthropic API key](https://console.anthropic.com/) for the LLM parser

## Backend setup

```bash
uv sync                       # installs the default + dev dependency groups
cp .env.example .env          # fill in APP_PASSCODE, SESSION_SECRET, ANTHROPIC_API_KEY
docker compose up -d mongo    # or point MONGODB_URI at your own instance
hatch run serve               # http://localhost:8000, auto-reloading
```

Generate a session secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### Tests and linting

```bash
hatch run test        # pytest — mocks Mongo and the Anthropic API, no network needed
hatch fmt             # ruff lint + format
```

The parser tests in `tests/test_llm_parser.py` include a `live` set,
parametrized over `tests/fixtures/workouts.json`, that call the real
Anthropic API and check the parsed output against each workout's expected
mode/rounds/rep scheme/movements. They're skipped unless `ANTHROPIC_API_KEY`
is set to a real key, since they cost tokens:

```bash
hatch run test -m live
```

### Building the fixture library from real workouts

`tests/fixtures/workouts.json` ships with 15 hand-checked benchmark
workouts (the Girls, a few Heroes, Chelsea, Tabata This, ...) so the test
suite works offline. To pull more from the web and run them through the
parser:

```bash
hatch run scrape https://example.com/workouts/24-1
```

This respects `robots.txt`, extracts the page's visible text, parses it with
the same LLM pipeline the app uses, and appends the result to
`tests/fixtures/scraped_workouts.json`. Only point it at sources whose terms
allow scraping.

> This repo was developed inside a sandboxed session whose network policy
> only allows PyPI/npm/Anthropic traffic — not arbitrary websites — so the
> scraper itself couldn't be run there. It's written and unit-tested via the
> curated fixtures, but hasn't been exercised against a live page; try it
> against a real URL before relying on it.

### MCP server

```bash
hatch run mcp
```

Exposes: `parse_workout` (authoring — run pasted text through the LLM
parser), `create_timer_workout` (authoring — save a structured workout
directly), and `search_workouts` / `get_workout` (library — read from the
same MongoDB collection the web app uses). See
`src/short_timer/mcp_server.py`.

## Frontend setup

```bash
cd web
npm install
cp .env.example .env   # VITE_API_BASE_URL, defaults to http://localhost:8000
npm run dev            # http://localhost:5173
```

`npm run build` type-checks and produces a production bundle; `npm run lint`
runs oxlint.

## Auth

There's no user model yet — just a single shared passcode (`APP_PASSCODE`).
Logging in gets you a signed, HttpOnly session cookie; every `/api/workouts*`
route requires it. Swap in real accounts later without touching the rest of
the app, since routes only depend on the `require_session` dependency, not
on any notion of a user.

## Note on Python 3.14

This project targets Python 3.14. The sandboxed session this was originally
built in couldn't install a 3.14 interpreter (its network policy blocks the
GitHub-hosted python-build-standalone releases `uv python install` needs, and
the deadsnakes PPA), so the test/lint runs here were validated against 3.13
as a stand-in. Run `uv python install 3.14 && uv sync` on a machine with
normal internet access to get the real target version.
