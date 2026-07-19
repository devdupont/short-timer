export type WorkoutMode = "for_time" | "amrap" | "emom" | "tabata" | "interval" | "custom";

export interface Movement {
  name?: string | null;
  reps?: number | null;
  distance?: string | null;
  calories?: number | null;
  load?: string | null;
  notes?: string | null;
}

export interface WorkoutSegment {
  label?: string | null;
  rounds?: number | null;
  rep_scheme?: number[] | null;
  movements: Movement[];
}

export interface Workout {
  id: string;
  name: string;
  description?: string | null;
  category?: string | null;
  source_text?: string | null;
  source_hash?: string | null;
  mode: WorkoutMode;
  time_cap_seconds?: number | null;
  rounds?: number | null;
  work_seconds?: number | null;
  rest_seconds?: number | null;
  rep_scheme?: number[] | null;
  segments: WorkoutSegment[];
  created_at: string;
  updated_at: string;
}

export interface WodEntry {
  date: string;
  title: string;
  text: string;
  url: string;
  saved_workout_id?: string | null;
}

export const MODE_LABELS: Record<WorkoutMode, string> = {
  for_time: "For Time",
  amrap: "AMRAP",
  emom: "EMOM",
  tabata: "Tabata",
  interval: "Interval",
  custom: "Custom",
};

export const MODE_HINTS: Record<WorkoutMode, string> = {
  for_time: "Finish the work as fast as possible, optionally against a time cap.",
  amrap: "As many rounds or reps as possible within a fixed time window.",
  emom: "Every interval on the minute, start the next movement.",
  tabata: "Repeated work/rest intervals — classically 20s work / 10s rest × 8.",
  interval: "Custom work/rest intervals repeated for a set number of rounds.",
  custom: "No fixed clock — a note, rest day, or free-form session.",
};
