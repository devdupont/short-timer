# Adding a gym platform

What it takes to connect one more place gyms keep their programming, and why
the seams are where they are. Written after SugarWOD became the second
provider, which is when the shape of the abstraction stopped being a guess.

## The three pieces

**A client module** (`wodify.py`, `sugarwod.py`) that turns a credential and a
day range into `list[GymWod]`. It owns its URLs, its auth header, its response
quirks, and its own defensive reading of fields whose names came from
documentation rather than from a live response. It swallows its errors: a day
that's missing or a call that fails returns nothing, because one bad day must
not empty a feed.

**A registry entry** (`gym_providers.py`) that names the provider, says how to
fetch it, and declares what to call its fields in front of a human. This is the
only file that knows both halves.

**A member of `GymProvider`** (`models.py`), plus a position in
`PROVIDER_PRIORITY`, which decides which connection wins when a user has
configured more than one.

Nothing else changes. Not the cache, not the refresh sweep, not the feed route,
and — importantly — not the frontend.

## Why the storage schema is deliberately vague

`GymConnection` stores a credential and two generically-named text fields,
`location` and `program`. Wodify calls them location and program; SugarWOD
calls the second one a track and has no use for the first.

The alternative — a field per platform — puts vocabulary into the storage
schema, which means a migration every time a platform disagrees with the last
one. Instead the vocabulary lives in the registry as `GymFieldInfo`, travels to
the browser as data, and Settings renders whatever it is handed. A provider
that declares no `location` simply doesn't get a location box.

The same declaration drives `GymProviderSpec.is_usable`, so the asterisk on the
form and the server's answer to "is this connected?" cannot drift apart.

## Why "one active gym"

A user may store several connections. Only one feeds the home page, because
"Your gym" is singular in the UI, and `PROVIDER_PRIORITY` breaks the tie rather
than storage order — so the answer doesn't depend on which was saved first.
Member routes outrank owner routes: someone who is both an admin and an athlete
sees the same gym either way, and a public whiteboard costs no API quota.

Multiple simultaneous gyms is a real feature (a coach at two boxes), but it's a
*display* problem — the feed would need sections — not a storage one. The
schema already holds them.

## The cache key is a credential fingerprint

`gym_fingerprint(credential, provider)` is a SHA-256 prefix of the provider and
the credential. Two facts hang off this:

- **It has to be gym-unique or the feed leaks.** "Main"/"CrossFit" is not a
  rare location/program pair, and keying on that would serve one gym's
  workouts to another. The credential is the only thing that actually
  identifies a gym.
- **It gets sharing right for free.** Twenty members of one gym hold the same
  whiteboard key, so they share cache entries and the gym is fetched once.
  Two admins with separately-issued API keys don't share, which is the safe
  way round.

The provider is mixed in because the same gym reached two ways returns
differently formatted text, and conflating them would serve whichever was
written last.

## Connection health exists because failures are silent

Every fetcher swallows its errors by design. The cost is that a wrong
credential and a gym that simply didn't post look identical from the outside —
both are an empty feed.

`GET /api/gym/health` reports, per stored connection, when it last fetched
successfully and how many days are cached. A connection that is switched on and
has **never** fetched is the actionable case, and it's the one thing the app
was previously unable to tell anyone. Both Wodify routes shipped without ever
running against a live gym, so this is not a hypothetical.

## Migrating stored config

`UserConfig` still has `wodify_owner` and `wodify_member` fields. They exist to
be migrated from: a `model_validator` folds them into `gyms` on every read and
clears them, so no read path ever sees the old shape and there's no ordering
dependency on a startup sweep having run.

`db.backfill_gym_connections` persists what the validator already computes. It
is the pattern to copy if a future change needs the same treatment — migrate in
the model for correctness, sweep for tidiness — because it means the migration
can't be half-applied.

The legacy fields also survive on `UserConfigView` and `UserConfigUpdate` as
deprecated mirrors, so a browser holding a page loaded before providers shipped
neither explodes on read nor 422s on save. Delete them once no client reads
them.

## Checklist

1. `GymProvider` member + a slot in `PROVIDER_PRIORITY`.
2. A client module returning `list[GymWod]`, stamping `provider` on each.
3. A `GymProviderSpec` in `PROVIDERS`: fetcher, labels, field declarations.
4. Tests. `tests/test_sugarwod.py` is the template — response parsing, every
   failure mode returning `[]` rather than raising, and the registry assertions
   (`test_every_provider_is_registered` fails automatically if you skip step 3).
5. Nothing in `web/`. If you find yourself editing the frontend, something
   belongs in the registry instead.
