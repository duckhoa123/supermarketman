import { useState } from "react";
import { api, type Proposal } from "../lib/api";

/** A change the assistant suggested, waiting for the owner's approval. Styled as a kitchen docket. */
export default function Docket({ proposal, locationId }: { proposal: Proposal; locationId: string }) {
  const [state, setState] = useState<"waiting" | "saving" | "done" | "dismissed">("waiting");
  const [text, setText] = useState(proposal.reply_text ?? "");
  const [error, setError] = useState("");

  async function approve() {
    setState("saving");
    setError("");
    try {
      if (proposal.kind === "review_reply") {
        await api(`/locations/${locationId}/reviews/${proposal.review_id}/reply`, {
          method: "POST",
          body: { text },
        });
      } else if (proposal.kind === "low_stock_threshold") {
        await api(`/locations/${locationId}/items/${proposal.item_id}`, {
          method: "PATCH",
          body: { low_stock_threshold: proposal.threshold },
        });
      } else {
        await api(`/locations/${locationId}/alert-rules`, {
          method: "PUT",
          body: {
            kind: proposal.rule_kind,
            channels: proposal.channels ?? ["push"],
            params: proposal.params ?? {},
            enabled: proposal.enabled ?? true,
          },
        });
      }
      setState("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
      setState("waiting");
    }
  }

  const heading = {
    review_reply: "Reply to review",
    low_stock_threshold: "Low-stock alert",
    alert_rule: "Alert setting",
  }[proposal.kind];

  return (
    <div className={`docket ${state === "done" || state === "dismissed" ? "done" : ""}`}>
      <div className="docket-head">
        <span>{heading}</span>
        <span>{state === "done" ? "Approved" : state === "dismissed" ? "Dismissed" : "Needs your OK"}</span>
      </div>
      <div className="docket-body">{proposal.label}</div>
      {proposal.kind === "review_reply" &&
        (state === "waiting" || state === "saving" ? (
          <textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} />
        ) : (
          <p className="quote">{text}</p>
        ))}
      {(state === "waiting" || state === "saving") && (
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary small" onClick={() => void approve()} disabled={state === "saving"}>
            {proposal.kind === "review_reply" ? "Post reply" : "Approve"}
          </button>
          <button className="btn small" onClick={() => setState("dismissed")}>
            Dismiss
          </button>
        </div>
      )}
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
