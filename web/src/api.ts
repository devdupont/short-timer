import type {
  Concept2WodEntry,
  GymFeed,
  HybridWodEntry,
  Me,
  UserConfigUpdate,
  WodEntry,
  Workout,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? response.statusText);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export { ApiError };

export function login(passcode: string): Promise<void> {
  return request("/api/auth/login", { method: "POST", body: JSON.stringify({ passcode }) });
}

export function logout(): Promise<void> {
  return request("/api/auth/logout", { method: "POST" });
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
}): Promise<WorkoutPage> {
  const search = new URLSearchParams();
  if (params?.limit !== undefined) search.set("limit", String(params.limit));
  if (params?.offset) search.set("offset", String(params.offset));
  if (params?.q) search.set("q", params.q);
  const query = search.toString();
  return request(`/api/workouts${query ? `?${query}` : ""}`);
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

export function deleteWorkout(id: string): Promise<void> {
  return request(`/api/workouts/${id}`, { method: "DELETE" });
}
