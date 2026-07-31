import type { Workout } from "../types";
import type { TimelineBar, TimelineBlock } from "../timerPlan";
import {
  buildTimeline,
  countsUp,
  formatClock,
  isIntervalMode,
  isRestSegment,
  locateBlock,
  movementLabel,
  segmentColors,
} from "../timerPlan";

/**
 * A workout as the clock will actually run it.
 *
 * Everything here is drawn from `timerPlan`, the same reading the timer engine
 * uses, so it doubles as a check on the parser: if the visualizer shows four
 * working minutes and a rest, that's what the clock will do. Colour is a
 * *secondary* cue — every block is labelled in the list beneath the bar, so
 * nothing here is carried by hue alone.
 */

/** The palette slot a block wears, as a CSS colour. */
function blockColor(colorIndex: number | null, kind: TimelineBlock["kind"]): string {
  if (kind === "rest") return "var(--rest-color)";
  return colorIndex === null ? "var(--viz-other)" : `var(--viz-${colorIndex + 1})`;
}

function Track({
  blocks,
  activeIndex,
}: {
  blocks: TimelineBlock[];
  activeIndex: number | null;
}) {
  const total = blocks.reduce((sum, b) => sum + b.seconds, 0) || 1;
  return (
    // The list below carries the same information as text, so the bar itself
    // is decoration as far as a screen reader is concerned.
    <div className="timeline-track" aria-hidden="true">
      {blocks.map((block, i) => (
        <div
          key={i}
          className={`timeline-block ${block.kind} ${i === activeIndex ? "active" : ""}`}
          // backgroundColor, not background: rest blocks carry a striped
          // background-image from CSS, and the shorthand would wipe it out.
          style={{
            flexGrow: block.seconds / total,
            backgroundColor: blockColor(block.colorIndex, block.kind),
          }}
          title={`${formatClock(block.seconds)} — ${block.label}`}
        />
      ))}
    </div>
  );
}

/** A rotation's legs, spelled out: which minute is which, and which is rest. */
function LegList({ blocks, activeIndex }: { blocks: TimelineBlock[]; activeIndex: number | null }) {
  return (
    <ol className="timeline-legs">
      {blocks.map((block, i) => (
        <li key={i} className={i === activeIndex ? "timeline-leg active" : "timeline-leg"}>
          <span
            className="timeline-swatch"
            style={{ backgroundColor: blockColor(block.colorIndex, block.kind) }}
          />
          <span className="timeline-leg-time">{formatClock(block.seconds)}</span>
          <span className={block.kind === "rest" ? "timeline-leg-label rest" : "timeline-leg-label"}>
            {block.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

function BarRow({
  bar,
  activeBlock,
}: {
  bar: TimelineBar;
  activeBlock: number | null;
}) {
  return (
    <div className="timeline-bar">
      <div className="timeline-bar-head">
        <span className="timeline-bar-label">{bar.label ?? "Block"}</span>
        <span className="timeline-bar-meta">
          {formatClock(bar.seconds)}
          {bar.repeats && bar.repeats > 1 ? ` × ${bar.repeats}` : ""}
        </span>
      </div>
      <Track blocks={bar.blocks} activeIndex={activeBlock} />
    </div>
  );
}

/** The one-line summary above the bar: how the clock is shaped, in words. */
function summaryChips(workout: Workout): string[] {
  const chips: string[] = [];

  if (isIntervalMode(workout)) {
    const timeline = buildTimeline(workout);
    const [first] = timeline.bars;
    if (timeline.bars.length === 1 && first) {
      chips.push(
        first.repeats
          ? `${first.repeats} × ${formatClock(first.seconds)} round`
          : `${formatClock(first.seconds)} round, unbounded`,
      );
    } else if (timeline.bars.length > 1) {
      chips.push(`${timeline.bars.length} blocks`);
    }
    if (timeline.totalSeconds) chips.push(`${formatClock(timeline.totalSeconds)} total`);
    // Direction is part of how the clock runs, and it's the one part the bars
    // can't show — so it's said in words, where a wrong reading is as visible
    // as a wrong leg length.
    if (countsUp(workout)) chips.push("sets count up");
  } else if (workout.time_cap_seconds) {
    chips.push(
      `${workout.mode === "amrap" ? "window" : "cap"} ${formatClock(workout.time_cap_seconds)}`,
    );
  } else if (workout.mode !== "custom") {
    chips.push("no cap");
  }

  if (workout.rounds && !isIntervalMode(workout)) chips.push(`${workout.rounds} rounds`);
  if (workout.rep_scheme?.length) chips.push(workout.rep_scheme.join("-"));
  return chips;
}

/**
 * Untimed and count-up workouts have no clock shape to draw to scale — a
 * chipper is "these movements, in this order, as fast as you can". They get
 * the same colour coding applied to the structure instead.
 */
function StructureList({ workout }: { workout: Workout }) {
  const colors = segmentColors(workout);
  return (
    <ol className="timeline-legs">
      {workout.segments.map((segment, i) => {
        const rest = isRestSegment(segment);
        return (
          <li key={i} className="timeline-leg">
            <span
              className="timeline-swatch"
              style={{ backgroundColor: blockColor(colors[i], rest ? "rest" : "work") }}
            />
            {segment.rounds ? <span className="timeline-leg-time">{segment.rounds}×</span> : null}
            <span className={rest ? "timeline-leg-label rest" : "timeline-leg-label"}>
              {segment.label && segment.label !== movementLabel(segment) && (
                <strong>{segment.label}: </strong>
              )}
              {movementLabel(segment) ?? "—"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function WorkoutTimeline({
  workout,
  elapsedSeconds = null,
}: {
  workout: Workout;
  /** Where the clock is right now, to mark the live position. Null when idle. */
  elapsedSeconds?: number | null;
}) {
  const chips = summaryChips(workout);
  const interval = isIntervalMode(workout);
  const timeline = interval ? buildTimeline(workout) : null;
  const active =
    timeline && elapsedSeconds !== null ? locateBlock(timeline, elapsedSeconds) : null;

  if (!interval && workout.segments.length === 0 && chips.length === 0) return null;

  return (
    <div className="workout-timeline">
      {chips.length > 0 && (
        <div className="timeline-summary">
          {chips.map((chip) => (
            <span className="timeline-chip" key={chip}>
              {chip}
            </span>
          ))}
        </div>
      )}

      {timeline ? (
        timeline.bars.length === 1 ? (
          <>
            <Track
              blocks={timeline.bars[0].blocks}
              activeIndex={active ? active.block : null}
            />
            <LegList
              blocks={timeline.bars[0].blocks}
              activeIndex={active ? active.block : null}
            />
          </>
        ) : (
          <div className="timeline-bars">
            {timeline.bars.map((bar, i) => (
              <BarRow key={i} bar={bar} activeBlock={active?.bar === i ? active.block : null} />
            ))}
          </div>
        )
      ) : (
        <StructureList workout={workout} />
      )}
    </div>
  );
}
