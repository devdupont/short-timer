import type { Invite } from "./types";

/**
 * What an invite row means, separated from how the admin screen draws it.
 *
 * This lives outside the component so it can be tested as plain logic — the
 * app has no jsdom or component-testing setup, and this is the only part of
 * the admin screen with a decision in it.
 */

export type InviteStatus = "pending" | "redeemed" | "expired";

export interface InviteState {
  status: InviteStatus;
  /** The timestamp worth showing for this status. */
  at: string;
  /** Whether the server would still delete it — see the note below. */
  revocable: boolean;
}

/**
 * Classify an invite, taking `now` so expiry can be tested without the clock.
 *
 * Expiry is derived here rather than sent by the server: the row carries
 * `expires_at` and nothing else, and an invite that lapsed a minute ago is no
 * different in the database from one that lapses a minute from now.
 *
 * An *expired* invite is still revocable, which looks wrong and isn't:
 * `revoke_invite` deletes on `redeemed_at is None` and never looks at expiry,
 * so the row is genuinely still there to delete. Only redemption takes it off
 * the table — a redeemed invite is the record of how an account came to exist,
 * and the server deliberately keeps it.
 */
export function inviteState(invite: Invite, now: number = Date.now()): InviteState {
  if (invite.redeemed_at !== null) {
    return { status: "redeemed", at: invite.redeemed_at, revocable: false };
  }
  if (new Date(invite.expires_at).getTime() < now) {
    return { status: "expired", at: invite.expires_at, revocable: true };
  }
  return { status: "pending", at: invite.expires_at, revocable: true };
}
