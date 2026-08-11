import { describe, expect, it } from "vitest";
import { inviteState } from "./invites";
import type { Invite } from "./types";

/**
 * The admin screen's only real decision: what an invite row says, and whether
 * it offers a Revoke button.
 *
 * `inviteState` takes `now` rather than reading the clock, so "expired" is a
 * fact about the fixture instead of about when the suite happened to run —
 * these would otherwise start passing or failing on their own in 2027.
 */

const NOW = Date.parse("2026-08-11T00:00:00Z");
const HOUR = 60 * 60 * 1000;

function invite(over: Partial<Invite> = {}): Invite {
  return {
    id: "i",
    email: "athlete@example.com",
    role: "user",
    created_by: "admin",
    created_at: "2026-08-10T00:00:00Z",
    expires_at: new Date(NOW + 24 * HOUR).toISOString(),
    redeemed_at: null,
    redeemed_by: null,
    ...over,
  };
}

describe("inviteState", () => {
  it("is pending while it still has time left", () => {
    const state = inviteState(invite(), NOW);

    expect(state.status).toBe("pending");
    expect(state.revocable).toBe(true);
  });

  it("is expired once its moment has passed", () => {
    const expires_at = new Date(NOW - HOUR).toISOString();

    const state = inviteState(invite({ expires_at }), NOW);

    expect(state.status).toBe("expired");
    expect(state.at).toBe(expires_at);
  });

  it("still offers to revoke an expired invite, because the row is still there", () => {
    // `revoke_invite` deletes on `redeemed_at is None` and never looks at
    // expiry, so hiding the button here would hide a button that works.
    const state = inviteState(invite({ expires_at: new Date(NOW - HOUR).toISOString() }), NOW);

    expect(state.revocable).toBe(true);
  });

  it("cannot revoke a redeemed invite", () => {
    // The server keeps it deliberately: it's the record of how an account came
    // to exist, and a Revoke button that always failed would be worse than none.
    const state = inviteState(invite({ redeemed_at: "2026-08-10T12:00:00Z" }), NOW);

    expect(state.status).toBe("redeemed");
    expect(state.revocable).toBe(false);
  });

  it("reports when it was redeemed, not when it would have expired", () => {
    const redeemed_at = "2026-08-10T12:00:00Z";

    const state = inviteState(invite({ redeemed_at }), NOW);

    expect(state.at).toBe(redeemed_at);
  });

  it("counts redemption first, even for an invite that later lapsed", () => {
    // Redeeming doesn't clear `expires_at`, so any invite redeemed more than
    // its window ago is also past that date. It's redeemed, not expired.
    const state = inviteState(
      invite({
        redeemed_at: "2026-07-01T00:00:00Z",
        expires_at: new Date(NOW - HOUR).toISOString(),
      }),
      NOW,
    );

    expect(state.status).toBe("redeemed");
    expect(state.revocable).toBe(false);
  });

  it("treats the expiry instant itself as not yet expired", () => {
    const state = inviteState(invite({ expires_at: new Date(NOW).toISOString() }), NOW);

    expect(state.status).toBe("pending");
  });

  it("classifies an open invite the same as an address-bound one", () => {
    // A null address only changes who may redeem it, never its lifecycle.
    const state = inviteState(invite({ email: null }), NOW);

    expect(state.status).toBe("pending");
    expect(state.revocable).toBe(true);
  });
});
