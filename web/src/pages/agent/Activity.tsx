import { useMemo, useState } from "react";
import EntryCard from "../../components/EntryCard";
import { useAgentHome } from "../../components/WorkProvider";
import { dayGroup, type WorkEntry } from "../../lib/agentHome";

const FILTERS = [
  { value: "all", label: "All" },
  { value: "waiting", label: "Waiting on you" },
  { value: "planned", label: "Planned" },
  { value: "done", label: "Done" },
] as const;
type Filter = (typeof FILTERS)[number]["value"];

/** One record of what the agent did, who approved it, and what it plans to do next. */
export default function Activity() {
  const { profile, work, error } = useAgentHome();
  const [filter, setFilter] = useState<Filter>("all");
  const name = profile?.display_name ?? "your agent";

  const groups = useMemo(() => {
    if (!work) return [];
    const pick: WorkEntry[] =
      filter === "waiting" ? work.needs_you
      : filter === "planned" ? [...work.scheduled, ...work.in_progress]
      : filter === "done" ? work.done
      : [...work.needs_you, ...work.in_progress, ...work.scheduled, ...work.done];
    const out: { name: string; entries: WorkEntry[] }[] = [];
    for (const e of pick) {
      const g = dayGroup(e);
      const last = out[out.length - 1];
      if (last && last.name === g) last.entries.push(e);
      else out.push({ name: g, entries: [e] });
    }
    return out;
  }, [work, filter]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Activity</h1>
          <p>What {name} did, who approved it, and what's planned next.</p>
        </div>
      </div>
      <div className="chips-row" role="group" aria-label="Show">
        {FILTERS.map((f) => (
          <button key={f.value} className={`chip ${filter === f.value ? "on" : ""}`} aria-pressed={filter === f.value}
            onClick={() => setFilter(f.value)}>{f.label}</button>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      {!work && !error && <p className="muted">Loading…</p>}
      {work && groups.length === 0 && <p className="empty panel">Nothing here yet.</p>}
      {groups.map((g) => (
        <section key={g.name} className="activity-group" aria-label={g.name}>
          <h2 className="home-h">{g.name}</h2>
          <div className="panel activity-list">
            {g.entries.map((e) => <EntryCard key={e.id} entry={e} compact />)}
          </div>
        </section>
      ))}
      {work && <p className="small muted activity-foot">Shows recent days. Anything sent, posted, ordered or booked
        shows who approved it.</p>}
    </>
  );
}
