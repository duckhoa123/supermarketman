import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import NavIcon from "./NavIcon";
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
 *  `primary` is the main menu. On phones that's the agent (Chat, Working on, Activity, You) with the classic
 *  screens under "Screens"; wider windows list the screens themselves and pass no `screens`. */
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
  // The side menu can fold down to just its icons; remembered on this device.
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("pass.railCollapsed") === "1"; } catch { return false; }
  });
  function toggle() {
    setCollapsed((c) => {
      try { localStorage.setItem("pass.railCollapsed", c ? "0" : "1"); } catch { /* storage blocked */ }
      return !c;
    });
  }
  const screensBadge = screens.reduce((sum, n) => sum + (n.badge ?? 0), 0);
  return (
    <div className={`shell ${collapsed ? "rail-collapsed" : ""}`}>
      <aside className="rail">
        <button className="rail-toggle" onClick={toggle} aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand the menu" : "Collapse the menu"} title={collapsed ? "Expand" : "Collapse"}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>
        <div className="brand">
          <img src="/icon.svg" alt="" />
          <span className="brand-name">Pass</span>
        </div>
        {profile && (
          <button className="agent-switch" onClick={onSwitchAgent} title={collapsed ? `${profile.display_name}: switch agent or customise` : "Switch agent or customise"}>
            <Avatar profile={profile} size={30} />
            <span>
              <strong>{profile.display_name}</strong>
              <span className="small muted">{profile.label}</span>
            </span>
          </button>
        )}
        <nav className="nav" aria-label={profile?.display_name ?? "Agent"}>
          {primary.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} title={collapsed ? n.label : undefined}>
              <NavIcon label={n.label} />
              <span className="nav-label">{n.label}</span>
              {badge(n)}
            </NavLink>
          ))}
        </nav>
        {screens.length > 0 && (
          <>
            <p className="rail-h">Screens</p>
            <nav className="nav nav-screens" aria-label="Screens">
              {screens.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} title={collapsed ? n.label : undefined}>
                  <NavIcon label={n.label} />
                  <span className="nav-label">{n.label}</span>
                  {badge(n)}
                </NavLink>
              ))}
            </nav>
          </>
        )}
        <div className="rail-foot">
          {!collapsed && footer}
          <button className="btn small rail-signout" onClick={() => void signOut().then(() => window.location.assign("/"))}
            title={collapsed ? "Sign out" : undefined} aria-label="Sign out">
            {collapsed ? (
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" /><path d="M10 17l-5-5 5-5" /><path d="M5 12h11" />
              </svg>
            ) : "Sign out"}
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
