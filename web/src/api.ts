import type {
  ApiToken,
  ApiTokenCreated,
  ApiTokenScope,
  Concept2WodEntry,
  Invite,
  InviteCheck,
  InviteCreated,
  GymConnectionHealth,
  GymFeed,
  GymProviderInfo,
  HybridWodEntry,
  Me,
  Passkey,
  PasskeyChallenge,
  Role,
  SessionView,
  UserConfigUpdate,
  WodEntry,
  Workout,
  WorkoutMode,
  WorkoutPage,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Flatten an error body's `detail` into something renderable.
 *
 * Our own handlers raise `HTTPException(detail="a sentence")`, but FastAPI's
 * request validation returns a *list* of `{loc, msg, ...}` objects. Passing
 * that straight to `ApiError` put "[object Object]" on screen wherever a 422
 * surfaced, which told the user nothing and hid the real complaint.
 */
function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object" && typeof (item as { msg?: unknown }).msg === "string"
          ? (item as { msg: string }).msg
          : null,
      )
      .filter((msg): msg is string => msg !== null);
    if (messages.length > 0) return messages.join(" ");
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, errorMessage(body.detail, response.statusText));
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export { ApiError };

export function login(email: string, password: string): Promise<void> {
  return request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function logout(): Promise<void> {
  return request("/api/auth/logout", { method: "POST" });
}

/** End every session for this account, including this one. */
export function logoutEverywhere(): Promise<void> {
  return request("/api/auth/logout-all", { method: "POST" });
}

/** Whether an invite link is usable, and which address it's bound to. */
export function checkInvite(token: string): Promise<InviteCheck> {
  return request(`/api/auth/invite?token=${encodeURIComponent(token)}`);
}

export function register(input: {
  inviteToken: string;
  email: string;
  password: string;
  displayName: string;
}): Promise<void> {
  return request("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({
      invite_token: input.inviteToken,
      email: input.email,
      password: input.password,
      display_name: input.displayName,
    }),
  });
}

export function verifyEmail(token: string): Promise<Me> {
  return request("/api/auth/verify", { method: "POST", body: JSON.stringify({ token }) });
}

export function resendVerification(): Promise<void> {
  return request("/api/auth/resend-verification", { method: "POST" });
}

/**
 * Always resolves, whether or not the address has an account — the server
 * deliberately answers the same way either way, so the UI must not imply it
 * learned anything.
 */
export function forgotPassword(email: string): Promise<void> {
  return request("/api/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, password: string): Promise<void> {
  return request("/api/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, password }),
  });
}

export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return request("/api/me/password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export function listSessions(): Promise<SessionView[]> {
  return request("/api/me/sessions");
}

/** Sign out everywhere else, keeping this session. */
export function endOtherSessions(): Promise<void> {
  return request("/api/me/sessions", { method: "DELETE" });
}

// --- Passkeys ---------------------------------------------------------------

export function passkeyRegisterChallenge(): Promise<PasskeyChallenge> {
  return request("/api/me/passkeys/challenge", { method: "POST" });
}

export function registerPasskey(input: {
  challengeHandle: string;
  credential: unknown;
  nickname: string;
}): Promise<Passkey> {
  return request("/api/me/passkeys", {
    method: "POST",
    body: JSON.stringify({
      challenge_handle: input.challengeHandle,
      credential: input.credential,
      nickname: input.nickname,
    }),
  });
}

export function listPasskeys(): Promise<Passkey[]> {
  return request("/api/me/passkeys");
}

