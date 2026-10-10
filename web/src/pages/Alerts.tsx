import { useEffect } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api, type AlertRow } from "../lib/api";
import { timeAgo } from "../lib/format";

const KIND: Record<string, { label: string; tone: string }> = {
  bad_review: { label: "Review", tone: "low" },
  low_stock: { label: "Stock", tone: "warn" },
  sales_drop: { label: "Sales", tone: "low" },
  daily_digest: { label: "Daily summary", tone: "good" },
  integration_error: { label: "Connection", tone: "low" },
};

export default function Alerts() {
  const { location, refreshUnread } = useApp();
  const { data, error, reload } = useApi<{ unread: number; alerts: AlertRow[] }>(`/locations/${location.id}/alerts`);

  useEffect(() => {
    if (data && data.unread > 0) {
      // Opening the inbox marks everything read; unread items keep their highlight until the next visit.
      void api(`/locations/${location.id}/alerts/read-all`, { method: "POST" }).then(refreshUnread);
    }
  }, [data, location.id, refreshUnread]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p>Things that need you: bad reviews, low stock, slow days and your morning summary.</p>
        </div>
        <button className="btn small" onClick={() => void reload()}>
          Refresh
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <section className="panel">
          <ul className="list">
            {data.alerts.map((a) => (
              <li key={a.id} className="stack" style={{ gap: 4 }}>
                <div className="spread">
                  <span className="row" style={{ gap: 8 }}>
                    <span className={`pill ${KIND[a.kind]?.tone ?? ""}`}>{KIND[a.kind]?.label ?? a.kind}</span>
                    <strong style={{ fontWeight: a.read ? 500 : 700 }}>{a.title}</strong>
                  </span>
                  <span className="small muted">{timeAgo(a.created_at)}</span>
                </div>
                <p className="muted" style={{ whiteSpace: "pre-wrap" }}>
                  {a.body}
                </p>
              </li>
            ))}
          </ul>
          {data.alerts.length === 0 && <p className="empty">You're all caught up.</p>}
        </section>
      )}
    </>
  );
}
