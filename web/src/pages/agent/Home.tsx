import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Avatar from "../../components/Avatar";
import { stripSpeaker } from "../../components/AgentDocks";
import ChatPanel, { type ChatConfig } from "../../components/ChatPanel";
import EntryCard, { Steps } from "../../components/EntryCard";
import { useAgentHome } from "../../components/WorkProvider";
import { useMe } from "../../context";
import { api, type AlertRow } from "../../lib/api";

function hello(name: string | null): string {
  const hour = new Date().getHours();
  const first = (name ?? "").trim().split(/\s+/)[0];
  return `${hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"}${first ? `, ${first}` : ""}`;
}

/** The agent's home: what needs you, what's under way, what it noticed, then the conversation itself. Talking to
 * the agent is the main way in; the classic screens stay one tap away under Screens. */
export default function AgentHome({ chat }: { chat: ChatConfig }) {
  const { me } = useMe();
  const { agent, profile, locationId, paths, work, reloadWork } = useAgentHome();
  const [noticed, setNoticed] = useState<AlertRow[]>([]);
  const [prompt, setPrompt] = useState<{ text: string; nonce: number } | null>(null);
  const name = profile?.display_name ?? "Your agent";
  const restaurant = agent === "restaurant_manager";
  const alertsPath = restaurant ? `/locations/${locationId}/alerts?unread_only=true&limit=3` : "/me/alerts?unread_only=true";

  const loadNoticed = useCallback(() => {
    api<{ alerts: AlertRow[] }>(alertsPath)
      .then((r) => setNoticed(r.alerts.filter((a) => !a.read).slice(0, 3)))
      .catch(() => setNoticed([]));
  }, [alertsPath]);
  useEffect(loadNoticed, [loadNoticed]);

  async function gotIt(id: string) {
    setNoticed((n) => n.filter((a) => a.id !== id));
    if (restaurant) await api(`/locations/${locationId}/alerts/${id}/read`, { method: "POST", body: {} }).catch(() => undefined);
    else await api("/me/alerts/read-all", { method: "POST", body: {} }).catch(() => undefined);
  }

  const needs = work?.needs_you ?? [];
  const running = work?.in_progress ?? [];
  const summary = !work
    ? "Checking what's new…"
    : [
        needs.length ? `${needs.length} ${needs.length === 1 ? "thing needs" : "things need"} your OK.` : "Nothing needs your OK right now.",
        running.length ? `${running.length} under way.` : "",
      ].join(" ").trim();

  const header = (
    <div className="home-head">
      <div className="home-hero">
        {profile && <Avatar profile={profile} size={56} />}
        <div>
          <h1>{hello(me.name)}</h1>
          <p className="muted">{summary}</p>
        </div>
      </div>

      {needs.length > 0 && (
        <section className="home-section" aria-labelledby="needs-h">
          <div className="spread">
            <h2 id="needs-h" className="home-h">Needs your OK</h2>
            {needs.length > 3 && <Link to="/working" className="small">See all {needs.length}</Link>}
          </div>
          {needs.slice(0, 3).map((e) => (
            <EntryCard key={e.id} entry={e} locationId={locationId} onChanged={reloadWork} />
          ))}
        </section>
      )}

      {running.length > 0 && (
        <Link to="/working" className="home-running">
          <span className="home-running-label">Under way</span>
          <strong>{running[0].title}</strong>
          {running[0].steps.length > 0 ? <Steps steps={running[0].steps} /> : <span className="small">{running[0].detail}</span>}
          {running.length > 1 && <span className="small">and {running.length - 1} more in Working on</span>}
        </Link>
      )}

      {noticed.length > 0 && (
        <section className="home-section" aria-labelledby="noticed-h">
          <h2 id="noticed-h" className="home-h">{name} noticed</h2>
          {noticed.map((a) => (
            <article key={a.id} className="notice">
              <strong>{stripSpeaker(a.title)}</strong>
              <p className="small body">{a.body}</p>
              <div className="row">
                <button className="btn primary small" onClick={() => {
                  void gotIt(a.id);
                  setPrompt({ text: `About "${stripSpeaker(a.title)}": what does this mean for me, and what are my options?`, nonce: Date.now() });
                }}>
                  Ask {name}
                </button>
                <button className="btn small" onClick={() => void gotIt(a.id)}>Got it</button>
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  );

  return (
    <div className="agent-home">
      <ChatPanel {...chat} variant="page" open onClose={() => undefined} prompt={prompt}
        resumeFrom={paths.conversations} header={header} onChanged={() => { void reloadWork(); loadNoticed(); }} />
    </div>
  );
}
