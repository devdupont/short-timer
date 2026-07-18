import type { Workout } from "./types";

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

export function parseWorkout(text: string, nameHint?: string): Promise<Workout> {
  return request("/api/workouts/parse", {
    method: "POST",
    body: JSON.stringify({ text, name_hint: nameHint ?? null }),
  });
}

export function listWorkouts(): Promise<Workout[]> {
  return request("/api/workouts");
}

export function getWorkout(id: string): Promise<Workout> {
  return request(`/api/workouts/${id}`);
}

export function createWorkout(workout: Workout): Promise<Workout> {
  return request("/api/workouts", { method: "POST", body: JSON.stringify({ workout }) });
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
