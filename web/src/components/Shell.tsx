import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import Avatar from "./Avatar";
import { signOut } from "../lib/auth";
import type { AgentProfile } from "../lib/api";

export type NavItem = { to: string; label: string; end?: boolean; badge?: number };

/** Side rail on wide screens, bottom tabs (plus "More") on phones. */
export default function Shell({ nav, tabs, profile, footer, onSwitchAgent, children }: {
  nav: NavItem[];
  tabs: string[];
  profile?: AgentProfile;
  footer?: ReactNode;
  onSwitchAgent: () => void;
  children: ReactNode;
}) {
  const badge = (n: NavItem) => (n.badge ? <span className="badge">{n.badge}</span> : null);
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
        <nav className="nav">
          {nav.map((n) => (
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
      <main className="main">{children}</main>
      <nav className="tabs" aria-label="Main">
        {nav
          .filter((n) => tabs.includes(n.to))
          .map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
              {badge(n)}
            </NavLink>
          ))}
        <NavLink to="/more">More</NavLink>
      </nav>
    </div>
  );
}
