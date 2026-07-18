export type WorkoutMode = "for_time" | "amrap" | "emom" | "tabata" | "interval" | "custom";

export interface Movement {
  name: string;
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

export const MODE_LABELS: Record<WorkoutMode, string> = {
  for_time: "For Time",
  amrap: "AMRAP",
  emom: "EMOM",
  tabata: "Tabata",
  interval: "Interval",
  custom: "Custom",
};
