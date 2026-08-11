import { afterEach, describe, expect, it } from "vitest";
import { clearUrl, hasPendingAuthLink, readLocation } from "./authLinks";

/**
 * Reading the emailed links off the address bar.
 *
 * `App` asks `hasPendingAuthLink` whether a visitor who already has a session
 * is here to use the app or to finish an emailed flow. It used to not ask at
 * all: the gate only mounted when signed out, so clicking a confirmation link
 * while signed in landed on the home page and dropped the token — and since
 * every re-issued link behaved identically, the address could never be
 * confirmed at all.
 *
 * These drive the real `window.location` through `history`, so they exercise
 * the same URL parsing a browser would rather than a hand-shaped stand-in.
 */

function at(url: string): void {
  window.history.pushState({}, "", url);
}

afterEach(() => {
  window.history.pushState({}, "", "/");
});

describe("readLocation", () => {
  it("picks the screen off the path and the token off the query", () => {
    at("/register?token=abc123");
    expect(readLocation()).toEqual({ screen: "register", token: "abc123" });

    at("/reset?token=abc123");
    expect(readLocation()).toEqual({ screen: "reset", token: "abc123" });

    at("/verify?token=abc123");
    expect(readLocation()).toEqual({ screen: "verify", token: "abc123" });
  });

  it("tolerates a trailing slash, which mail clients like to add", () => {
    at("/verify/?token=abc123");
    expect(readLocation()).toEqual({ screen: "verify", token: "abc123" });
  });

  it("url-decodes the token", () => {
    // Tokens are url-safe base64, but the query parser is what guarantees a
    // stray encoded character survives the round trip rather than truncating.
    at(`/verify?token=${encodeURIComponent("a+b/c=")}`);
    expect(readLocation().token).toBe("a+b/c=");
  });

  it("reports a missing token rather than inventing one", () => {
    // The screens render their own "this link is missing its code" message,
    // which is friendlier than a request that fails for unexplained reasons.
    at("/verify");
    expect(readLocation()).toEqual({ screen: "verify", token: null });
  });

  it("never carries a token off the plain sign-in page", () => {
    at("/?token=abc123");
    expect(readLocation()).toEqual({ screen: "login", token: null });
  });
});

describe("hasPendingAuthLink", () => {
  it.each(["/register", "/reset", "/verify"])("is true at %s", (path) => {
    at(`${path}?token=abc123`);
    expect(hasPendingAuthLink()).toBe(true);
  });

  it("is false on the pages a signed-in visitor actually uses", () => {
    at("/");
    expect(hasPendingAuthLink()).toBe(false);
  });

  it("is true even with no token, so a broken link still explains itself", () => {
    // Returning false here would drop someone with a truncated link straight
    // into the app, which is the silence this whole thing exists to remove.
    at("/verify");
    expect(hasPendingAuthLink()).toBe(true);
  });
});

describe("clearUrl", () => {
  it("takes the token out of the address bar", () => {
    // A token left in the URL survives in history and in anything the user
    // copies out of the address bar, long after it has been spent.
    at("/verify?token=abc123");

    clearUrl();

    expect(window.location.pathname).toBe("/");
    expect(window.location.search).toBe("");
    expect(hasPendingAuthLink()).toBe(false);
  });
});
