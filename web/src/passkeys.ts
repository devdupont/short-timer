/**
 * Browser half of the WebAuthn ceremonies.
 *
 * The whole job here is encoding. The WebAuthn API deals in ArrayBuffers, JSON
 * can't carry those, and the server speaks base64url — so options coming down
 * need decoding into buffers, and the credential going back up needs encoding
 * into strings. Getting a single field wrong produces a signature that fails
 * verification with no clue as to why, which is why this is one small module
 * rather than inline in a component.
 *
 * Browsers have `parseCreationOptionsFromJSON` / `toJSON` for exactly this now,
 * but doing it by hand is twenty lines and works everywhere, so there's no
 * feature-detection branch to get wrong.
 */

function toBuffer(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function toBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Whether this browser can do passkeys at all. */
export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && Boolean(window.PublicKeyCredential);
}

/* eslint-disable @typescript-eslint/no-explicit-any */

/** Run `navigator.credentials.create()` and encode the attestation for the API. */
export async function createCredential(options: any): Promise<unknown> {
  const publicKey: any = {
    ...options,
    challenge: toBuffer(options.challenge),
    user: { ...options.user, id: toBuffer(options.user.id) },
    excludeCredentials: (options.excludeCredentials ?? []).map((c: any) => ({
      ...c,
      id: toBuffer(c.id),
    })),
  };

  const credential = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error("No passkey was created.");
  const response = credential.response as AuthenticatorAttestationResponse;

  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      attestationObject: toBase64url(response.attestationObject),
    },
    // Tells the browser next time whether this credential lives on USB, NFC,
    // an internal sensor, and so on.
    transports: response.getTransports?.() ?? [],
  };
}

/** Run `navigator.credentials.get()` and encode the assertion for the API. */
export async function getCredential(options: any): Promise<unknown> {
  const publicKey: any = {
    ...options,
    challenge: toBuffer(options.challenge),
    allowCredentials: (options.allowCredentials ?? []).map((c: any) => ({
      ...c,
      id: toBuffer(c.id),
    })),
  };

  const credential = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error("No passkey was used.");
  const response = credential.response as AuthenticatorAssertionResponse;

  return {
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(response.clientDataJSON),
      authenticatorData: toBase64url(response.authenticatorData),
      signature: toBase64url(response.signature),
      // Present for a discoverable credential — it's how the authenticator
      // says which account signed, though the server identifies it by
      // credential id rather than trusting this.
      userHandle: response.userHandle ? toBase64url(response.userHandle) : null,
    },
  };
}
