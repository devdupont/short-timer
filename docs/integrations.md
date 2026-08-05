# Platform integrations

Surveyed 2026-08-04, after Wodify shipped. Findings are about *external*
services, so none of this is recoverable from the repo — it's here so the next
decision doesn't start from a blank search box. Companion to
`docs/feeds.md`-adjacent notes in [[roadmap]]; the public *feed* survey lives in
memory and covers crossfit.com, Concept2, Hybrid Calisthenics, Darebee, Zwift,
Hyrox and the exercise-catalog APIs.

## What actually qualifies

short-timer turns **workout text** into a runnable clock. That makes the bar
narrower than "fitness platform with an API":

- It must expose a **specific day's programming as text**, for a person who is
  a member rather than an employee.
- The text has to be prose or structured-enough prose. An image is a vision
  call per workout (this is why Darebee was rejected).
- The access route has to work for one gym or one athlete, without a
  partnership.

Most gym software fails the *first* test, not the third. Class schedules,
billing, check-ins and CRM are what these platforms sell; the whiteboard is a
side feature, and several of the biggest don't expose it at all.

## Build next: SugarWOD

The strongest remaining candidate, and structurally the same problem
`wodify.py` already solved — two routes, one per role:

- **Gym-member route — documented, but not currently reproducible.** The KB
  articles describe a per-affiliate public feed with no authentication:
  `https://app.sugarwod.com/public/api/v1/affiliates/{GYM_ID}/workouts/days/{N}/html`
  plus an `/rss` variant, filtered by `?tracks=["workout-of-the-day"]`.
  `www.sugarwod.com` 301s to `app.`.

  **Probing it does not confirm that shape.** Every format segment tried
  (`html`, `rss`, `json`, `xml`, `text`, `ical`, `csv`) returns the body
  `Invalid format.` regardless of the gym id — including ids shaped as slugs,
  integers, UUIDs and 24-char hex, which rules out the id being what's
  rejected. Dropping the format segment returns a generic SugarWOD HTML shell.
  So the route exists and answers, but the documented path or its parameters
  are stale, and the current shape can't be recovered without a real gym
  account or the KB article (which fails TLS negotiation from here).

  Treat this route as **blocked on access, not on effort** — the same position
  the Wodify member route was in. One gym owner with a SugarWOD login settles
  it in a minute.
