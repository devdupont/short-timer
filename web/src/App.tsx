import { useState } from "react";
import "./App.css";
import { PasscodeGate } from "./components/PasscodeGate";
import { TimerView } from "./components/TimerView";
import { WorkoutBuilder } from "./components/WorkoutBuilder";
import { WorkoutImport } from "./components/WorkoutImport";
import { WorkoutLibrary } from "./components/WorkoutLibrary";
import { logout } from "./api";
import type { Workout } from "./types";

type Tab = "import" | "build" | "library" | "timer";

function App() {
  const [unlocked, setUnlocked] = useState(false);
  const [tab, setTab] = useState<Tab>("library");
  const [activeWorkout, setActiveWorkout] = useState<Workout | null>(null);
  const [libraryRefreshKey, setLibraryRefreshKey] = useState(0);

  if (!unlocked) {
    return <PasscodeGate onUnlocked={() => setUnlocked(true)} />;
  }

  function handleSaved(workout: Workout) {
    setLibraryRefreshKey((k) => k + 1);
    setActiveWorkout(workout);
    setTab("timer");
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>short-timer</h1>
        <nav className="tabs">
          <button className={tab === "import" ? "active" : ""} onClick={() => setTab("import")}>
            Paste
          </button>
          <button className={tab === "build" ? "active" : ""} onClick={() => setTab("build")}>
            Build
          </button>
          <button className={tab === "library" ? "active" : ""} onClick={() => setTab("library")}>
            Library
          </button>
          {activeWorkout && (
            <button className={tab === "timer" ? "active" : ""} onClick={() => setTab("timer")}>
              Timer
            </button>
          )}
        </nav>
        <button
          className="logout-button"
          onClick={() => {
            logout();
            setUnlocked(false);
          }}
        >
          Lock
        </button>
      </header>

      <main>
        {tab === "import" && <WorkoutImport onSaved={handleSaved} />}
        {tab === "build" && <WorkoutBuilder onSaved={handleSaved} />}
        {tab === "library" && (
          <WorkoutLibrary
            refreshKey={libraryRefreshKey}
            onSelect={(workout) => {
              setActiveWorkout(workout);
              setTab("timer");
            }}
          />
        )}
        {tab === "timer" && activeWorkout && <TimerView workout={activeWorkout} />}
      </main>
    </div>
  );
}

export default App;
