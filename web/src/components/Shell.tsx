import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import Avatar from "./Avatar";
import { signOut } from "../lib/auth";
import type { AgentProfile } from "../lib/api";

export type NavItem = { to: string; label: string; end?: boolean; badge?: number };

/** On phones the menu bar sits at the top: it slides away while you scroll down to read,
 *  and comes back as soon as you scroll up (or reach the top, or change page). */
function useHideOnScroll(): boolean {
  const [hidden, setHidden] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setHidden(false), [pathname]);
  useEffect(() => {
    let last = window.scrollY;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        const y = window.scrollY;
        const dy = y - last;
        if (y < 64) setHidden(false);
        else if (dy > 8) setHidden(true);
        else if (dy < -8) setHidden(false);
        if (Math.abs(dy) > 8 || y < 64) last = y;
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  return hidden;
}

/** Side rail on wide screens; on phones, a menu bar at the top that hides while scrolling down.
 *  The agent comes first (Chat, Working on, Activity, You); the classic screens sit under "Screens". */
export default function Shell({ primary, screens, profile, footer, onSwitchAgent, children }: {
  primary: NavItem[];
  screens: NavItem[];
  profile?: AgentProfile;
  footer?: ReactNode;
  onSwitchAgent: () => void;
  children: ReactNode;
}) {
  const badge = (n: NavItem) => (n.badge ? <span className="badge">{n.badge}</span> : null);
  const hidden = useHideOnScroll();
  const screensBadge = screens.reduce((sum, n) => sum + (n.badge ?? 0), 0);
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <img src="/icon.svg" alt="" />
          Pass
        </div>
        {profile && (
          <button className="agent-switch" onClick={onSwitchAgent} title="Switch agent or customise">
            <Avatar profile={profile} size={30} />
            <span>
              <strong>{profile.display_name}</strong>
              <span className="small muted">{profile.label}</span>
            </span>
          </button>
        )}
        <nav className="nav" aria-label={profile?.display_name ?? "Agent"}>
          {primary.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
              {badge(n)}
            </NavLink>
          ))}
        </nav>
        <p className="rail-h">Screens</p>
        <nav className="nav nav-screens" aria-label="Screens">
          {screens.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
              {badge(n)}
            </NavLink>
          ))}
        </nav>
        <div className="rail-foot">
          {footer}
          <button className="btn small" onClick={() => void signOut().then(() => window.location.assign("/"))}>
            Sign out
          </button>
        </div>
      </aside>
      <nav className={`tabs ${hidden ? "tabs-hidden" : ""}`} aria-label="Main">
        {primary.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}>
            {n.label}
            {badge(n)}
          </NavLink>
        ))}
        <NavLink to="/more">
          Screens
          {screensBadge > 0 && <span className="badge">{screensBadge}</span>}
        </NavLink>
      </nav>
      <main className="main">{children}</main>
    </div>
  );
}
