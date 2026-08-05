# Pricing and feature gating

Written 2026-08-04, before user accounts. A proposal, not a decision — but the
unit economics below are measured rather than estimated, and they're what any
pricing decision has to survive.

## What a parse actually costs

Measured against the real `SYSTEM_PROMPT` and `_WORKOUT_TOOL` from `llm.py`,
via `messages.count_tokens` over the 20 workouts in
`tests/fixtures/workouts.json`:

| | tokens |
|---|---|
| Fixed prefix (system prompt + tool schema) | **2,800** |
| Median total input, benchmark workout | 2,843 |
| Largest fixture | 2,908 |
| Largest input the server will accept (`max_workout_text_chars` = 20,000) | ~7,800 |
| Output ceiling (`max_tokens`) | 2,048 |

The striking number is the first one: **98% of a typical parse's input is the
fixed prefix**, and the workout itself is noise around it. That shapes
everything downstream — the cost is per *call*, essentially flat, regardless of
what got pasted.

At `claude-sonnet-5` list pricing ($3 / $15 per MTok):

- **Typical parse: ~$0.013.** Measured, not estimated — a real three-round
  chipper through the live API came back at **2,978 in / 265 out = $0.0129**.
  The input matches the `count_tokens` figure above; the output is about half
  what was first assumed, because a parsed workout is a compact tool call
  rather than prose.
- **Worst case: ~$0.054** (a maximum-length paste that fills `max_tokens`)

Introductory pricing runs at $2/$10 through 2026-08-31, which makes a typical
parse ~$0.011 — don't build the model on that number, it expires.

## What already controls the cost

Two mechanisms are in the code and doing real work:

**`parse_cache` is the single biggest lever, and it's already built.** The pool
is keyed by source-text hash and shared across all users, so a given piece of
text is parsed **once, ever**. For the public feeds this is decisive: one
crossfit.com parse serves every user forever, and the same holds for Concept2
and Hybrid. Feed traffic is therefore effectively free at any scale, no matter
how many users read it.

**Rate limits** (`ratelimit.py`) bound the rest: 60 model calls per hour per
subject, 500 per hour globally. Crucially, `_guard_llm_call` only charges on a
*cache miss* — reusing a parse costs nobody anything. That's already the right
shape for a metered quota.

## What isn't being used yet

**Prompt caching.** The 2,800-token prefix is byte-identical on every single
request and sits above `claude-sonnet-5`'s 1,024-token cache minimum. A cache
read costs ~0.1×, which drops input from $0.0086 to ~$0.001 and takes a typical
parse from ~$0.016 to **~$0.0085 — roughly half**.

The honest caveat: the default TTL is 5 minutes and a cache write costs 1.25×,
so this only pays once sustained volume exceeds about one parse per five
minutes. Below that it's a small net loss. It costs one `cache_control` marker
to add, so the sensible move is to add it and watch
`usage.cache_read_input_tokens` — if it stays at zero, traffic is too sparse to
benefit yet, and nothing is lost by leaving it in place until it isn't.

**Batch API (50% off)** fits the nightly feed pre-warm (`ensure_wods_parsed`),
which nobody is waiting on. Small absolute saving, near-zero risk.

**A cheaper model** is available ($1/$5 for Haiku 4.5) and I'd argue against
it. Parse quality *is* the product — a workout that runs the wrong clock is
worse than no workout — and the whole cost structure above says the model isn't
where the money goes anyway.

## The gap: there is no budget ceiling

The global limit is 500 calls/hour with **no daily or monthly cap**. Sustained
at that ceiling, that's ~360,000 parses/month ≈ **$4,600/month** at the measured
per-parse cost. The cap is a burst guard, not a budget; nothing in the system
can say "stop, we've spent enough this month". That's the one control worth
adding before opening signups, independent of whatever pricing lands.

Since 2026-08-05 the *actual* number is at least observable:
`GET /api/metrics/operator` reports token totals and estimated spend per model
over a window (see `docs/metrics.md`). A budget still has to be built; this at
least means it can be set from data rather than guessed.

## The insight the tiers should be built on

**Cost scales with distinct workouts, not with users.**

- Feeds cost nothing per user — one parse, shared.
- The timer, the visualizer, the builder, the audio, the wake lock: all free to
  serve. Zero marginal cost.
- **Pasting arbitrary text is the only thing that costs money**, and only on a
  cache miss.

That maps almost exactly onto a tier boundary, which is a good sign: the meter
matches the cost, so the pricing won't feel arbitrary to a user.

