# Accounts

How identity works, and why it's built this way. The code is in
`src/short_timer/auth.py` (sessions), `users.py` (records and roles),
`invites.py`, `email_tokens.py`, `passwords.py` and `routers/auth.py`.

## The shape of it

Registration is **invite-only**. An admin mints an invite; redeeming it creates
an account; signing in creates a database-backed session whose token lives in a
cookie. `current_owner` resolves that session to a user id, and every
owner-scoped query filters on the result — that function is still the single
place tenancy is decided, exactly as it was under the shared passcode.

## Passwords

Argon2id at OWASP's current floor: 19 MiB of memory, two iterations, one lane.
Memory-hardness is the point — it's what stops a GPU farm being cheaper per
guess than the server that did the hashing.

Not bcrypt: it silently truncates at 72 bytes, so every call site needs a length
guard that's easy to forget. Not `passlib`, whose last release was 2020.

`needs_rehash` runs on every successful login, so the cost parameters can be
raised later and each account upgrades the next time its owner signs in. That's
the only moment the plaintext exists to re-hash with.

## Sessions

An opaque 256-bit token in a cookie; a row in `sessions` keyed by the token's
**SHA-256**. The token itself is never stored, so a leaked database dump can't
be replayed.

Two clocks bound a session. The **idle** deadline slides forward as it's used
(at most once an hour, so reading a session isn't a write per request); the
**absolute** deadline never moves. Defaults are 30 and 180 days — deliberately
long, because this is a timer opened sporadically on a phone at a gym and being
signed out mid-workout is a real cost. What makes that defensible is that
sessions are now revocable, and the actions that matter re-ask for the password.

**Expiry is enforced in application code, not by the TTL index.** Mongo sweeps a
TTL index about once a minute, and `mongomock` never sweeps at all, so trusting
it would be both insecure and untestable. The index only stops the collection
growing without bound.

## The cookie

`__Host-short_timer_session` in production, `short_timer_session` in plain-http
local dev (the prefix mandates `Secure`, which http can't set). The prefix
matters here: a browser refuses such a cookie if it carries a `Domain`
attribute, which stops anything else under `shortimer.com` from planting a
session cookie this API would read as its own — a real shape of attack, because
the API and the site are deliberately sibling subdomains.

`SameSite=Lax` still constrains hosting: the API must stay on a subdomain of the
site's domain, or the browser won't send the cookie at all.

## Invites

Two kinds, and the difference decides whether we send a confirmation email:

- **Address-bound** — only that address may redeem it. Because the token was
  delivered *to* the mailbox, redeeming it already proves control of it, so the
  account is created verified and no confirmation email is sent.
- **Open code** — anyone holding it may register, so it proves nothing about the
  address typed into the form. Those accounts must verify.

That's not only tidiness: Postmark's free tier is 100 emails a month, and this
halves the mail an ordinary signup costs.

Redeemed invites are kept, not deleted — they record how an account came to
exist. Revoking only works on unredeemed ones.

## Roles

`user`, `staff`, `admin`, on the user record. This axis answers "what may you
see across the whole deployment?" and nothing else.

`staff` exists so the privileged check can't collapse to `is_admin`: support
needs the operator metrics without being able to mint invites. Metrics accept
`OPERATOR_ROLES` (staff and admin); `/api/admin/*` requires admin.

A gym owner is deliberately **not** a role. Being privileged over your own gym's
data is a question of *scope* — which records — not of rank, and folding the two
together produces an enum that grows a member every time a new boundary appears.
That belongs with the plan/tier work; see `roadmap.md`.

`METRICS_ADMIN_USER_IDS` survives as a **break-glass**, not the mechanism. A role
stored in the database is unreadable in exactly the situation where you most
want metrics: the database is sick, or a bad write put the wrong roles in. It
logs a warning when it's what let you in.

`status` (`active` / `disabled`) is a separate axis from role — a disabled admin
is refused everywhere.

## What the API refuses to tell you

Login answers identically for a wrong password and an unknown address, and burns
an equivalent Argon2 verify against a dummy hash when no account exists so the
*timing* doesn't answer the question the message won't. Forgot-password always
returns 204. On an invite-only app the user list is precisely what an attacker
would want.

The one deliberate exception is a disabled account, which says so: the
credential was right, and that's the only way its owner can act on it.

Rate limits on login count per IP **and** per address. Per-IP alone lets a
botnet spray one account; per-address alone lets a gym full of people behind one
WiFi lock each other out. Only failures are charged.

## Email

Postmark, over `httpx` — the send API is one POST with a token header, so an SDK
would be a dependency to carry and audit for nothing.

`EMAIL_ENABLED=false` (the default) logs the link instead of sending it, which
is what lets the whole signup flow be developed and tested without a provider,
an account, or DNS.

### DNS

Send from a subdomain — `send.shortimer.com` — and leave the apex alone. The
apex publishes `v=spf1 -all` and `p=reject; sp=reject; adkim=s; aspf=s`, which
is a deliberate "this domain never sends mail" posture worth keeping.

On the sending subdomain you need SPF (TXT), DKIM, Postmark's return-path
record, and its own `_dmarc.send` TXT.

**Strict alignment is the trap.** `adkim=s` means the `From:` domain must match
the DKIM `d=` exactly, so mail must come from `@send.shortimer.com` — not
`@shortimer.com`. Combined with `sp=reject`, a misconfigured subdomain hard-
bounces rather than landing in spam, so verify the records resolve before
pointing real signups at it.

An MX record on the sending subdomain is required by Postmark for bounce
processing. It's on the subdomain, so apex mail is unaffected.

## Passkeys

A passkey replaces the *password*, not the account. There's still an email
address underneath, because a passkey that only exists on a lost phone needs a
way back in — and that way is the reset flow. Removing your last passkey is
therefore allowed.

Both ceremonies are two round trips: we issue a challenge, the authenticator
signs it, we verify against the stored public key. The challenge is the
anti-replay measure, so it's ours, single-use, and five minutes long — stored
server-side and spent on read, for the same reason session expiry isn't left to
the TTL index.

**The RP ID is permanent and must be the apex.** `WEBAUTHN_RP_ID=shortimer.com`,
never `api.shortimer.com`. It's hashed into the credential at creation and can
never be changed: a credential registered at the apex works from any subdomain,
while one registered at a subdomain could never be used at the apex or a
sibling. Recovering from getting this wrong means a `.well-known/webauthn`
Related Origins file, so it's worth getting right once.

The RP ID and the *origin* differ on purpose — the browser is on
`shortimer.com` while this code runs on `api.shortimer.com`. That's legal
because the RP ID may be a registrable suffix of the origin.

Registration asks for a resident key and authentication sends an empty
`allow_credentials`. Together that's what makes "sign in with a passkey" work
with no email typed first: the browser offers whichever passkey it holds for
the site, and the credential itself says who owns it. It also means the login
challenge endpoint reveals nothing about which accounts exist.

The stored `sign_count` is a clone detector. Many passkeys report a constant 0,
which means "not supported" rather than "cloned", so it's advisory.

## API tokens

Some clients can't hold a session — the MCP server is a local stdio process
with no browser, no cookie, and nobody to redirect to a login screen. Those
present a per-user token instead, minted under Settings → API tokens.

That is also what the MCP specification says a stdio server should do: the
OAuth 2.1 flow it defines (protected resource metadata, resource indicators,
audience-bound tokens) is for HTTP transports, and stdio servers are told to
take credentials from the environment. Exposing the MCP server over HTTP
publicly would mean becoming an OAuth resource server with an authorization
server behind it — a much larger piece of work, and not built.

The token replaced an `MCP_OWNER_ID` setting that merely *named* an owner.
Naming one asserted an identity without proving it, so anyone who could edit
the environment could point the server at any library. A token has to have been
issued to that account, carries scopes (`library:read`, `library:write`), and
can be revoked on its own without touching the account.

It's resolved per tool call rather than cached at startup, so revoking one
takes effect on the next call rather than the next restart. Minting one asks
for the current password, because the credential outlives the session that
created it — revoking every session wouldn't take it back.

**A password reset does not revoke API tokens**, which is a deliberate choice
and worth knowing. Resetting ends every *session*, but a token is a named,
long-lived integration credential, and breaking the MCP server on every routine
password change would be its own kind of wrong. It's also what GitHub and
GitLab do with personal access tokens. Minting one requires the current
password, so a stolen session alone can't produce one — but if you reset
because you think someone *had* your password, revoke the tokens too. They're
listed with a "last used" time under Settings for exactly that.

## Bootstrapping

Invites come from admins, so an empty database can't produce its first one:

```bash
hatch run python scripts/create_admin.py you@example.com
```

That's the only way to create an account without an invite. It prints which
database it's about to write to and asks for confirmation first, because a local
`.env` has historically pointed at the production cluster.

## Still open

- ~~**The MCP server**~~ authenticates with a per-user API token now
  (`MCP_API_TOKEN`); see "API tokens" below.
- **Account deletion** isn't built. When it is, note that the shared
  `parse_cache` is content-addressed and carries no user linkage, so there is
  nothing user-identifying in it to delete; user-submitted entries age out on
  their own after a year (`parse_cache.USER_RETENTION`).
