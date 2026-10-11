import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { AppContext } from "./context";
import { api, ApiError, type Me } from "./lib/api";
import { getToken, onAuthChange, signOut } from "./lib/auth";
import Alerts from "./pages/Alerts";
import Assistant from "./pages/Assistant";
import Login from "./pages/Login";
import More from "./pages/More";
import Onboarding from "./pages/Onboarding";
import Orders from "./pages/Orders";
import Reviews from "./pages/Reviews";
import Settings from "./pages/Settings";
import Stock from "./pages/Stock";
import Tables from "./pages/Tables";
import Today from "./pages/Today";

type Phase = "loading" | "signed-out" | "onboarding" | "ready" | "error";

const NAV = [
  { to: "/", label: "Assistant", end: true },
  { to: "/today", label: "Today" },
  { to: "/stock", label: "Stock" },
  { to: "/orders", label: "Orders" },
  { to: "/tables", label: "Tables" },
  { to: "/reviews", label: "Reviews" },
  { to: "/alerts", label: "Alerts" },
  { to: "/settings", label: "Settings" },
];
// Phones get the most-used pages as tabs; the rest live under "More".
const TABS = ["/", "/today", "/stock", "/reviews", "/alerts"];

export default function App() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [locationId, setLocationId] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState("");

  const refreshMe = useCallback(async () => {
    if (!(await getToken())) {
      setPhase("signed-out");
      return;
    }
    try {
      const data = await api<Me>("/me");
      setMe(data);
      setLocationId((current) => current ?? data.locations[0]?.id ?? null);
      setPhase(data.locations.length ? "ready" : "onboarding");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        await signOut();
        setPhase("signed-out");
      } else {
        setError(e instanceof Error ? e.message : "Couldn't reach the server.");
        setPhase("error");
      }
    }
  }, []);

  const refreshUnread = useCallback(() => {
    if (!locationId) return;
    api<{ unread: number }>(`/locations/${locationId}/alerts?unread_only=true&limit=1`)
      .then((r) => setUnread(r.unread))
      .catch(() => undefined);
  }, [locationId]);

  useEffect(() => {
    void refreshMe();
    return onAuthChange(() => void refreshMe());
  }, [refreshMe]);

  useEffect(() => {
    refreshUnread();
    const t = window.setInterval(refreshUnread, 60_000);
    return () => window.clearInterval(t);
  }, [refreshUnread]);

  if (phase === "loading") return <div className="center muted">Loading…</div>;
  if (phase === "signed-out") return <Login onSignedIn={refreshMe} />;
  if (phase === "error")
    return (
      <div className="center">
        <div className="card-narrow">
          <h1>Can't reach Pass</h1>
          <p className="muted">{error} Check your connection, then try again.</p>
          <button className="btn primary" onClick={() => void refreshMe()}>
            Try again
          </button>
        </div>
      </div>
    );
  if (phase === "onboarding" || !me) return <Onboarding onDone={refreshMe} demoAvailable={!!me?.demo_available} />;

  const location = me.locations.find((l) => l.id === locationId) ?? me.locations[0];

  return (
    <AppContext.Provider value={{ me, location, unread, refreshUnread, refreshMe }}>
      <div className="shell">
        <aside className="rail">
          <div className="brand">
            <img src="/icon.svg" alt="" />
            Pass
          </div>
          <nav className="nav">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end}>
                {n.label}
                {n.to === "/alerts" && unread > 0 && <span className="badge">{unread}</span>}
              </NavLink>
            ))}
          </nav>
          <div className="rail-foot">
            {me.locations.length > 1 ? (
              <select
                className="select"
                value={location.id}
                onChange={(e) => setLocationId(e.target.value)}
                aria-label="Restaurant"
              >
                {me.locations.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            ) : (
              <span className="small muted">{location.name}</span>
            )}
            <button className="btn small" onClick={() => void signOut().then(refreshMe)}>
              Sign out
            </button>
          </div>
        </aside>
        <main className="main">
          <Routes>
            <Route path="/" element={<Assistant key={location.id} />} />
            <Route path="/today" element={<Today />} />
            <Route path="/stock" element={<Stock />} />
            <Route path="/orders" element={<Orders />} />
            <Route path="/tables" element={<Tables />} />
            <Route path="/reviews" element={<Reviews />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/settings" element={<Settings />} />
            <Route
              path="/more"
              element={<More locations={me.locations} current={location.id} onSwitch={setLocationId} />}
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
        <nav className="tabs" aria-label="Main">
          {NAV.filter((n) => TABS.includes(n.to)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
              {n.to === "/alerts" && unread > 0 && <span className="badge">{unread}</span>}
            </NavLink>
          ))}
          <NavLink to="/more">More</NavLink>
        </nav>
      </div>
    </AppContext.Provider>
  );
}
