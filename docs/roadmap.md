# Notes for later

Things we've decided *not* to build yet, written down with the constraint in
today's code that will decide how they get built. Not commitments — context,
so the next person doesn't have to re-derive it.

## Cursor paging, once a listing stops being single-owner

`GET /api/workouts` pages with `limit`/`offset` (see
`src/short_timer/routers/workouts.py`). That's the right trade for a personal
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

The precedent to read first is `src/short_timer/parse_cache.py`. Its docstring
already works through the copy-vs-share question for parses and lands on
copy: a shared record would push one user's renames and time caps onto
everyone else who pasted the same text, so the pool holds only the neutral
parse, and every user gets their own copy to edit. Sharing a *workout* faces
the same fork, with the added wrinkle that a shared record has an author
someone might expect to keep editing.

Two things need to exist first:

- **Real accounts.** Auth is still one shared passcode resolving to
  `DEFAULT_OWNER_ID` (`src/short_timer/auth.py`, `src/short_timer/users.py`).
  Both are written so signup is another way to mint a user id rather than a
  storage change, but "shared with you" needs someone to share *with*.
- **A visibility model.** Ownership is currently binary: a row is yours or
  it's invisible. Sharing means a third state, and every query that filters
  `{"owner_id": owner_id}` becomes a decision point.

The MCP server used to read the whole `workouts` collection without filtering
on `owner_id`; it now acts on one library, named by `MCP_OWNER_ID` and
defaulting to the shared-passcode user. Worth remembering that it resolves its
owner from configuration rather than from a session, so a deployment with real
accounts has to point it at one of them — it has no way to answer "who's
asking?" on its own.

## Roles on the user record, when accounts land

`METRICS_ADMIN_USER_IDS` is a placeholder. It exists because
`/api/metrics/operator` needed *some* gate and there was nothing on the user
record to gate on — an env-var allowlist was the smallest honest thing that
wasn't "anyone with the passcode sees the Anthropic bill". Accounts should
replace it with a real `role` field on `User`, alongside `config`.

Two roles are known to be needed:

- **`admin`** — the operator. Reads global spend, every user's activity, and
  whatever else gets added to `/api/metrics/operator`.
- **Something for internal staff** — people who need the privileged metrics to
  do support or ops work, but aren't the account owner. Call it `staff`; the
  point is that it's a second privileged role, so the check can't be
  `user.is_admin`.

Three things worth deciding deliberately rather than discovering later.

**Role is not the same axis as scope.** The gym tier in `docs/pricing.md`
implies a gym owner who is privileged over *their own gym's* data and over
nothing else. That isn't a rung on the admin ladder — it's a different
question ("what may you see?" vs "how much of it?"). A single ordered
`role` enum handles admin and staff fine and will fight the gym case, so
either keep gym-scoping as its own concept from the start, or accept that
`role` answers only the global question.

**The seam is already one function.** `_require_operator` in
`src/short_timer/routers/metrics.py` is the only place the allowlist is read.
Swapping it for a role check is a change to that function and nothing else,
which is the property to preserve as more privileged surfaces appear — every
one of them should depend on that dependency, not re-derive the rule.

**Consider keeping the env allowlist as a break-glass.** A role stored in the
database is unreadable in exactly the situation where you most want metrics:
the database is sick, or a bad migration wrote the wrong roles. An env var that
still works when the `users` collection doesn't is cheap insurance, provided
it's clearly the fallback rather than the mechanism.

Related: `MCP_OWNER_ID` has the same shape of problem — configuration standing
in for identity because there isn't any yet. Both should be revisited in the
same pass.

## Canonical movement names

Right now a movement is whatever text the parser pulled out of whatever the gym
wrote: "Thrusters", "thruster 95/65", "KB swings", "Strict Press". That's fine
for putting words on a clock and insufficient for anything that has to *know*
what the movement is.

Everyone else in this space has solved it. Wodify keys personal bests,
movement and weight history, and percentage-based loading off canonical
movement identities — "build to a heavy single, then 5×3 at 80%" is
unanswerable without one. Hevy does the same thing with
`exercise_template_id`. It's table stakes for any integration that writes.

**Several names mean the same movement.** Strict press, shoulder press,
military press and overhead press are one thing. So the useful artifact isn't a
canonical *name*, it's an **alias table** — many strings collapsing to one id.
That table is static data rather than code, it's the thing that makes parsing
robust *and* every integration cheaper, and it's worth building even if no
export ever ships.

**The trap is that not every near-name is a synonym.** Push press and push jerk
are not a strict press — they're different movements with different loading.
Banded, jumping and kipping pull-ups aren't a pull-up, they're scalings of it.
A table built by pattern-matching on words will collapse things that must stay
distinct and will quietly corrupt any PR history downstream of it. Aliases and
variants need to be different relationships from the start; retrofitting that
distinction after the data exists is the expensive version.

### Whose vocabulary?

The open question, and the one that decides the shape: **every service has its
own identifiers.** Wodify's canonical names are not Hevy's template ids.

Two ways to go, and the precedent in this codebase points at the second:

- **Adopt one service's vocabulary.** Cheap for that one integration, and every
  subsequent service becomes a translation from a vocabulary that was never
  designed to be neutral. N services means N×N mappings.
- **Own an internal id, and let each adapter map to its own vendor's.** N
  mappings, each owned by the adapter that already knows that platform. This is
  exactly what `gym_providers.py` does with `location`/`program`: the storage
  schema stays vocabulary-free and the registry holds the per-platform naming.

The second is almost certainly right, but it's only worth paying for once
something actually consumes it — see `docs/exports.md`, where the export path
is blocked on a result model rather than on this.

### Where canonicalisation happens

A deterministic lookup against the alias table should be primary, with the LLM
as the fallback for text the table doesn't know. That's the reverse of the
"have the parser emit a canonical name" idea noted earlier in `docs/exports.md`:
a table costs no tokens, gives the same answer every time, and can be tested.
Reserve the model for the long tail, and feed what it resolves back into the
table.

### The part worth *not* building

Percentage-based loading needs an athlete's one-rep max, which is history —
which this app has deliberately decided is not its job. The consistent answer
is to read PRs *from* the connected service rather than accumulate them here,
for the same reason exports push rather than store: the tracker is the system
of record. Canonical names are what make that read possible.
