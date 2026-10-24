import { useState } from "react";
import { useApp } from "../context";
import { useApi } from "../hooks";
import { api, type ReviewRow } from "../lib/api";

function ReviewCard({ review, locationId, onReplied }: { review: ReviewRow; locationId: string; onReplied: () => void }) {
  const [draft, setDraft] = useState(review.suggested_reply ?? "");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function post() {
    setBusy(true);
    setError("");
    try {
      await api(`/locations/${locationId}/reviews/${review.id}/reply`, { method: "POST", body: { text: draft } });
      onReplied();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reply wasn't posted.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="stack" style={{ gap: 6 }}>
      <div className="spread">
        <span>
          <span className="stars" aria-label={`${review.rating} stars`}>
            {"★".repeat(review.rating)}
            {"☆".repeat(5 - review.rating)}
          </span>{" "}
          <strong>{review.author ?? "Customer"}</strong>
        </span>
        <span className="small muted">
          {review.source} · {review.posted}
        </span>
      </div>
      <p>{review.text || <span className="muted">No written review.</span>}</p>
      <div className="row">
        {review.topics.map((t) => (
          <span key={t} className="pill">
            {t}
          </span>
        ))}
        {review.replied ? (
          <span className="pill good">Replied</span>
        ) : (
          <button className="btn small" onClick={() => setOpen(!open)}>
            {open ? "Cancel" : "Reply"}
          </button>
        )}
      </div>
      {open && !review.replied && (
        <div className="stack" style={{ gap: 8 }}>
          <textarea
            className="textarea"
            value={draft}
            placeholder="Write a reply. Tip: tap your assistant in the corner to draft one."
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="row">
            <button className="btn primary small" disabled={busy || draft.trim().length < 2} onClick={() => void post()}>
              Post reply
            </button>
            {error && <span className="error small">{error}</span>}
          </div>
        </div>
      )}
    </li>
  );
}

export default function Reviews() {
  const { location } = useApp();
  const [filter, setFilter] = useState<"all" | "bad" | "unreplied">("all");
  const qs = filter === "bad" ? "&max_rating=3" : filter === "unreplied" ? "&unreplied_only=true" : "";
  const { data, error, reload } = useApi<{ average_rating: number | null; reviews: ReviewRow[] }>(
    `/locations/${location.id}/reviews?period=last_30_days${qs}`
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Reviews</h1>
          <p>
            {data?.average_rating != null ? `Average ${data.average_rating.toFixed(1)}★ over 30 days` : "Last 30 days"}
          </p>
        </div>
        <div className="seg" role="group" aria-label="Filter">
          {(["all", "bad", "unreplied"] as const).map((f) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f === "bad" ? "1–3 stars" : "Not replied"}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <section className="panel">
          <ul className="list">
            {data.reviews.map((r) => (
              <ReviewCard key={r.id} review={r} locationId={location.id} onReplied={() => void reload()} />
            ))}
          </ul>
          {data.reviews.length === 0 && <p className="empty">No reviews match. Connect Google in Settings to pull them in.</p>}
        </section>
      )}
    </>
  );
}