- **Gym-owner route.** `https://api.sugarwod.com/v2`, key from
  `/gyms/settings/developer-keys`, sent as `Authorization: <key>` (a
  `?apiKey=` query form exists — don't use it, it lands in logs).
  `GET /workouts?dates=YYYYMMDD-YYYYMMDD&track_id=…` with a **7-day maximum
  range**, JSON:API envelope, `page[skip]`/`page[limit]` paging, 429 on
  throttle. It also exposes CrossFit HQ mainsite programming through the same
  shape.

This is a second adapter, not a new architecture: same owner/member split,
same credential-storage path (`SECRETS_KEYS`), same cache shape as
`wodify_cache`. The one caveat carried over from Wodify — the field casing in
the JSON envelope comes from documentation, not observation — applies here
too, so keep `_first_str`-style tolerance.

**Build the owner route first.** It's the one whose shape is documented well
enough to write against, and it's the route the paying customer uses anyway
(see `docs/pricing.md` — the gym is the buyer). The member route drops into the
same provider slot once someone confirms its URL.

> **Shipped**, as `src/short_timer/sugarwod.py` / `GymProvider.SUGARWOD_OWNER`.
>
> **Partly verified against the live API.** `https://api.sugarwod.com/v2/workouts`
> answers, and it distinguishes *no* key (`{"errors": {"message": "No API Key
> found in request.", "code": 999999}}`) from an *unrecognised* one
> (`"Invalid API Key."`) — which confirms the endpoint, and confirms the server
> reads a bare `Authorization: <key>` header. What can't be checked without a
> real key is the response body: the attribute names come from documentation,
> so `_first` and `_as_date` tolerate several spellings rather than hard-failing.
>
> **A rejected key comes back as HTTP 400, not 401.** Worth knowing before
> writing any other client against this API. The member route is a `TODO` in
> that module's docstring, not a design question.

## Rejected, with reasons

**PushPress** — the highest CrossFit-affiliate adoption of any platform, and
the most tempting name on the list. Its Platform API is nonetheless the wrong
plane. The published TypeScript SDK's operation list is customers, check-ins
(class / appointment / event / open-facility), classes, class types, events,
appointments, plan enrollments, invitations, company, API keys and webhooks.
There is **no workout, WOD, or programming resource**. It can tell you a class
happened; it cannot tell you what the class did. (The SDK also self-describes
as early alpha with breaking changes expected.)

**Zen Planner** — now behind Daxko's partner program; access requires talking
to a sales rep. The documented surface is Class, Location, Membership and
People. Scheduling, not programming.

**Mindbody** — partner approval (1–2 weeks) *and* a paid Mindbody
subscription. `GetClasses` returns schedules. Its market is studio/boutique
fitness, not CrossFit-style daily programming. Wrong shape and the highest
friction of anything surveyed.

**Beyond the Whiteboard (btwb)** — no public API. The only trace is a
years-old feature request on their support forum asking for one. Athlete-side
logging, and a plausible *competitor* to the result-logging feature rather
than a partner. Watch; don't build.

**TrainHeroic** — API is explicitly closed. There is also no plan or history
export: their own support docs say everything is designed to be delivered
in-app, and a coach request for spreadsheet export is open and unimplemented.
Zapier is the only automation surface. Nothing to integrate with.

**TeamBuildr / TrueCoach** — strength-and-conditioning coach→athlete
platforms. TeamBuildr advertises API docs; TrueCoach has an unofficial
community API repo. Neither publishes a member-readable daily-programming
feed, and both are a different sport (1:1 coaching, not a class whiteboard).
Low priority rather than a hard no.

**Strava** — reject, and the reason is worth keeping. The 2026 API agreement
(a) forbids third-party apps from displaying a user's activity data to anyone
but that user, (b) forbids using API-obtained data to train AI models or
similar applications, and (c) explicitly forbids operating "MCP Servers or
agent-mediated interfaces" that expose Strava data. short-timer ships an MCP
server (`mcp_server.py`) and runs every piece of text through an LLM. Two of
those three clauses land directly on the architecture, so even a read-only
integration is on the wrong side of the terms. Standard-tier developers now
also need a paid Strava subscription. This is a policy rejection, not a
technical one, and it won't age out.

**Wodwell** — a large public library of named workouts. `robots.txt` permits
crawling but sets `crawl-delay: 600` — one request per ten minutes. That kills
bulk import, and it makes an on-demand "look up this named workout" feature
unusable even though nothing else about it is prohibited.

**Wearables (Garmin, Whoop, Fitbit, Apple HealthKit, Google Health Connect)** —
these are results-*out*, not programming-*in*. The platform-level aggregators
(HealthKit, Health Connect) are the sensible entry point rather than per-brand
APIs, but both need a native mobile app, which short-timer isn't. Forward-
looking, not next.

## The other direction: Hevy

Worth calling out separately because it is the only surveyed platform with a
genuinely open, athlete-owned REST API: `https://api.hevyapp.com/v1`, `api-key`
header, key self-served from `hevy.com/settings?developer` (Hevy Pro only). It
covers listing and fetching workouts, **creating and updating** them, routines
and routine folders, exercise templates, and webhook subscriptions.

That write path is the interesting half. It's the first way short-timer could
close the "what did I score" loop — push a completed session to the athlete's
existing log — without building and maintaining a logging product of its own.
The Pro gate on the key means it's a feature for engaged users, which is
exactly who a paid tier is aimed at (see `docs/pricing.md`).

Reading Hevy *routines* as a programming source is possible but much less
interesting: routines are strength templates, not a dated daily workout, and
the user authored them in the first place.

**Designed, not built** — see `docs/exports.md`, which has the verified API
shape (it publishes an OpenAPI spec) and the finding that matters: the blocker
is not the client, it's that this app has no *result* to send.

## The highest-leverage build: a generic feed adapter

Three separate findings point the same way. SugarWOD publishes RSS. Gyms
embed their whiteboard on their own sites. Programming publishers post to
blogs. And the pipeline that would consume all of them already exists
end-to-end: fetch → `html_text` → LLM parse → `parse_cache`.

A user-supplied feed URL would therefore cover SugarWOD RSS, gym sites, and
most publishers with one adapter instead of N — and it degrades gracefully,
because a source that stops parsing well is one user's problem rather than a
broken integration.

**The cost is SSRF, and it should be priced in before the feature is
scheduled.** Every outbound host in the codebase today is a hardcoded
constant (`api.wodify.com`, `app.wodify.com`, `log.concept2.com`,
`crossfit.com`, `hybridcalisthenics.com`). A user-supplied URL is the first
time the server fetches somewhere the deployment didn't choose, which means it
needs, at minimum: scheme allowlisting, DNS resolution checked against private
and link-local ranges (including on every redirect hop — the existing clients
all use `follow_redirects=True`), a response size cap, and the fetch running
under the same per-owner rate limit as a parse. Cloud metadata endpoints are
the specific thing being defended against; `169.254.169.254` is reachable from
a container app.

That is a contained piece of work, but it is real work, and it is the reason
this feature is bigger than "just take a URL".

## Summary

| Platform | Programming feed? | Route | Verdict |
|---|---|---|---|
| Wodify | Yes | Public whiteboard + Program API | **Shipped** |
| SugarWOD | Yes | Public per-affiliate feed + v2 API key | **Build next** |
| Hevy | Write-back | Public REST, athlete API key (Pro) | **Build for results** |
| PushPress | No | CRM/ops only | Reject |
| Zen Planner | No | Partner-gated, scheduling | Reject |
| Mindbody | No | Partner-gated, scheduling | Reject |
| btwb | No | No public API | Watch |
| TrainHeroic | No | API closed, no export | Reject |
| TeamBuildr / TrueCoach | No | Coach→athlete, no member feed | Low priority |
| Strava | N/A | Terms forbid LLM + MCP use | Reject (policy) |
| Wodwell | Library | `crawl-delay: 600` | Reject |
| Wearables | Results-out | Needs a native app | Later |