## Proposed tiers

### Free — "the timer"

- **Every public feed, unlimited.** crossfit.com, Concept2, Hybrid. Pre-parsed
  server-side; a user costs nothing.
- **The builder, unlimited.** Hand-built workouts never touch the LLM.
- **The benchmark seed** (Murph, Fran, Cindy, …) — pre-parsed, free.
- **Library capped at ~25 saved workouts.**
- **10 parses/month**, counted only on a genuine model call.

Ten is deliberately enough to fall in love with the product and not enough to
run a training week on it.

### Pro — ~$4/month, ~$36/year

- **200 parses/month.** Even if every one were a worst-case max-length paste,
  that's $10.80 — and the measured typical is a fifth of that, so the margin
  holds without needing users to under-consume.
- Unlimited library, gym connection (Wodify / SugarWOD), custom feeds.
- Result export (Hevy write-back, when it lands) and MCP server access.

On the price: the marginal cost of even a heavy Pro user is a couple of
dollars, this is a *timer*, and the athlete-facing comparison set (SugarWOD,
btwb) sits at $0–5/month. Price it where the annual plan is an impulse rather
than a decision.

### Gym / affiliate — ~$20–30/month per gym

This is the actual business, and it's a different product.

The economics are the best in the whole system: **a gym's daily workout is
parsed once and every member reads the cache**. The marginal cost of the
sixtieth member at a gym is approximately zero. Nothing else here scales that
way.

What a gym is buying:

- A whiteboard/timer view for the class TV, with the day's workout already
  loaded on the clock.
- Members open their phone and the workout is there, timer ready, no typing.
- Coach-side scaling and overrides.

**Members of a paying gym get Pro at no extra charge.** That's not generosity,
it's the acquisition engine — giving 60 members Pro genuinely costs near
nothing, and it converts one sales conversation into sixty users of the
consumer product.

## What to gate, and what never to gate

**Gate on parse volume, plus a few structural limits.** Library size, gym
connections, custom feeds, export/API/MCP access.

**Never gate the clock.** No limits on the timer, the modes, the visualizer,
the wake lock, or the audio. A workout timer that stops mid-workout is a broken
product, and none of it costs anything to serve. The free tier should be a
genuinely good timer that happens to have a small paste allowance — not a
crippled one.

## What accounts have to add

The plumbing is closer than it looks:

- `subject_for()` already switches from IP to owner the moment `current_owner`
  stops returning `DEFAULT_OWNER_ID`. Per-user metering needs no new plumbing.
- A monthly quota is a new `RateLimit` scope with `window_seconds` of 30 days.
  One caveat: fixed windows allow up to 2× the limit across a boundary, which
  is fine for an hourly abuse guard and *not* fine for a billing meter — the
  monthly quota specifically should key on the calendar month rather than a
  floor-divided window.
- **Plan belongs on `User`**, beside `config`. `_guard_llm_call` and
  `writes_allowed` are the only two seams that need to read it.
- Cache hits must not decrement the quota. That's already how
  `_parse_or_cached` is structured — preserve it, and say so in the UI, because
  "loading today's WOD didn't use a credit" is a good surprise.

## On hosted auth (DeScope et al.)

The concern in the original framing is correct: a fixed per-MAU fee against a
mostly-free user base inverts the model. At $0.02–0.05/MAU a free user who
costs nothing to serve starts costing something, and free users are the whole
top of the funnel here.

The counterweight is that this app has already built the expensive parts.
Signed session cookies, `current_owner` as the single tenancy seam, owner-scoped
queries, encrypted per-user credentials, rate limits keyed on the subject — all
done. What's actually missing is password hashing, a signup route, and token
revocation. That's a small, well-understood piece of work against a permanent
per-user cost.

**Recommendation: build it locally.** Revisit a hosted IdP when SSO for gym
staff shows up as a sales requirement — that lands at the gym tier, where the
customer is paying enough to carry the fee, and where the buyer actually cares
about it.

## Forward-looking

- **The gym tier is the business**; consumer Pro funds the roadmap and feeds it
  users.
- **Result logging** is the obvious next loop — the timer already knows what
  happened, and Hevy's write API means shipping it doesn't require building a
  logging product (see `docs/integrations.md`).
- **Class mode / TV whiteboard** is the feature a gym would actually pay for,
  and it's mostly a front-end problem on top of what exists.
- **Programming publishers** (Mayhem, CompTrain, Street Parking and similar)
  are a possible revenue-share feed partnership rather than a scrape — the
  right conversation once there are members to offer them.
