import { Link } from "react-router-dom";
import Docket from "./Docket";
import { whenLabel, type WorkEntry, type WorkState } from "../lib/agentHome";

export const STATE_LABEL: Record<WorkState, string> = {
  needs_you: "Needs your OK",
  in_progress: "Under way",
  scheduled: "Planned",
  done: "Done",
};

/** Small stroke icons for each state (decorative: the state is also written out). */
export function StateIcon({ state, byYou = false }: { state: WorkState; byYou?: boolean }) {
  const common = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2.2,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  return (
    <span className={`state-icon state-${state} ${byYou ? "by-you" : ""}`}>
      {state === "needs_you" && <svg {...common}><path d="M12 8v5" /><path d="M12 16.5v.5" /><circle cx="12" cy="12" r="9" /></svg>}
      {state === "in_progress" && <svg {...common}><path d="M21 12a9 9 0 1 1-6.2-8.6" /></svg>}
      {state === "scheduled" && <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>}
      {state === "done" && <svg {...common}><path d="M5 12l5 5 9-10" /></svg>}
    </span>
  );
}

export function Steps({ steps }: { steps: WorkEntry["steps"] }) {
  if (!steps.length) return null;
  const done = steps.filter((s) => s.done).length;
  return (
    <div className="steps">
      <div className="steps-bar" role="progressbar" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={done}
        aria-label="Progress">
        {steps.map((s) => <span key={s.label} className={s.done ? "on" : s.current ? "now" : ""} />)}
      </div>
      <ol className="steps-list">
        {steps.map((s) => (
          <li key={s.label} className={s.done ? "on" : s.current ? "now" : ""}>{s.label}</li>
        ))}
      </ol>
    </div>
  );
}

/** One thing the agent is waiting on, doing, will do, or did. Waiting items with a prepared action show the usual
 * confirm docket, so they can be approved right here. */
export default function EntryCard({ entry, locationId, onChanged, compact = false }: {
  entry: WorkEntry;
  locationId?: string;
  onChanged?: () => void;
  /** A one-line row (Activity) instead of a card. */
  compact?: boolean;
}) {
  if (!compact && entry.state === "needs_you" && entry.proposal) {
    return <Docket proposal={entry.proposal} locationId={locationId} onDone={onChanged} />;
  }
  const when = whenLabel(entry.at);
  return (
    <article className={`entry ${compact ? "entry-row" : "entry-card"} entry-${entry.state}`}>
      <StateIcon state={entry.state} byYou={entry.by_you} />
      <div className="entry-main">
        <div className="entry-top">
          <strong className="entry-title">{entry.title}</strong>
          {when && <time className="small muted" dateTime={entry.at ?? undefined}>{when}</time>}
        </div>
        {entry.detail && <p className="small muted entry-detail">{entry.detail}</p>}
        {!compact && <Steps steps={entry.steps} />}
        <div className="entry-tags">
          {compact && <span className={`tag tag-${entry.state}`}>{STATE_LABEL[entry.state]}</span>}
          {entry.by_you && <span className="tag tag-you">Approved by you</span>}
          {entry.source && <span className="tag tag-source">{entry.source}</span>}
          {entry.link && (
            <Link className="entry-link small" to={entry.link}>
              {entry.state === "needs_you" ? "Open" : "Details"}
            </Link>
          )}
        </div>
      </div>
    </article>
  );
}
