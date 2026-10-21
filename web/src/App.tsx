import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import Shell, { type NavItem } from "./components/Shell";
import { AppContext, MeContext } from "./context";
import { api, API_URL, ApiError, type AgentKind, type Me } from "./lib/api";
import { getToken, onAuthChange, signOut } from "./lib/auth";
import { initialMode, storeMode } from "./lib/mode";
import Agents from "./pages/Agents";
import Alerts from "./pages/Alerts";
import AppOrders from "./pages/AppOrders";
import Assistant from "./pages/Assistant";
import Bookings from "./pages/Bookings";
import Login from "./pages/Login";
import More from "./pages/More";
import Onboarding from "./pages/Onboarding";
import Orders from "./pages/Orders";
import Plan from "./pages/Plan";
import Purchasing from "./pages/Purchasing";
import Receiving from "./pages/Receiving";
import Reviews from "./pages/Reviews";
import Settings from "./pages/Settings";
import Stock from "./pages/Stock";
import Tables from "./pages/Tables";
import Today from "./pages/Today";
import Updates from "./pages/Updates";
import CustomerChat from "./pages/customer/CustomerChat";
import Discover from "./pages/customer/Discover";
import MyOrders from "./pages/customer/MyOrders";
import Nutrition from "./pages/customer/Nutrition";
import Profile from "./pages/customer/Profile";
import DriverChat from "./pages/driver/DriverChat";
import Earnings from "./pages/driver/Earnings";
import Fleet from "./pages/driver/Fleet";
import Jobs from "./pages/driver/Jobs";

type Phase = "loading" | "signed-out" | "ready" | "error";

function usePolling(path: string | null, every = 60_000): [number, () => void] {
  const [count, setCount] = useState(0);
  const refresh = useCallback(() => {
    if (!path) return;
    api<{ unread: number }>(path)
      .then((r) => setCount(r.unread))
      .catch(() => undefined);
  }, [path]);
  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, every);
    return () => window.clearInterval(t);
  }, [refresh, every]);
  return [count, refresh];
}

