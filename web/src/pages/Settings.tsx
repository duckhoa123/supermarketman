import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api, type IntegrationRow } from "../lib/api";
import { timeAgo } from "../lib/format";
import RestaurantProfile from "../components/RestaurantProfile";

const PROVIDERS = [
  { id: "clover", name: "Clover", about: "Sales, orders, menu items and stock levels." },
  { id: "square", name: "Square", about: "Coming next. Same data as Clover.", soon: true },
  { id: "google", name: "Google reviews", about: "New reviews, alerts for bad ones, and replies from the app." },
];

const RULES = [
  { kind: "bad_review", label: "A review of 1–3 stars, or one that mentions illness or safety" },
  { kind: "low_stock", label: "An item drops to its alert level" },
  { kind: "sales_drop", label: "Yesterday's sales were well below a normal day" },
  { kind: "daily_digest", label: "Morning end-of-day report for yesterday" },
  { kind: "weekly_report", label: "Weekly report on Mondays" },
  { kind: "monthly_report", label: "Monthly report on the 1st" },
  { kind: "new_order", label: "A new order or booking from the customer app" },
];
const CHANNELS = ["push", "email", "whatsapp", "sms"] as const;
const CHANNEL_LABEL: Record<string, string> = { push: "App", email: "Email", whatsapp: "WhatsApp", sms: "SMS" };

const ERRORS: Record<string, string> = {
  cancelled: "Connection was cancelled.",
  expired: "The connection link expired. Try again.",
  forbidden: "Only owners and managers can connect accounts.",
  already_linked: "That account is already linked to another restaurant.",
  no_merchant: "The POS didn't say which merchant to link. Try again.",
  token: "The provider rejected the connection. Try again.",
};

type Rule = { id: string; kind: string; channels: string[]; enabled: boolean; params: Record<string, unknown> };

async function enableWebPush(): Promise<string> {
  const key = import.meta.env.VITE_VAPID_PUBLIC_KEY as string | undefined;
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "This browser can't receive notifications.";
  if (!key) return "Browser notifications aren't set up on this server yet.";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "Notifications are blocked for this site in your browser settings.";
  const reg = await navigator.serviceWorker.ready;
  const raw = atob(key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (key.length % 4)) % 4));
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: Uint8Array.from(raw, (c) => c.charCodeAt(0)),
  });
  await api("/me/push-subscriptions", { method: "POST", body: { kind: "webpush", token: JSON.stringify(sub) } });
  return "Notifications are on for this browser.";
}

