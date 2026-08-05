# Metrics

Recording landed before any dashboard, on purpose: **events can't be
backfilled**. A chart drawn on top of good data is a week's work whenever it's
wanted; a month of history nobody captured is gone. So this ships the
instrumentation and two aggregation endpoints, and no UI.

## The two decisions worth knowing

**Events store tokens, never dollars.** A token count is a fact and stays true;
a dollar figure is a *policy* applied to that fact, and the policy moves —
`claude-sonnet-5` is on introductory pricing until 2026-08-31, so every cost
computed today is wrong from September. Prices live in `MODEL_PRICES` and are
applied when a question is asked, which means correcting a price re-prices all
of history instead of leaving a column with two different dollars in it.

The corollary: a model that isn't in `MODEL_PRICES` reports its tokens with no
cost attached, and marks the total `cost_is_complete: false`. A sum that
silently omits a model would be worse than one that admits a gap.

**Recording never breaks the thing it measures.** `record` swallows its own
failures, same discipline as the feed fetchers. A metrics write that 500s a
parse the user already paid for is a far worse bug than a missing data point.

## What's recorded

| Event | Answers | Notes |
|---|---|---|
| `model_call` | What is the Anthropic bill made of? | The only event that costs money. Carries token counts and a `purpose` — a user's paste and a feed pre-warm are the same call with very different economics. |
| `parse` | Are the caches earning their keep? | The demand side. `library_hit` / `pool_hit` / `model_call` / `failed`. |
| `feed_refresh` | Is each source actually working? | `ok` is the signal. `rows` counts days written *on that pass*, so it's 0 for a refresh that was skipped as still fresh — don't read a zero as a failure. |
| `workout_started` | Is anyone training, or just browsing? | Its own call from the timer, not inferred from a read — loading a workout and running it are very different signals. |
| `workout_completed` | Do people finish what they start? | Paired with the above it gives a completion rate — programming that fits versus programming people abandon. Carries elapsed clock time, **not** a result: reps, load and score need a model that doesn't exist (`docs/exports.md`). |
| `login` | How many people are active? | The basis of any MAU number. |

Deliberately **not** recorded: workout text, credentials, or anything derived
from them. An event carries ids, counts and enum labels — enough for "how much"
and "how often", never "what did they paste".

## Where the instrumentation sits

`model_call` is recorded inside `parse_workout_text` rather than at its six call
sites. That function *is* the moment money is spent, so it's the one place that
can't miss a call — and a seventh caller gets counted without knowing metrics
exist.

`parse` is recorded in `_parse_or_cached`, because only the router knows which
tier served the request. Those two events together are the whole cost picture:
demand, and what demand cost after the caches took their share.

## The two endpoints

`GET /api/metrics/me` — the caller's own usage. No cost figures. This is the
shape the eventual per-plan usage meter and gym-owner view get built from.

`GET /api/metrics/operator` — everything, across every user, including spend.
**Gated on the caller's role**: `staff` or `admin` (`OPERATOR_ROLES` in
`models.py`). It returns 404 rather than 403 to anyone else — an endpoint you
may not read shouldn't confirm it exists.

`METRICS_ADMIN_USER_IDS` survives as a **break-glass**, not the mechanism. A
role stored in the database is unreadable in exactly the situation where you
most want metrics: the database is sick, or a bad write put the wrong roles in.
It defaults to empty, and logs a warning when it's what let you in.

See `docs/accounts.md` for the role model, including why a gym owner doesn't
fit on the same axis as an admin.

## Retention and scale

Raw events, one document each, aged out by a TTL index at
`EVENTS_RETENTION_DAYS` (400 by default — long enough to compare a month
against the same month last year).

Aggregating into rollups would be premature at this volume: the aggregations
run over an indexed `(type, at)` and there are hundreds of events a day, not
millions. The signal to revisit is the operator endpoint getting slow, and the
first move is a nightly rollup collection rather than a shorter window — the
history is the point.

## What this doesn't do yet

**Gym-owner analytics.** A gym wants "how many of my members ran today's
workout", which needs a member→gym relationship that doesn't exist until real
accounts do. `workout_started` carries the workout id, so the events being
captured now will answer it retroactively once that relationship exists — which
is precisely the argument for recording before building the view.

**Cost per user.** `model_call` carries `owner_id`, so the query is trivial;
it's just not exposed, because with one shared user it would only ever report
one number.
