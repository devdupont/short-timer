import { useState } from "react";
import "./App.css";
import { Home } from "./components/Home";
import { PasscodeGate } from "./components/PasscodeGate";
import { Settings } from "./components/Settings";
import { TimerView } from "./components/TimerView";
import { WorkoutBuilder } from "./components/WorkoutBuilder";
import { WorkoutImport } from "./components/WorkoutImport";
import { WorkoutLibrary } from "./components/WorkoutLibrary";
import type { EditTarget } from "./components/WorkoutBuilder";
import { logout } from "./api";
import type { Workout } from "./types";

type Tab = "home" | "import" | "build" | "library" | "timer" | "settings";

function App() {
  const [unlocked, setUnlocked] = useState(false);
  const [tab, setTab] = useState<Tab>("home");
  const [activeWorkout, setActiveWorkout] = useState<Workout | null>(null);
  const [libraryRefreshKey, setLibraryRefreshKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null);

  if (!unlocked) {
    return <PasscodeGate onUnlocked={() => setUnlocked(true)} />;
  }

  function handleSaved(workout: Workout) {
    setLibraryRefreshKey((k) => k + 1);
    setActiveWorkout(workout);
    setEditTarget(null);
    setTab("timer");
  }

  /** Open a workout in the builder so it can be tweaked before timing it. */
  function handleEdit(target: EditTarget) {
    setEditTarget(target);
    setTab("build");
  }

  // Load into the timer without touching the library (used for unsaved previews).
  function handleLoadOnly(workout: Workout) {
    setActiveWorkout(workout);
    setTab("timer");
  }

  // Selecting anything from the nav also closes the mobile menu.
  function selectTab(next: Tab) {
    setTab(next);
    setMenuOpen(false);
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "home", label: "Home" },
    { id: "import", label: "Paste" },
    { id: "build", label: "Build" },
    { id: "library", label: "Library" },
    ...(activeWorkout ? [{ id: "timer" as Tab, label: "Timer" }] : []),
    { id: "settings", label: "Settings" },
  ];

  return (
    <div className="app">
      <header className="app-header">
        <h1>shortimer</h1>

        <button
          className={`hamburger ${menuOpen ? "open" : ""}`}
          aria-label="Menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((o) => !o)}
        >
          <span />
          <span />
          <span />
        </button>

        {menuOpen && <div className="nav-backdrop" onClick={() => setMenuOpen(false)} />}

        <div className={`nav-group ${menuOpen ? "open" : ""}`}>
          <nav className="tabs">
            {tabs.map(({ id, label }) => (
              <button
                key={id}
                className={tab === id ? "active" : ""}
                onClick={() => selectTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <button
            className="logout-button"
            onClick={() => {
              logout();
              setMenuOpen(false);
              setUnlocked(false);
            }}
          >
            Lock
          </button>
        </div>
      </header>

      <main>
        {tab === "home" && (
          <Home
            onLoad={handleSaved}
            onEdit={handleEdit}
            onOpenSettings={() => setTab("settings")}
          />
        )}
        {tab === "import" && <WorkoutImport onSaved={handleSaved} onLoad={handleLoadOnly} />}
        {tab === "build" && (
          // Remount when the edit target changes so the form re-seeds from it.
          <WorkoutBuilder
            key={editTarget?.workout.id ?? "new"}
            onSaved={handleSaved}
            editTarget={editTarget}
            onCancelEdit={() => setEditTarget(null)}
          />
        )}
        {tab === "library" && (
          <WorkoutLibrary
            refreshKey={libraryRefreshKey}
            onSelect={(workout) => {
              setActiveWorkout(workout);
              setTab("timer");
            }}
            onEdit={(workout) => handleEdit({ workout, saved: true })}
          />
        )}
        {tab === "timer" && activeWorkout && <TimerView workout={activeWorkout} />}
        {tab === "settings" && <Settings />}
      </main>
    </div>
  );
}

export default App;