export default function Settings() {
  const { me, location, refreshMe } = useApp();
  const [params, setParams] = useSearchParams();
  const integrations = useApi<IntegrationRow[]>(`/locations/${location.id}/integrations`);
  const rules = useApi<Rule[]>(`/locations/${location.id}/alert-rules`);
  const [phone, setPhone] = useState(me.phone ?? "");
  const [channels, setChannels] = useState(me.channels);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const connected = params.get("connected");
    if (!connected) return;
    const err = params.get("error");
    setNotice(err ? ERRORS[err] ?? "Connection failed." : `Connected. The first sync runs in the background.`);
    setParams({}, { replace: true });
  }, [params, setParams]);

  async function connect(provider: string) {
    try {
      const { url } = await api<{ url: string }>(`/locations/${location.id}/integrations/${provider}/connect`);
      window.location.href = url;
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Couldn't start the connection.");
    }
  }

  async function disconnect(provider: string) {
    if (!window.confirm("Disconnect? Data already synced stays; new data stops coming in.")) return;
    await api(`/locations/${location.id}/integrations/${provider}`, { method: "DELETE" });
    await integrations.reload();
  }

  async function pickGoogleLocation(name: string) {
    await api(`/locations/${location.id}/integrations/google/location`, { method: "PUT", body: { name } });
    await integrations.reload();
  }

  async function saveProfile() {
    try {
      await api("/me", { method: "PUT", body: { phone: phone || undefined, channels } });
      await refreshMe();
      setNotice("Saved.");
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Couldn't save.");
    }
  }

  async function toggleRule(kind: string, channel: string, on: boolean) {
    const rule = rules.data?.find((r) => r.kind === kind);
    const current = rule?.channels ?? [];
    const next = on ? Array.from(new Set([...current, channel])) : current.filter((c) => c !== channel);
    await api(`/locations/${location.id}/alert-rules`, {
      method: "PUT",
      body: { kind, channels: next, params: rule?.params ?? {}, enabled: next.length > 0 },
    });
    await rules.reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>{location.name}</p>
        </div>
      </div>
      {notice && <p className="panel" role="status">{notice}</p>}

      <div className="stack" style={{ gap: 16 }}>
        <section className="panel">
          <h2>Connected accounts</h2>
          <ul className="list">
            {PROVIDERS.map((p) => {
              const row = integrations.data?.find((i) => i.provider === p.id && i.status !== "disconnected");
              return (
                <li key={p.id} className="stack" style={{ gap: 6 }}>
                  <div className="spread">
                    <div>
                      <strong>{p.name}</strong>
                      <p className="small muted">{p.about}</p>
                    </div>
                    {row ? (
                      <div className="row">
                        <span className={`pill ${row.status === "active" ? "good" : "low"}`}>
                          {row.status === "active"
                            ? row.last_synced_at
                              ? `Synced ${timeAgo(row.last_synced_at)}`
                              : "Connected"
                            : "Needs reconnecting"}
                        </span>
                        {row.status !== "active" && (
                          <button className="btn small primary" onClick={() => void connect(p.id)}>
                            Reconnect
                          </button>
                        )}
                        <button className="btn small danger" onClick={() => void disconnect(p.id)}>
                          Disconnect
                        </button>
                      </div>
                    ) : (
                      <button className="btn small primary" disabled={p.soon} onClick={() => void connect(p.id)}>
                        {p.soon ? "Coming soon" : `Connect ${p.name}`}
                      </button>
                    )}
                  </div>
                  {row?.last_error && <p className="small error">{row.last_error}</p>}
                  {p.id === "google" && row && !row.external_id && row.choices && row.choices.length > 0 && (
                    <label className="row small">
                      Which business?
                      <select className="select" style={{ width: "auto" }} defaultValue=""
                        onChange={(e) => void pickGoogleLocation(e.target.value)}>
                        <option value="" disabled>
                          Choose a location
                        </option>
                        {row.choices.map((c) => (
                          <option key={c.name} value={c.name}>
                            {c.title}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        <section className="panel">
          <h2>Tell me when…</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th />
                  {CHANNELS.map((c) => (
                    <th key={c} className="num">
                      {CHANNEL_LABEL[c]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {RULES.map((r) => {
                  const rule = rules.data?.find((x) => x.kind === r.kind);
                  return (
                    <tr key={r.kind}>
                      <td>{r.label}</td>
                      {CHANNELS.map((c) => (
                        <td key={c} className="num">
                          <input
                            type="checkbox"
                            aria-label={`${r.label} by ${CHANNEL_LABEL[c]}`}
                            checked={rule ? rule.enabled && rule.channels.includes(c) : false}
                            onChange={(e) => void toggleRule(r.kind, c, e.target.checked)}
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="small muted" style={{ marginTop: 10 }}>
            WhatsApp and SMS also need your mobile number below, and cost a few cents per message.
          </p>
        </section>

        <section className="panel stack">
          <h2>You</h2>
          <p className="small muted">Signed in as {me.email}</p>
          <label className="stack" style={{ gap: 6, maxWidth: 320 }}>
            <span className="small">Mobile for WhatsApp and SMS (with country code)</span>
            <input className="input" placeholder="+14165551234" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </label>
          <div className="row">
            {CHANNELS.map((c) => (
              <label key={c} className="check">
                <input
                  type="checkbox"
                  checked={channels[c]}
                  onChange={(e) => setChannels({ ...channels, [c]: e.target.checked })}
                />
                {CHANNEL_LABEL[c]}
              </label>
            ))}
          </div>
          <div className="row">
            <button className="btn primary" onClick={() => void saveProfile()}>
              Save
            </button>
            <button className="btn" onClick={() => void enableWebPush().then(setNotice)}>
              Turn on notifications in this browser
            </button>
          </div>
        </section>
        <RestaurantProfile />
      </div>
    </>
  );
}
