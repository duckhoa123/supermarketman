import { useApi } from "../hooks";
import { api, type PersonalAlert } from "../lib/api";
import { timeAgo } from "../lib/format";

/** Personal notifications: order and booking updates for customers, jobs and busy alerts for drivers. */
export default function Updates({ onRead }: { onRead: () => void }) {
  const { data, reload } = useApi<{ unread: number; alerts: PersonalAlert[] }>("/me/alerts");
  async function readAll() {
    await api("/me/alerts/read-all", { method: "POST", body: {} });
    await reload();
    onRead();
  }
  return (
    <>
      <div className="page-head">
        <h1>Updates</h1>
        {data && data.unread > 0 && <button className="btn small" onClick={() => void readAll()}>Mark all read</button>}
      </div>
      {data?.alerts.length === 0 && <p className="empty panel">Nothing yet.</p>}
      <section className="panel">
        <ul className="list">
          {data?.alerts.map((a) => (
            <li key={a.id}>
              <div className="spread">
                <strong>{a.title}</strong>
                <span className="small muted">{timeAgo(a.created_at)}</span>
              </div>
              <div className={a.read ? "small muted" : "small"}>{a.body}</div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