export function deletePasskey(id: string): Promise<void> {
  return request(`/api/me/passkeys/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function passkeyLoginChallenge(): Promise<PasskeyChallenge> {
  return request("/api/auth/passkey/challenge", { method: "POST" });
}

export function passkeyLogin(challengeHandle: string, credential: unknown): Promise<void> {
  return request("/api/auth/passkey/login", {
    method: "POST",
    body: JSON.stringify({ challenge_handle: challengeHandle, credential }),
  });
}

export function listApiTokens(): Promise<ApiToken[]> {
  return request("/api/me/tokens");
}

/**
 * Mints a token and returns its value — the only time it's ever visible, since
 * the server stores only a hash. The current password is required because this
 * credential outlives the session that created it.
 */
export function createApiToken(input: {
  name: string;
  scopes: ApiTokenScope[];
  currentPassword: string;
}): Promise<ApiTokenCreated> {
  return request("/api/me/tokens", {
    method: "POST",
    body: JSON.stringify({
      name: input.name,
      scopes: input.scopes,
      current_password: input.currentPassword,
    }),
  });
}

export function revokeApiToken(id: string): Promise<void> {
  return request(`/api/me/tokens/${id}`, { method: "DELETE" });
}

// --- Admin -----------------------------------------------------------------

export function listInvites(): Promise<Invite[]> {
  return request("/api/admin/invites");
}

export function createInvite(email: string | null, role: Role): Promise<InviteCreated> {
  return request("/api/admin/invites", {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

export function revokeInvite(id: string): Promise<void> {
  return request(`/api/admin/invites/${id}`, { method: "DELETE" });
}

/** The signed-in user and their config. Credentials come back masked, never in full. */
export function getMe(): Promise<Me> {
  return request("/api/me");
}

/** Partial update: omitted fields keep their stored value. Returns the new state. */
export function updateConfig(update: UserConfigUpdate): Promise<Me> {
  return request("/api/me/config", { method: "PUT", body: JSON.stringify(update) });
}

export function parseWorkout(text: string, nameHint?: string): Promise<Workout> {
  return request("/api/workouts/parse", {
    method: "POST",
    body: JSON.stringify({ text, name_hint: nameHint ?? null }),
  });
}

/** Get-or-create: returns a saved workout matching the text, else parses and saves it. */
export function loadWorkoutFromText(text: string, nameHint?: string): Promise<Workout> {
  return request("/api/workouts/from-text", {
    method: "POST",
    body: JSON.stringify({ text, name_hint: nameHint ?? null }),
  });
}

export function listWods(days?: number): Promise<WodEntry[]> {
  return request(`/api/wods${days ? `?days=${days}` : ""}`);
}

/** Recent workouts from the user's own gym, if they've connected one. */
export function listGymWods(days?: number): Promise<GymFeed> {
  return request(`/api/gym/wods${days ? `?days=${days}` : ""}`);
}

/**
 * Every gym platform this server can connect to, and how to label its fields.
 *
 * Settings renders from this rather than from a hardcoded form per platform,
 * so a new integration reaches the UI without a frontend change.
 */
export function listGymProviders(): Promise<GymProviderInfo[]> {
  return request("/api/gym/providers");
}

/** Whether each stored connection has ever successfully fetched. */
export function getGymHealth(): Promise<GymConnectionHealth[]> {
  return request("/api/gym/health");
}

/** Recent daily erg workouts from Concept2 (RowErg, SkiErg and BikeErg). */
export function listConcept2Wods(days?: number): Promise<Concept2WodEntry[]> {
  return request(`/api/concept2/wods${days ? `?days=${days}` : ""}`);
}

/** The Hybrid Calisthenics weekly rotation, projected onto recent dates. */
export function listHybridWods(days?: number): Promise<HybridWodEntry[]> {
  return request(`/api/hybrid/wods${days ? `?days=${days}` : ""}`);
}

/** Add the classic benchmark workouts (Murph, Cindy, Fran, …) to the library. */
export function seedBenchmarks(): Promise<{ added: number; skipped: number }> {
  return request("/api/workouts/seed", { method: "POST" });
}

/**
 * One page of the saved library, newest first.
 *
 * `q` is matched server-side across name, description, category, mode and the
 * movements inside each workout, so a search spans the whole library rather
 * than the page currently on screen.
 */
export function listWorkouts(params?: {
  limit?: number;
  offset?: number;
  q?: string;
  mode?: WorkoutMode | "";
  category?: string;
}): Promise<WorkoutPage> {
  const search = new URLSearchParams();
  if (params?.limit !== undefined) search.set("limit", String(params.limit));
  if (params?.offset) search.set("offset", String(params.offset));
  if (params?.q) search.set("q", params.q);
  if (params?.mode) search.set("mode", params.mode);
  if (params?.category) search.set("category", params.category);
  const query = search.toString();
  return request(`/api/workouts${query ? `?${query}` : ""}`);
}

/** Categories present in the library, for the filter dropdown. */
export function listWorkoutCategories(): Promise<string[]> {
  return request("/api/workouts/categories");
}

export function getWorkout(id: string): Promise<Workout> {
  return request(`/api/workouts/${id}`);
}

export function createWorkout(workout: Workout): Promise<Workout> {
  // Drop the server-managed fields when they're empty (a freshly built workout
  // has no id/timestamps yet) so the API's model defaults generate them rather
  // than rejecting the blank strings.
  const { id, created_at, updated_at, ...rest } = workout;
  const payload: Record<string, unknown> = { ...rest };
  if (id) payload.id = id;
  if (created_at) payload.created_at = created_at;
  if (updated_at) payload.updated_at = updated_at;
  return request("/api/workouts", { method: "POST", body: JSON.stringify({ workout: payload }) });
}

export function updateWorkout(id: string, workout: Workout): Promise<Workout> {
  return request(`/api/workouts/${id}`, {
    method: "PUT",
    body: JSON.stringify({ workout }),
  });
}

/**
 * Tell the server the clock actually started on this workout.
 *
 * Fire-and-forget on purpose: it's telemetry, and a failed metric must never
 * interrupt a workout that's already underway. Errors are swallowed at the
 * call site rather than here so `request`'s behaviour stays uniform.
 */
export function markWorkoutStarted(id: string): Promise<void> {
  return request(`/api/workouts/${id}/started`, { method: "POST" });
}

/** Tell the server the clock stopped, and how long it ran. Same fire-and-forget contract as `markWorkoutStarted`. */
export function markWorkoutCompleted(id: string, elapsedSeconds: number): Promise<void> {
  return request(`/api/workouts/${id}/completed`, {
    method: "POST",
    body: JSON.stringify({ elapsed_seconds: elapsedSeconds }),
  });
}

export function deleteWorkout(id: string): Promise<void> {
  return request(`/api/workouts/${id}`, { method: "DELETE" });
}
