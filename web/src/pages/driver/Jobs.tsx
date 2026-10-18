import { useEffect, useRef, useState } from "react";
import { useApi } from "../../hooks";
import { api, type DriverMe } from "../../lib/api";

function time(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";
}

/** Shift on/off, live position while on shift, and the current pickups and drop-offs. */
export default function Jobs() {
  const { data, reload, error } = useApi<DriverMe>("/driver/me");
  const [message, setMessage] = useState("");
  const watch = useRef<number | null>(null);
  const onShift = data?.driver.status === "on_shift";

  useEffect(() => {
    const t = window.setInterval(() => void reload(), 15_000);
    return () => window.clearInterval(t);
  }, [reload]);

  // Share position every so often while on shift, so the nearest driver gets the next job.
  useEffect(() => {
    if (!onShift || !navigator.geolocation) return;
    let last = 0;
    watch.current = navigator.geolocation.watchPosition(
      (p) => {
        if (Date.now() - last < 30_000) return;
        last = Date.now();
        void api("/driver/location", { method: "POST", body: { lat: p.coords.latitude, lng: p.coords.longitude } });
      },
      () => undefined,
      { enableHighAccuracy: true, maximumAge: 20_000 }
    );
    return () => {
      if (watch.current !== null) navigator.geolocation.clearWatch(watch.current);
    };
  }, [onShift]);

  async function post(path: string, body: unknown = {}) {
    setMessage("");
    try {
      await api(path, { method: "POST", body });
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "That didn't work.");
    }
  }

  function start() {
    if (!navigator.geolocation) return void post("/driver/shift/start");
    navigator.geolocation.getCurrentPosition(
      (p) => void post("/driver/shift/start", { lat: p.coords.latitude, lng: p.coords.longitude }),
      () => void post("/driver/shift/start"),
      { timeout: 8000 }
    );
  }

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const d = data.driver;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{onShift ? "On shift" : d.status === "on_call" ? "On call" : "Off shift"}</h1>
          <p>
            {data.shift ? `Since ${time(data.shift.started_at)} · ` : ""}
            {data.today.deliveries} deliveries today · {data.today.tips} tips
          </p>
        </div>
        <div className="row">
          {onShift ? (
            <>
              <button className="btn" onClick={() => void post("/driver/shift/end", { on_call: true })}>End shift, stay on call</button>
              <button className="btn" onClick={() => void post("/driver/shift/end", { on_call: false })}>End shift</button>
            </>
          ) : (
            <>
              <button className="btn primary" onClick={start}>Start shift</button>
              <button className="btn" onClick={() => void post("/driver/availability", { status: d.status === "on_call" ? "off" : "on_call" })}>
                {d.status === "on_call" ? "Stop being on call" : "Be on call"}
              </button>
            </>
          )}
        </div>
      </div>
      {message && <p className="error">{message}</p>}
      {onShift && data.deliveries.length === 0 && (
        <p className="empty panel">No jobs right now. New pickups appear here and as a notification.</p>
      )}
      {!onShift && d.status === "on_call" && (
        <p className="panel small">You'll get a notification when it gets busy and more drivers are needed.</p>
      )}
      <div className="stack">
        {data.deliveries.map((j) => (
          <section key={j.id} className="panel stack" style={{ gap: 8 }}>
            <div className="spread">
              <strong>{j.status === "assigned" ? `Pick up at ${j.restaurant}` : `Deliver to ${j.customer_name ?? "customer"}`}</strong>
              <span className="pill warn">{j.distance_km} km</span>
            </div>
            <div className="small">
              {j.status === "assigned" ? j.pickup.address : j.dropoff.address}
              {j.items != null && ` · ${j.items} item${j.items === 1 ? "" : "s"}`}
            </div>
            {j.notes && <div className="small">Note: {j.notes}</div>}
            <div className="small muted">Arrive by about {time(j.eta_at)}{j.tip !== "$0.00" && ` · tip ${j.tip}`}</div>
            <div className="row">
              <a className="btn small" href={j.navigate_url} target="_blank" rel="noreferrer">Navigate</a>
              {j.status === "assigned" ? (
                <button className="btn primary small" onClick={() => void post(`/driver/deliveries/${j.id}/status`, { status: "picked_up" })}>
                  Picked up
                </button>
              ) : (
                <button className="btn primary small" onClick={() => void post(`/driver/deliveries/${j.id}/status`, { status: "delivered" })}>
                  Delivered
                </button>
              )}
            </div>
          </section>
        ))}
      </div>
      <p className="small muted" style={{ marginTop: 16 }}>Ride safe: never use your phone while moving.</p>
    </>
  );
}
