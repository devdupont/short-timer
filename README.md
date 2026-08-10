# shortimer

A programmable workout timer. Paste a workout from anywhere (or build one by
hand), and the server uses an LLM to turn it into a structured, runnable
timer — For Time, AMRAP, EMOM, Tabata, or interval — that the web app can
drive.

## Layout

```
shortimer/             FastAPI server, MongoDB models, LLM parser, MCP server
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
21-15-9 for time) share one schema. A segment can also carry its own
`work_seconds`/`rest_seconds` (a "5/4/3/2/1 minutes" ladder is five legs of
different lengths) or set `is_rest` to say the leg *is* the recovery — an
EMOM whose "Minute 5: Rest" — so the clock runs it as a rest period instead
of announcing a movement nobody performs. `interval_clock` says which way the
clock runs *inside* a leg: down by default (how long you have left to finish
the minute), or up for sets scored by their finish time ("Every 3:00 x 5 sets,
score = slowest set"), where athletes finish at different moments and each
needs to read their own split. See `shortimer/models.py`.

`web/src/timerPlan.ts` turns that shape into the plan the clock runs, and the
visualizer (`WorkoutTimeline`) draws the same plan to scale — colour-coded by
movement, rest striped — so a bad parse is visible in the paste preview or
the builder without starting a timer to find it.

## Requirements

- Python 3.14 (`uv python install 3.14` if you don't have it)
- Node 20+ for the frontend
- A MongoDB instance (`docker compose up -d mongo` works for local dev)
- An [Anthropic API key](https://console.anthropic.com/) for the LLM parser

## Backend setup

```bash
uv sync                       # installs the default + dev dependency groups
cp .env.example .env          # fill in ANTHROPIC_API_KEY; the rest have defaults
docker compose up -d mongo    # or point MONGODB_URI at your own instance
hatch run serve               # http://localhost:8000, auto-reloading
```

Registration is invite-only, and invites come from admins, so create the first
account directly:

```bash
hatch run python scripts/create_admin.py you@example.com
```

Outbound email is off by default, so verification and reset links are written
to the log rather than sent — the whole signup flow works with no provider.

### Tests and linting

```bash
hatch run test         # pytest — mocks Mongo and the Anthropic API, no network needed
hatch check code       # ruff lint      (add --fix to apply)
hatch check fmt        # ruff format    (add --fix to apply)
hatch run types        # mypy
```

All four should pass before a change lands. `hatch fmt` still works but is
deprecated in favour of the two `hatch check` commands above.

CI runs the tests through `uv sync --frozen` instead, so it installs exactly
what `uv.lock` pins — the same resolution the Docker image is built from. That
means a dependency's new release can't turn `main` red on its own: it has to
arrive as a lockfile change (`uv lock --upgrade-package <name>`) that someone
reviewed. Hatch reads the same `[dependency-groups] dev`, so the two agree.

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
`shortimer/mcp_server.py`.

It reads and writes one owner's library. Having no session to derive that
from, it authenticates with a per-user API token in `MCP_API_TOKEN` — mint one
under Settings → API tokens. The owner and the allowed operations both come
from the token, and it can be revoked without touching the account.

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

Email and password, with invite-only registration. Passwords hash with Argon2id;
sessions live in the database, so they can actually be revoked. Roles (`user`,
`staff`, `admin`) gate the privileged surfaces.

Every owner-scoped route depends on `current_owner`, which is the single place
tenancy is decided — routes never read the session themselves.

See [docs/accounts.md](docs/accounts.md) for the reasoning, the DNS the email
needs, and what's still open.

## Note on Python 3.14

This project targets Python 3.14. The sandboxed session this was originally
built in couldn't install a 3.14 interpreter (its network policy blocks the
GitHub-hosted python-build-standalone releases `uv python install` needs, and
the deadsnakes PPA), so the test/lint runs here were validated against 3.13
as a stand-in. Run `uv python install 3.14 && uv sync` on a machine with
normal internet access to get the real target version.
