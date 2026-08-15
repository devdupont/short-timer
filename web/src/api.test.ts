import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getMe } from "./api";

/**
 * How a failed response becomes the sentence the user reads.
 *
 * Our own handlers raise `HTTPException(detail="a sentence")`, but FastAPI's
 * request validation returns a *list* of `{loc, msg, ...}` objects instead.
 * The client passed `detail` straight through, so every 422 in the app
 * rendered as the string "[object Object]" — on the sign-in form, that was the
 * entire feedback for a rejected address.
 *
 * These go through `request` rather than testing the flattening directly,
 * because the shape being handled is whatever the server actually sends.
 */

function respondWith(body: unknown, init: ResponseInit): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(typeof body === "string" ? body : JSON.stringify(body), init)),
  );
}

/** The `ApiError` a failed call rejected with. Fails the test if it resolved. */
async function errorFrom(call: () => Promise<unknown>): Promise<ApiError> {
  try {
    await call();
  } catch (err) {
    expect(err).toBeInstanceOf(ApiError);
    return err as ApiError;
  }
  throw new Error("expected the request to reject");
}

const UNPROCESSABLE: ResponseInit = { status: 422, statusText: "Unprocessable Content" };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request error messages", () => {
  it("reads the message out of a FastAPI validation error", async () => {
    respondWith(
      {
        detail: [
          {
            type: "value_error",
            loc: ["body", "email"],
            msg: "value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.",
            input: "someone@example.test",
          },
        ],
      },
      UNPROCESSABLE,
    );

    const err = await errorFrom(getMe);

    expect(err.status).toBe(422);
    expect(err.message).toContain("not a valid email address");
    expect(err.message).not.toContain("[object Object]");
  });

  it("joins every complaint when more than one field is wrong", async () => {
    respondWith(
      {
        detail: [
          { loc: ["body", "email"], msg: "Field required" },
          { loc: ["body", "password"], msg: "Input should be a valid string" },
        ],
      },
      UNPROCESSABLE,
    );

    const err = await errorFrom(getMe);

    expect(err.message).toBe("Field required Input should be a valid string");
  });

  it("passes a plain-string detail through untouched", async () => {
    respondWith({ detail: "Incorrect email or password." }, { status: 401, statusText: "Unauthorized" });

    const err = await errorFrom(getMe);

    expect(err.status).toBe(401);
    expect(err.message).toBe("Incorrect email or password.");
  });

  it("falls back to the status text when there is no detail", async () => {
    respondWith({}, { status: 500, statusText: "Internal Server Error" });

    expect((await errorFrom(getMe)).message).toBe("Internal Server Error");
  });

  it("falls back when the body is not JSON at all", async () => {
    // A proxy or gateway failing in front of the app sends HTML, not our shape.
    respondWith("<html>502 Bad Gateway</html>", { status: 502, statusText: "Bad Gateway" });

    expect((await errorFrom(getMe)).message).toBe("Bad Gateway");
  });

  it("falls back rather than rendering a list of things that aren't messages", async () => {
    // Nothing sends this today; the point is that an unfamiliar shape degrades
    // to something readable instead of to "[object Object]" again.
    respondWith({ detail: [{ unexpected: true }] }, UNPROCESSABLE);

    expect((await errorFrom(getMe)).message).toBe("Unprocessable Content");
  });
});
