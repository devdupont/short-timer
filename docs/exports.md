# Exporting back out to someone's own training log

Not built. This is the design and, more usefully, the reason it isn't built
yet — which is not the thing it looks like from the outside.

## The blocker is that there is nothing to export

The obvious reading is "we need a Hevy client". That's the easy half. The real
problem is that **shortimer knows the plan, not the outcome.**

It knows a workout was 21-15-9 thrusters and pull-ups, and since PR #20 it
knows the clock started and how long it ran. It does not know how many rounds
you actually got, what you loaded the bar to, whether you scaled the pull-ups,
or what you'd call your score. Hevy's model is sets carrying `weight_kg`,
`reps`, `distance_meters`, `duration_seconds` — every one of which is a fact
about what happened, and none of which this app has.

Export today would therefore push "here is the workout I was told to run",
which is a *routine*, not a training log entry. That's worse than nothing: it
fills someone's history with prescriptions they may not have followed.

So the first piece of work is a result model, and it's a product decision
before it's a schema one.

## Export-through, not store-then-export

The constraint that should shape it: **workout history is explicitly not what
this app is for.** The stated preference is to hook back out into whatever
tracker someone already uses, and that preference is worth defending in the
design rather than just in the roadmap.

Concretely, that means capturing a result at the end of a session and pushing
it straight to the destination, keeping only what a retry needs — not building
a history feature and adding export to it. The difference matters:

| Store-then-export | Export-through |
|---|---|
| Results live here; export is a copy | The tracker is the system of record |
| Implies browsing, editing, deleting past sessions | Implies one screen at the end of a workout |
| Implies analytics, PRs, trends — a second product | Implies none of that |
| Retention, deletion and export-my-data all become our problem | Mostly someone else's problem |

The pull toward the first column will be constant, because each individual
step is small. The check is: does this feature make sense if a user's history
lives entirely in Hevy? If not, it belongs in Hevy.

The minimum this implies is a small `export_queue` — a pending push, its
destination, and enough payload to retry — that is emptied rather than
accumulated. Not a `sessions` collection.

## The shape it should take

A **destination registry**, mirroring `gym_providers.py` exactly and running
the other way. That pattern is proven now (see `docs/gym-providers.md`) and the
symmetry is real:

| Gym providers (in) | Export destinations (out) |
|---|---|
| Per-user encrypted credential | Same |
| Registry declares fields + labels | Same |
| Settings renders generically | Same |
| Fetch returns `list[GymWod]` | Push takes a result, returns success |
| Failure degrades to a stale feed | Failure needs a retry, and that's the difference |

The one genuine asymmetry is at the bottom row. A failed *fetch* can be
swallowed — the user sees yesterday's workout and nothing is lost. A failed
*push* has lost something the athlete did and can't reproduce, so it has to be
queued and retried rather than logged and dropped. That is the reason exports
can't simply reuse `gym_providers` as-is, and it's worth knowing before
starting.

`workout_completed` (PR #21) is the trigger point. It already fires at exactly
the right moment, from all three ways a session can end.

## What's verified about the Hevy API

Probed live on 2026-08-05. Unlike SugarWOD, nothing here needed guessing: the
API publishes an OpenAPI spec, embedded in the Swagger UI at
`https://api.hevyapp.com/docs` (the spec object lives inside
`/docs/swagger-ui-init.js`; there is no `/openapi.json`).

- **Base:** `https://api.hevyapp.com`, header `api-key: <key>`.
- **Auth failures are a clean `401 InvalidApiKey`** — for a bad key, a missing
  key, and a key sent under the wrong header name alike. Note it does *not*
  distinguish "no key" from "wrong key" the way SugarWOD does.
- **Pro-gated.** The key comes from `hevy.com/settings?developer`, and the
  spec's own description says the API "is only available to Hevy Pro users".
  Spec version is `0.0.1` and it warns it may change.
- **Endpoints:** `GET|POST /v1/workouts`, `GET|PUT /v1/workouts/{id}`,
  `GET /v1/workouts/count`, `GET /v1/workouts/events`,
  `GET|POST /v1/routines`, `GET|POST /v1/routine_folders`,
  `GET|POST /v1/exercise_templates`, `GET /v1/exercise_history/{templateId}`,
  `GET|POST /v1/body_measurements`, `GET /v1/user/info`.

`POST /v1/workouts` takes `title`, `description`, `start_time`, `end_time`,
`is_private`, and `exercises[]`. Each exercise carries an
`exercise_template_id`, optional `superset_id` and `notes`, and `sets[]`. Each
set carries `type` (`warmup` / `normal` / `failure` / `dropset`), `weight_kg`,
`reps`, `distance_meters`, `duration_seconds`, `custom_metric`, and `rpe`
(enum `6, 7, 7.5, 8, 8.5, 9, 9.5, 10`).

⚠️ **The spec marks nothing as required** — every property on all three request
schemas has an empty `required` list. That is almost certainly the spec being
loose rather than the API being permissive, so validation rules will be
discovered at runtime. Write the client to tolerate a 400 with a useful message
rather than assuming a well-formed body is enough.

## The actual hard part: movement names

`exercise_template_id` is Hevy's own identifier. shortimer has free text
("Thrusters", "thruster 95/65 lb", "KB swings") because that's what the LLM
parsed out of whatever the gym wrote. Bridging those is the integration.

Three tools, in order of preference:

1. **Match on `title`** against `GET /v1/exercise_templates`, which returns
   `id`, `title`, `type`, `primary_muscle_group`, `secondary_muscle_groups`,
   `equipment_category` and `is_custom`. Note the default `pageSize` is **5**,
   so building a full catalogue map is many requests — fetch once and cache it,
   don't resolve per workout.
2. **A curated map** for the CrossFit vocabulary Hevy is unlikely to match
   cleanly on its own — thruster, wall ball, box jump, double-under, toes-to-bar
   and so on. Small, static, and the highest-value hundred lines in the feature.
3. **`POST /v1/exercise_templates`** creates a custom exercise (`title`,
   `exercise_type`, `equipment_category`, `muscle_group`, `other_muscles`).
   The fallback for anything still unmatched, and the reason nothing has to be
   dropped on the floor.

This is a specific case of a bigger question — canonical movement identities,
which every platform in this space maintains and which also underpin personal
bests and percentage-based loading. See *Canonical movement names* in
`docs/roadmap.md`, which covers the alias problem, why a deterministic table
should lead rather than the parser, and the open question of whose vocabulary
to own.

## Order of work, when it's picked up

1. Design the result model. Product decision, not a schema one — the smallest
   thing that makes a training-log entry honest.
2. A result-capture screen at the end of a session, and the `export_queue` it
   writes to.
3. The destination registry, with Hevy as the first entry.
4. The name-mapping layer, starting with the curated map.

Steps 1 and 2 are the whole of the risk. Step 3 is the pattern already in the
codebase, and step 4 is tedious rather than hard.
