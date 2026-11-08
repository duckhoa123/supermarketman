import { Link } from "react-router-dom";
import EntryCard from "../../components/EntryCard";
import { useAgentHome } from "../../components/WorkProvider";
import { isEarningsGoal, type EarningsGoal, type ProteinGoal, type WorkEntry } from "../../lib/agentHome";

function Section({ title, entries, locationId, onChanged, empty }: {
  title: string;
  entries: WorkEntry[];
  locationId?: string;
  onChanged: () => void;
  empty?: string;
}) {
  if (!entries.length && !empty) return null;
  return (
    <section className="work-section" aria-label={title}>
      <h2 className="home-h">{title}{entries.length > 0 && <span className="count">{entries.length}</span>}</h2>
      {entries.length === 0 ? <p className="small muted">{empty}</p> : entries.map((e) => (
        <EntryCard key={e.id} entry={e} locationId={locationId} onChanged={onChanged} />
      ))}
    </section>
  );
}

function ProteinCard({ goal }: { goal: ProteinGoal }) {
  const max = Math.max(goal.target * 1.2, ...goal.days.map((d) => d.value), 1);
  return (
    <section className="panel goal-card" aria-label="This week's goal">
      <div className="spread">
        <span className="tag tag-done">Goal · this week</span>
        <span className="small muted">{goal.met_days} of 7 days</span>
      </div>
      <h3>{goal.title}</h3>
      <div className="goal-bars" role="img"
        aria-label={goal.days.map((d) => `${d.day}: ${d.value} ${goal.unit}`).join(", ")}>
        {goal.days.map((d, i) => (
          <div key={i} className={`goal-bar ${d.met ? "met" : ""} ${d.today ? "today" : ""} ${d.future ? "future" : ""}`}>
            <span style={{ height: `${Math.max(4, (d.value / max) * 100)}%` }} />
            <small>{d.day}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function EarningsCard({ goal }: { goal: EarningsGoal }) {
  return (
    <section className="panel goal-card" aria-label="This week">
      <div className="spread">
        <h3>{goal.title}</h3>
        <strong>{goal.earned}{goal.target && <span className="muted"> of {goal.target}</span>}</strong>
      </div>
      {goal.pct !== null && (
        <div className="meter" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={goal.pct}
          aria-label="Weekly goal"><span style={{ width: `${goal.pct}%` }} /></div>
      )}
      <div className="goal-figures">
        <div><small className="muted">Hours</small><strong>{goal.hours}</strong></div>
        <div><small className="muted">Deliveries</small><strong>{goal.deliveries}</strong></div>
        <div><small className="muted">On time</small><strong>{goal.on_time_pct === null ? "–" : `${goal.on_time_pct}%`}</strong></div>
      </div>
      {!goal.target && <Link className="small" to="/earnings">Set a weekly goal</Link>}
    </section>
  );
}

/** Everything the agent is waiting on, doing and will do, so nothing happens out of sight. */
export default function WorkingOn() {
  const { agent, profile, locationId, work, error, reloadWork } = useAgentHome();
  const name = profile?.display_name ?? "Your agent";
  const reload = () => void reloadWork();
  const goal = work?.goal;
  const quiet = work && !work.needs_you.length && !work.in_progress.length && !work.scheduled.length;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{agent === "driver" ? "My shift" : "Working on"}</h1>
          <p>{agent === "driver"
            ? "Your shift, current jobs and this week's earnings, kept up to date as you go."
            : `${name} keeps going while the app is closed. Anything that sends, posts, orders, books or charges waits here for your OK.`}</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!work && !error && <p className="muted">Loading…</p>}
      {work && (
        <div className="work">
          {goal && (isEarningsGoal(goal) ? <EarningsCard goal={goal} /> : <ProteinCard goal={goal} />)}
          <Section title="Needs your OK" entries={work.needs_you} locationId={locationId} onChanged={reload} />
          <Section title="Under way" entries={work.in_progress} locationId={locationId} onChanged={reload} />
          <Section title="Coming up" entries={work.scheduled} locationId={locationId} onChanged={reload} />
          {quiet && <p className="empty panel">{agent === "driver"
            ? "No jobs right now. Start a shift on Jobs and your pickups and drop-offs show up here."
            : `Nothing under way right now. Ask ${name} for something in Chat, and longer jobs show up here while they run.`}</p>}
          {work.done.length > 0 && (
            <section className="work-section" aria-label="Recently done">
              <div className="spread">
                <h2 className="home-h">Recently done</h2>
                <Link to="/activity" className="small">All activity</Link>
              </div>
              {work.done.slice(0, 3).map((e) => <EntryCard key={e.id} entry={e} compact />)}
            </section>
          )}
        </div>
      )}
    </>
  );
}