export default function App() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [mode, setModeState] = useState<AgentKind | null>(null);
  const [error, setError] = useState("");

  const refreshMe = useCallback(async () => {
    if (!(await getToken())) {
      setPhase("signed-out");
      return;
    }
    try {
      const data = await api<Me>("/me");
      setMe(data);
      setModeState((current) => (current && data.agents.some((a) => a.agent === current) ? current : initialMode(data)));
      setPhase("ready");
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

  useEffect(() => {
    void refreshMe();
    return onAuthChange(() => void refreshMe());
  }, [refreshMe]);

  const setMode = (m: AgentKind | null) => {
    storeMode(m);
    setModeState(m);
  };

  if (phase === "loading") return <div className="center muted">Loading…</div>;
  if (phase === "signed-out") return <Login onSignedIn={refreshMe} />;
  if (phase === "error" || !me)
    return (
      <div className="center">
        <div className="card-narrow">
          <h1>Can't reach Pass</h1>
          <p className="muted">
            The server answered: "{error}". Check your connection and try again. If it keeps happening, the web app
            may be pointing at the wrong API address (VITE_API_URL).
          </p>
          <p className="small muted">
            Tried: <code>{API_URL}/me</code>
          </p>
          <button className="btn primary" onClick={() => void refreshMe()}>
            Try again
          </button>
          <button className="btn" onClick={() => void signOut().then(refreshMe)}>
            Sign out
          </button>
        </div>
      </div>
    );

  if (mode === null) return <Agents me={me} current={null} onPick={setMode} refreshMe={refreshMe} />;
  if (mode === "restaurant_manager" && me.locations.length === 0)
    return <Onboarding onDone={refreshMe} demoAvailable={me.demo_available} onBack={() => setMode(null)} />;

  return (
    <MeContext.Provider value={{ me, refreshMe }}>
      {mode === "restaurant_manager" ? (
        <RestaurantApp me={me} refreshMe={refreshMe} setMode={setMode} />
      ) : mode === "customer" ? (
        <PersonalApp me={me} refreshMe={refreshMe} setMode={setMode} agent="customer" />
      ) : (
        <PersonalApp me={me} refreshMe={refreshMe} setMode={setMode} agent="driver" />
      )}
    </MeContext.Provider>
  );
}

type ModeProps = { me: Me; refreshMe: () => Promise<void>; setMode: (m: AgentKind | null) => void };

function AgentsRoute({ me, refreshMe, setMode, current }: ModeProps & { current: AgentKind }) {
  const navigate = useNavigate();
  return (
    <Agents
      me={me}
      current={current}
      refreshMe={refreshMe}
      onPick={(a) => {
        setMode(a);
        navigate("/");
      }}
    />
  );
}

function RestaurantApp({ me, refreshMe, setMode }: ModeProps) {
  const [locationId, setLocationId] = useState<string | null>(me.locations[0]?.id ?? null);
  const location = me.locations.find((l) => l.id === locationId) ?? me.locations[0];
  const [unread, refreshUnread] = usePolling(`/locations/${location.id}/alerts?unread_only=true&limit=1`);
  const navigate = useNavigate();
  const nav: NavItem[] = [
    { to: "/", label: "Assistant", end: true },
    { to: "/today", label: "Today" },
    { to: "/plan", label: "Plan" },
    { to: "/app-orders", label: "App orders" },
    { to: "/bookings", label: "Bookings" },
    { to: "/stock", label: "Stock" },
    { to: "/receiving", label: "Receiving" },
    { to: "/purchasing", label: "Purchasing" },
    { to: "/orders", label: "POS orders" },
    { to: "/tables", label: "Tables" },
    { to: "/reviews", label: "Reviews" },
    { to: "/alerts", label: "Alerts", badge: unread },
    { to: "/settings", label: "Settings" },
    ...(me.is_fleet_admin ? [{ to: "/fleet", label: "Fleet" }] : []),
  ];
  const tabs = ["/", "/today", "/app-orders", "/receiving", "/alerts"];
  const picker =
    me.locations.length > 1 ? (
      <select className="select" value={location.id} onChange={(e) => setLocationId(e.target.value)} aria-label="Restaurant">
        {me.locations.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name}
          </option>
        ))}
      </select>
    ) : (
      <span className="small muted">{location.name}</span>
    );

  return (
    <AppContext.Provider value={{ me, location, unread, refreshUnread, refreshMe }}>
      <Shell nav={nav} tabs={tabs} profile={me.agents.find((a) => a.agent === "restaurant_manager")}
        footer={picker} onSwitchAgent={() => navigate("/agents")}>
        <Routes>
          <Route path="/" element={<Assistant />} />
          <Route path="/today" element={<Today />} />
          <Route path="/plan" element={<Plan />} />
          <Route path="/app-orders" element={<AppOrders />} />
          <Route path="/bookings" element={<Bookings />} />
          <Route path="/stock" element={<Stock />} />
          <Route path="/receiving" element={<Receiving />} />
          <Route path="/purchasing" element={<Purchasing />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/tables" element={<Tables />} />
          <Route path="/reviews" element={<Reviews />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/settings" element={<Settings />} />
          {me.is_fleet_admin && <Route path="/fleet" element={<Fleet />} />}
          <Route path="/agents" element={<AgentsRoute me={me} refreshMe={refreshMe} setMode={setMode} current="restaurant_manager" />} />
          <Route
            path="/more"
            element={
              <More links={[...nav.filter((n) => !tabs.includes(n.to)), { to: "/agents", label: "Switch agent or customise" }]}>
                {me.locations.length > 1 && <li className="stack" style={{ gap: 6 }}>{picker}</li>}
              </More>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </AppContext.Provider>
  );
}

function PersonalApp({ me, refreshMe, setMode, agent }: ModeProps & { agent: "customer" | "driver" }) {
  const [unread, refreshUnread] = usePolling("/me/alerts?unread_only=true", 30_000);
  const navigate = useNavigate();
  const fleet = me.is_fleet_admin ? [{ to: "/fleet", label: "Fleet" }] : [];
  const nav: NavItem[] =
    agent === "customer"
      ? [
          { to: "/", label: "Assistant", end: true },
          { to: "/discover", label: "Discover" },
          { to: "/orders", label: "Orders" },
          { to: "/nutrition", label: "Food log" },
          { to: "/profile", label: "Profile" },
          { to: "/updates", label: "Updates", badge: unread },
          ...fleet,
        ]
      : [
          { to: "/", label: "Jobs", end: true },
          { to: "/assistant", label: "Assistant" },
          { to: "/earnings", label: "Earnings" },
          { to: "/updates", label: "Updates", badge: unread },
          ...fleet,
        ];
  const tabs = nav.slice(0, 5).map((n) => n.to);

  return (
    <Shell nav={nav} tabs={tabs} profile={me.agents.find((a) => a.agent === agent)} onSwitchAgent={() => navigate("/agents")}>
      <Routes>
        {agent === "customer" ? (
          <>
            <Route path="/" element={<CustomerChat />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/orders" element={<MyOrders />} />
            <Route path="/nutrition" element={<Nutrition />} />
            <Route path="/profile" element={<Profile />} />
          </>
        ) : (
          <>
            <Route path="/" element={<Jobs />} />
            <Route path="/assistant" element={<DriverChat />} />
            <Route path="/earnings" element={<Earnings />} />
          </>
        )}
        <Route path="/updates" element={<Updates onRead={refreshUnread} />} />
        {me.is_fleet_admin && <Route path="/fleet" element={<Fleet />} />}
        <Route path="/agents" element={<AgentsRoute me={me} refreshMe={refreshMe} setMode={setMode} current={agent} />} />
        <Route
          path="/more"
          element={<More links={[...nav.filter((n) => !tabs.includes(n.to)), { to: "/agents", label: "Switch agent or customise" }]} />}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
