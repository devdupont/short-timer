# Notes for later

Things we've decided *not* to build yet, written down with the constraint in
today's code that will decide how they get built. Not commitments — context,
so the next person doesn't have to re-derive it.

## Cursor paging, once a listing stops being single-owner

`GET /api/workouts` pages with `limit`/`offset` (see
`shortimer/routers/workouts.py`). That's the right trade for a personal
library: it's cheap, it gives an exact `total`, and it can jump to page 5.
Its known weakness is drift — a workout saved while you're reading page 2
shifts every later row down one, so you see a duplicate on page 3 and a
row you never saw at all.

For one person browsing their own saved workouts that's close to unobservable;
writes are rare and they're usually the one making them. It stops being
unobservable the moment a listing has more than one writer or a lot of them —
a shared list, a public feed, anything curated centrally. That's the trigger
to switch to a cursor keyed on `(created_at, _id)`, which costs the `total`
and random page access.

The index added for paging, `(owner_id, created_at desc)`, serves a cursor
just as well, so the switch is a router change, not a data migration.

## Curated lists

Seeding benchmarks (`POST /api/workouts/seed`) *copies* 15 workouts into the
caller's library, deduped by `source_hash`. A curated list is the same idea
generalized — "CrossFit Girls", "bodyweight only", "20 minutes or less" —
and the open question is whether it stays a copy.

Copying is what makes the current model simple: every row has exactly one
`owner_id`, every query filters on it, and the user can rename and re-cap
anything without consequence. A list that *references* shared workouts breaks
that — reads stop being owner-scoped, and edits need a copy-on-write rule.
Worth being deliberate about which one a curated list is before building it,
because the two produce very different queries.

If lists are their own collection, note that a list of ids paged
independently of the workouts it points at is the multi-writer case above.

## Shareable lists and workouts

The precedent to read first is `shortimer/parse_cache.py`. Its docstring
already works through the copy-vs-share question for parses and lands on
copy: a shared record would push one user's renames and time caps onto
everyone else who pasted the same text, so the pool holds only the neutral
parse, and every user gets their own copy to edit. Sharing a *workout* faces
the same fork, with the added wrinkle that a shared record has an author
someone might expect to keep editing.

Two things need to exist first:

- ~~**Real accounts.**~~ Built — see `docs/accounts.md`. There is now someone
  to share *with*.
- **A visibility model.** Ownership is currently binary: a row is yours or
  it's invisible. Sharing means a third state, and every query that filters
  `{"owner_id": owner_id}` becomes a decision point.

The MCP server acts on one library, decided by the API token in
`MCP_API_TOKEN`. It has no session and no way to answer "who's asking?" on its
own, so the token is the identity.

## Roles — built

Landed with accounts. `Role` (`user` / `staff` / `admin`) is on the user
record, `_require_operator` gates on it, and `METRICS_ADMIN_USER_IDS` was kept
as the break-glass this section argued for. `docs/accounts.md` has the
reasoning, including why a gym owner is a question of *scope* rather than rank
and so is still waiting on the plan/tier work.

`MCP_OWNER_ID` had the same shape of problem and is gone too — per-user API
tokens replaced it. See `docs/accounts.md`.
