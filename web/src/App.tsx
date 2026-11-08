import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { PersonalDock, RestaurantDock, personalChat, useRestaurantChat } from "./components/AgentDocks";
import Shell, { type NavItem } from "./components/Shell";
import { WorkProvider, useAgentHome } from "./components/WorkProvider";
import { AppContext, MeContext } from "./context";
import { api, API_URL, ApiError, type AgentKind, type Me } from "./lib/api";
import { getToken, onAuthChange, signOut } from "./lib/auth";
import {
  captureCloverLaunch, captureWebsiteLink, cartToParam, initialMode, pendingCloverLaunch, storeMode, takeWebsiteLink,
} from "./lib/mode";
import Agents from "./pages/Agents";
import AgentActivity from "./pages/agent/Activity";
import AgentHome from "./pages/agent/Home";
import WorkingOn from "./pages/agent/WorkingOn";
import You from "./pages/agent/You";
import Alerts from "./pages/Alerts";
import AppOrders from "./pages/AppOrders";
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
import Website from "./pages/Website";
import Discover from "./pages/customer/Discover";
import MyOrders from "./pages/customer/MyOrders";
import Nutrition from "./pages/customer/Nutrition";
import Profile from "./pages/customer/Profile";
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

captureWebsiteLink();
captureCloverLaunch();

export default function App() {
  const navigate = useNavigate();
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
      const fromSite = takeWebsiteLink();
      const fromClover = data.locations.length ? pendingCloverLaunch(true) : null; // after onboarding if needed
      if (fromClover !== null) {
        // Opened from Clover: go to Settings to connect it to this restaurant.
        storeMode("restaurant_manager");
        setModeState("restaurant_manager");
        navigate(`/settings?clover_launch=1${fromClover ? `&merchant_id=${encodeURIComponent(fromClover)}` : ""}`, { replace: true });
      } else if (fromSite) {
        // Came from a restaurant's website: open that restaurant in the customer app.
        storeMode("customer");
        setModeState("customer");
        const q = new URLSearchParams({ restaurant: fromSite.restaurant });
        if (fromSite.book) q.set("book", "1");
        if (fromSite.date) q.set("date", fromSite.date);
        if (fromSite.time) q.set("time", fromSite.time);
        if (fromSite.party) q.set("party", String(fromSite.party));
        if (fromSite.items) q.set("cart", cartToParam(fromSite.items));
        navigate(`/discover?${q.toString()}`, { replace: true });
      } else {
        setModeState((current) => (current && data.agents.some((a) => a.agent === current) ? current : initialMode(data)));
      }
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
  }, [navigate]);

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

/** The shell for one agent: its own four tabs first (Chat, Working on, Activity, You), then the classic screens. */
function AgentShell({ screens, footer, onSwitchAgent, children }: {
  screens: NavItem[];
  footer?: ReactNode;
  onSwitchAgent: () => void;
  children: ReactNode;
}) {
  const { agent, profile, work } = useAgentHome();
  const primary: NavItem[] = [
    { to: "/", label: "Chat", end: true },
    { to: "/working", label: agent === "driver" ? "My shift" : "Working on", badge: work?.needs_you.length || undefined },
    { to: "/activity", label: "Activity" },
    { to: "/you", label: "You" },
  ];
  return (
    <Shell primary={primary} screens={screens} profile={profile} footer={footer} onSwitchAgent={onSwitchAgent}>
      {children}
    </Shell>
  );
}

function RestaurantHome() {
  return <AgentHome chat={useRestaurantChat()} />;
}

function RestaurantApp({ me, refreshMe, setMode }: ModeProps) {
  const [locationId, setLocationId] = useState<string | null>(me.locations[0]?.id ?? null);
  const location = me.locations.find((l) => l.id === locationId) ?? me.locations[0];
  const [unread, refreshUnread] = usePolling(`/locations/${location.id}/alerts?unread_only=true&limit=1`);
  const navigate = useNavigate();
  const nav: NavItem[] = [
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
    { to: "/website", label: "Website" },
    { to: "/alerts", label: "Alerts", badge: unread },
    { to: "/settings", label: "Settings" },
    ...(me.is_fleet_admin ? [{ to: "/fleet", label: "Fleet" }] : []),
  ];
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
      <WorkProvider agent="restaurant_manager" profile={me.agents.find((a) => a.agent === "restaurant_manager")}
        locationId={location.id}>
      <RestaurantDock>
      <AgentShell screens={nav} footer={picker} onSwitchAgent={() => navigate("/agents")}>
        <Routes>
          <Route path="/" element={<RestaurantHome />} />
          <Route path="/working" element={<WorkingOn />} />
          <Route path="/activity" element={<AgentActivity />} />
          <Route path="/you" element={<You />} />
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
          <Route path="/website" element={<Website />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/settings" element={<Settings />} />
          {me.is_fleet_admin && <Route path="/fleet" element={<Fleet />} />}
          <Route path="/agents" element={<AgentsRoute me={me} refreshMe={refreshMe} setMode={setMode} current="restaurant_manager" />} />
          <Route
            path="/more"
            element={
              <More links={[...nav, { to: "/agents", label: "Switch agent or customise" }]}>
                {me.locations.length > 1 && <li className="stack" style={{ gap: 6 }}>{picker}</li>}
              </More>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AgentShell>
      </RestaurantDock>
      </WorkProvider>
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
          { to: "/discover", label: "Discover" },
          { to: "/orders", label: "Orders" },
          { to: "/nutrition", label: "Food log" },
          { to: "/profile", label: "Profile" },
          { to: "/updates", label: "Updates", badge: unread },
          ...fleet,
        ]
      : [
          { to: "/jobs", label: "Jobs" },
          { to: "/earnings", label: "Earnings" },
          { to: "/updates", label: "Updates", badge: unread },
          ...fleet,
        ];
  const profile = me.agents.find((a) => a.agent === agent);

  return (
    <WorkProvider agent={agent} profile={profile}>
    <PersonalDock me={me} agent={agent}>
    <AgentShell screens={nav} onSwitchAgent={() => navigate("/agents")}>
      <Routes>
        <Route path="/" element={<AgentHome chat={personalChat(me, agent)} />} />
        <Route path="/working" element={<WorkingOn />} />
        <Route path="/activity" element={<AgentActivity />} />
        <Route path="/you" element={<You />} />
        {agent === "customer" ? (
          <>
            <Route path="/discover" element={<Discover />} />
            <Route path="/orders" element={<MyOrders />} />
            <Route path="/nutrition" element={<Nutrition />} />
            <Route path="/profile" element={<Profile />} />
          </>
        ) : (
          <>
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/earnings" element={<Earnings />} />
          </>
        )}
        <Route path="/updates" element={<Updates onRead={refreshUnread} />} />
        {me.is_fleet_admin && <Route path="/fleet" element={<Fleet />} />}
        <Route path="/agents" element={<AgentsRoute me={me} refreshMe={refreshMe} setMode={setMode} current={agent} />} />
        <Route path="/more" element={<More links={[...nav, { to: "/agents", label: "Switch agent or customise" }]} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AgentShell>
    </PersonalDock>
    </WorkProvider>
  );
}
