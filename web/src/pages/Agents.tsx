import { useState } from "react";
import Avatar, { ICON_GLYPHS, SWATCHES } from "../components/Avatar";
import { api, type AgentKind, type AgentProfile, type Me } from "../lib/api";

const ABOUT: Record<AgentKind, string> = {
  restaurant_manager: "For restaurant owners and managers: sales, stock, staff, suppliers, bookings, reviews and forecasts.",
  customer: "For eating out and in: find food nearby, order pickup or delivery, book tables, track nutrition.",
  driver: "For our delivery team: jobs, routes, shifts and earnings.",
};
const TONES = [
  { value: "friendly", label: "Friendly" },
  { value: "concise", label: "Concise" },
  { value: "detailed", label: "Detailed" },
  { value: "playful", label: "Playful" },
];

function Editor({ profile, onSaved }: { profile: AgentProfile; onSaved: () => Promise<void> }) {
  const [draft, setDraft] = useState(profile);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api(`/agents/${profile.agent}/profile`, {
        method: "PUT",
        body: { display_name: draft.display_name, icon: draft.avatar.icon, color: draft.avatar.color, tone: draft.tone },
      });
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stack" style={{ gap: 10, marginTop: 10 }}>
      <label className="stack" style={{ gap: 4 }}>
        <span className="small">Name</span>
        <input className="input" maxLength={60} value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} />
      </label>
      <div className="row" role="group" aria-label="Icon">
        {Object.entries(ICON_GLYPHS).map(([icon, glyph]) => (
          <button key={icon} type="button" className={`icon-pick ${draft.avatar.icon === icon ? "on" : ""}`}
            onClick={() => setDraft({ ...draft, avatar: { ...draft.avatar, icon } })} aria-label={icon}>
            {glyph}
          </button>
        ))}
      </div>
      <div className="row" role="group" aria-label="Colour">
        {SWATCHES.map((c) => (
          <button key={c} type="button" className={`swatch ${draft.avatar.color === c ? "on" : ""}`} style={{ background: c }}
            onClick={() => setDraft({ ...draft, avatar: { ...draft.avatar, color: c } })} aria-label={`Colour ${c}`} />
        ))}
      </div>
      <div className="seg" role="group" aria-label="Tone">
        {TONES.map((t) => (
          <button key={t.value} type="button" className={draft.tone === t.value ? "on" : ""} onClick={() => setDraft({ ...draft, tone: t.value })}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="row">
        <Avatar profile={draft} size={36} />
        <button className="btn primary small" disabled={saving} onClick={() => void save()}>Save</button>
        {error && <span className="error small">{error}</span>}
      </div>
    </div>
  );
}

/** Pick which agent to open, and customise each one's name, look and tone. */
export default function Agents({ me, current, onPick, refreshMe }: {
  me: Me;
  current: AgentKind | null;
  onPick: (agent: AgentKind) => void;
  refreshMe: () => Promise<void>;
}) {
  const [editing, setEditing] = useState<AgentKind | null>(null);
  return (
    <div className={current ? "" : "center"}>
      <div className={current ? "" : "card-wide"}>
        <div className="page-head">
          <div>
            <h1>{current ? "Your agents" : "Who's using the app today?"}</h1>
            <p>Each agent is yours to name and style. They share one account, so an order you place reaches the restaurant and its driver.</p>
          </div>
        </div>
        <div className="grid two">
          {me.agents.map((a) => (
            <section key={a.agent} className={`panel agent-card ${current === a.agent ? "current" : ""}`}>
              <div className="row" style={{ alignItems: "center" }}>
                <Avatar profile={a} size={52} />
                <div style={{ flex: 1 }}>
                  <h2 style={{ marginBottom: 0 }}>{a.display_name}</h2>
                  <div className="small muted">{a.label}</div>
                </div>
              </div>
              <p className="small" style={{ margin: "10px 0" }}>{ABOUT[a.agent]}</p>
              <div className="row">
                <button className="btn primary small" onClick={() => onPick(a.agent)}>
                  {current === a.agent ? "Open" : a.agent === "restaurant_manager" && !a.ready ? "Set up my restaurant" : "Use this agent"}
                </button>
                <button className="btn small" onClick={() => setEditing(editing === a.agent ? null : a.agent)}>
                  {editing === a.agent ? "Close" : "Customise"}
                </button>
              </div>
              {editing === a.agent && <Editor profile={a} onSaved={async () => { await refreshMe(); setEditing(null); }} />}
            </section>
          ))}
        </div>
        {!me.is_driver && <p className="small muted" style={{ marginTop: 14 }}>Work for us as a driver? Ask your dispatcher for an invite to this email.</p>}
      </div>
    </div>
  );
}
