import { useState } from "react";
import { api, type SiteSection } from "../../lib/api";
import ImagePicker from "./ImagePicker";

type Img = { id: string; url: string };

function Text({ label, value, onChange, long = false, max }: {
  label: string; value: unknown; onChange: (v: string) => void; long?: boolean; max?: number;
}) {
  return (
    <label className="stack" style={{ gap: 4 }}>
      <span className="small">{label}</span>
      {long ? (
        <textarea className="textarea" rows={5} maxLength={max} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input className="input" maxLength={max} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  );
}

function Check({ label, value, onChange }: { label: string; value: unknown; onChange: (v: boolean) => void }) {
  return (
    <label className="check small">
      <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/** The fields for one section of the page. */
export default function SectionEditor({ section, onChange, images, uploadPath, onUploaded, categories, locationId, onCopy }: {
  section: SiteSection;
  onChange: (props: Record<string, unknown>) => void;
  images: Img[];
  uploadPath: string;
  onUploaded: (img: Img) => void;
  categories: string[];
  locationId: string;
  onCopy: (copy: { tagline: string; about: string; seo_description: string }) => void;
}) {
  const p = section.props;
  const set = (key: string) => (v: unknown) => onChange({ ...p, [key]: v });
  const [writing, setWriting] = useState(false);

  async function writeForMe() {
    setWriting(true);
    try {
      onCopy(await api(`/locations/${locationId}/website/write-copy`, { method: "POST", body: {} }));
    } finally {
      setWriting(false);
    }
  }
  const aiButton = (
    <button type="button" className="btn small" disabled={writing} onClick={() => void writeForMe()}>
      {writing ? "Writing…" : "✨ Write it for me"}
    </button>
  );

  switch (section.type) {
    case "hero":
      return (
        <div className="stack">
          <Text label="Headline" value={p.title} onChange={set("title")} max={80} />
          <Text label="Tagline" value={p.tagline} onChange={set("tagline")} max={160} />
          {aiButton}
          <span className="small">Photo (optional)</span>
          <ImagePicker images={images} value={p.image as string | null} onChange={(v) => set("image")(v)} uploadPath={uploadPath} onUploaded={onUploaded} />
          <Check label="“Order online” button" value={p.order_button} onChange={set("order_button")} />
          <Check label="“Book a table” button" value={p.booking_button} onChange={set("booking_button")} />
        </div>
      );
    case "announcement":
      return <Text label="Message (e.g. “Now open for brunch on Sundays”)" value={p.text} onChange={set("text")} max={160} />;
    case "about":
      return (
        <div className="stack">
          <Text label="Heading" value={p.heading} onChange={set("heading")} max={80} />
          <Text label="Text (leave a blank line between paragraphs)" value={p.body} onChange={set("body")} long max={2000} />
          {aiButton}
          <span className="small">Photo (optional)</span>
          <ImagePicker images={images} value={p.image as string | null} onChange={(v) => set("image")(v)} uploadPath={uploadPath} onUploaded={onUploaded} />
        </div>
      );
    case "menu": {
      const chosen = (p.categories as string[]) ?? [];
      return (
        <div className="stack">
          <Text label="Heading" value={p.heading} onChange={set("heading")} max={80} />
          <p className="small muted">Dishes and prices come from your menu automatically and stay up to date.</p>
          <span className="small">Categories to show (none ticked = all)</span>
          <div className="row">
            {categories.map((c) => (
              <label key={c} className="check small">
                <input type="checkbox" checked={chosen.includes(c)}
                  onChange={(e) => set("categories")(e.target.checked ? [...chosen, c] : chosen.filter((x) => x !== c))} />
                {c}
              </label>
            ))}
          </div>
          <Check label="Show prices" value={p.show_prices} onChange={set("show_prices")} />
          <Check label="Show descriptions" value={p.show_descriptions} onChange={set("show_descriptions")} />
          <Check label="Show dietary labels (V, VG, GF)" value={p.show_dietary} onChange={set("show_dietary")} />
        </div>
      );
    }
    case "gallery":
      return (
        <div className="stack">
          <Text label="Heading" value={p.heading} onChange={set("heading")} max={80} />
          <span className="small">Photos (tap to add or remove, up to 12)</span>
          <ImagePicker images={images} multiple values={(p.images as string[]) ?? []} onChange={(v) => set("images")(v)}
            uploadPath={uploadPath} onUploaded={onUploaded} />
        </div>
      );
    case "reviews":
      return (
        <div className="stack">
          <Text label="Heading" value={p.heading} onChange={set("heading")} max={80} />
          <p className="small muted">Your latest Google reviews, shown automatically.</p>
          <label className="row small">Only reviews with at least
            <select className="select" style={{ width: "auto" }} value={Number(p.min_rating ?? 4)} onChange={(e) => set("min_rating")(Number(e.target.value))}>
              {[5, 4, 3].map((n) => <option key={n} value={n}>{n} stars</option>)}
            </select>
          </label>
          <label className="row small">Show up to
            <input className="input" style={{ width: 70 }} type="number" min={1} max={12} value={Number(p.count ?? 6)} onChange={(e) => set("count")(Number(e.target.value))} />
            reviews
          </label>
        </div>
      );
    case "visit":
      return (
        <div className="stack">
          <Text label="Heading" value={p.heading} onChange={set("heading")} max={80} />
          <p className="small muted">Hours, address and phone come from Settings → Customer app and delivery.</p>
          <Check label="Show a map" value={p.show_map} onChange={set("show_map")} />
        </div>
      );
    case "cta":
      return (
        <div className="stack">
          <Text label="Heading" value={p.heading} onChange={set("heading")} max={80} />
          <Text label="Text" value={p.text} onChange={set("text")} max={300} />
          <label className="row small">Button
            <select className="select" style={{ width: "auto" }} value={String(p.button ?? "order")} onChange={(e) => set("button")(e.target.value)}>
              <option value="order">Order online</option>
              <option value="book">Book a table</option>
              <option value="call">Call us</option>
            </select>
          </label>
        </div>
      );
    default:
      return null;
  }
}
