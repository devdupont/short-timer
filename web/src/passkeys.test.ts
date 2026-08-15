import { afterEach, describe, expect, it, vi } from "vitest";
import { createCredential, getCredential, passkeysSupported } from "./passkeys";

/**
 * The base64url ↔ ArrayBuffer encoding either side of a WebAuthn ceremony.
 *
 * This is the module's entire job, and its failure mode is the reason it's
 * worth pinning: one mis-encoded field produces a signature the server rejects
 * with nothing to say about which field was wrong. The alphabet matters too —
 * base64url swaps `+/` for `-_` and drops padding, so a round trip through
 * plain base64 corrupts roughly one credential in sixteen and looks flaky
 * rather than broken.
 */

/** Bytes that encode to every character base64url treats differently. */
const AWKWARD = [251, 255, 191];
const AWKWARD_B64URL = "-_-_";

function buf(bytes: number[]): ArrayBuffer {
  return new Uint8Array(bytes).buffer;
}

function bytesOf(value: ArrayBuffer): number[] {
  return Array.from(new Uint8Array(value));
}

function credential(over: Record<string, unknown> = {}) {
  return {
    id: "cred-id",
    rawId: buf(AWKWARD),
    type: "public-key",
    response: {
      clientDataJSON: buf([1, 2, 3]),
      attestationObject: buf([4, 5, 6]),
      authenticatorData: buf([7, 8, 9]),
      signature: buf([10, 11, 12]),
      userHandle: null,
      getTransports: () => ["internal", "hybrid"],
    },
    ...over,
  };
}

/** Install a fake authenticator and return what the browser API was handed. */
function stubCredentials(result: unknown): { calls: any[] } {
  const calls: any[] = [];
  vi.stubGlobal("navigator", {
    credentials: {
      create: vi.fn(async (opts: unknown) => {
        calls.push(opts);
        return result;
      }),
      get: vi.fn(async (opts: unknown) => {
        calls.push(opts);
        return result;
      }),
    },
  });
  return { calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("passkeysSupported", () => {
  it("is true when the browser has the WebAuthn entry point", () => {
    vi.stubGlobal("window", { PublicKeyCredential: class {} });
    expect(passkeysSupported()).toBe(true);
  });

  it("is false when it doesn't", () => {
    // Older browsers and some embedded webviews; the UI hides the button
    // rather than offering one that throws.
    vi.stubGlobal("window", {});
    expect(passkeysSupported()).toBe(false);
  });
});

describe("createCredential", () => {
  it("decodes the challenge and user id into buffers for the browser", async () => {
    // The server sends base64url because JSON can't carry an ArrayBuffer; the
    // WebAuthn API accepts nothing else.
    const { calls } = stubCredentials(credential());

    await createCredential({
      challenge: AWKWARD_B64URL,
      user: { id: "_w", name: "athlete" },
    });

    const { publicKey } = calls[0];
    expect(bytesOf(publicKey.challenge)).toEqual(AWKWARD);
    expect(bytesOf(publicKey.user.id)).toEqual([255]);
    // Everything else is passed through untouched.
    expect(publicKey.user.name).toBe("athlete");
  });

  it("decodes each excluded credential's id", async () => {
    // These are the credentials already registered; a mis-decoded id here
    // silently allows a duplicate passkey for the same account.
    const { calls } = stubCredentials(credential());

    await createCredential({
      challenge: "_w",
      user: { id: "_w" },
      excludeCredentials: [{ id: AWKWARD_B64URL, type: "public-key" }],
    });

    expect(bytesOf(calls[0].publicKey.excludeCredentials[0].id)).toEqual(AWKWARD);
    expect(calls[0].publicKey.excludeCredentials[0].type).toBe("public-key");
  });

  it("copes with no excluded credentials at all", async () => {
    const { calls } = stubCredentials(credential());

    await createCredential({ challenge: "_w", user: { id: "_w" } });

    expect(calls[0].publicKey.excludeCredentials).toEqual([]);
  });

  it("encodes the attestation back to base64url", async () => {
    stubCredentials(credential());

    const result: any = await createCredential({ challenge: "_w", user: { id: "_w" } });

    expect(result.id).toBe("cred-id");
    expect(result.type).toBe("public-key");
    expect(result.rawId).toBe(AWKWARD_B64URL);
    expect(result.response.clientDataJSON).toBe("AQID");
    expect(result.response.attestationObject).toBe("BAUG");
  });

  it("passes on the transports the authenticator reported", async () => {
    // Tells the browser next time whether to look at USB, NFC, or the
    // internal sensor.
    stubCredentials(credential());

    const result: any = await createCredential({ challenge: "_w", user: { id: "_w" } });

    expect(result.transports).toEqual(["internal", "hybrid"]);
  });

  it("reports no transports when the browser doesn't implement them", async () => {
    const cred = credential();
    delete (cred.response as any).getTransports;
    stubCredentials(cred);

    const result: any = await createCredential({ challenge: "_w", user: { id: "_w" } });

    expect(result.transports).toEqual([]);
  });

  it("fails loudly when no passkey was created", async () => {
    // A null result means the user dismissed the prompt; sending an empty
    // registration would fail server-side with nothing useful to say.
    stubCredentials(null);

    await expect(createCredential({ challenge: "_w", user: { id: "_w" } })).rejects.toThrow(
      "No passkey was created.",
    );
  });
});

describe("getCredential", () => {
  it("decodes the challenge and every allowed credential id", async () => {
    const { calls } = stubCredentials(credential());

    await getCredential({
      challenge: AWKWARD_B64URL,
      allowCredentials: [{ id: "_w", type: "public-key" }],
    });

    expect(bytesOf(calls[0].publicKey.challenge)).toEqual(AWKWARD);
    expect(bytesOf(calls[0].publicKey.allowCredentials[0].id)).toEqual([255]);
  });

  it("copes with no allowed credentials, which is how discoverable login works", async () => {
    // An empty list is what lets the authenticator offer whichever account it
    // holds, rather than the page naming one first.
    const { calls } = stubCredentials(credential());

    await getCredential({ challenge: "_w" });

    expect(calls[0].publicKey.allowCredentials).toEqual([]);
  });

  it("encodes the assertion back to base64url", async () => {
    stubCredentials(credential());

    const result: any = await getCredential({ challenge: "_w" });

    expect(result.rawId).toBe(AWKWARD_B64URL);
    expect(result.response.clientDataJSON).toBe("AQID");
    expect(result.response.authenticatorData).toBe("BwgJ");
    expect(result.response.signature).toBe("CgsM");
  });

  it("encodes a user handle when the authenticator sent one", async () => {
    const cred = credential();
    (cred.response as any).userHandle = buf(AWKWARD);
    stubCredentials(cred);

    const result: any = await getCredential({ challenge: "_w" });

    expect(result.response.userHandle).toBe(AWKWARD_B64URL);
  });

  it("sends null rather than an empty string when there is no user handle", async () => {
    // Non-discoverable credentials omit it; the server identifies the account
    // by credential id regardless, so null has to survive as null.
    stubCredentials(credential());

    const result: any = await getCredential({ challenge: "_w" });

    expect(result.response.userHandle).toBeNull();
  });

  it("fails loudly when no passkey was used", async () => {
    stubCredentials(null);

    await expect(getCredential({ challenge: "_w" })).rejects.toThrow("No passkey was used.");
  });
});
