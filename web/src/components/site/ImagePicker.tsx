import { useRef, useState } from "react";
import { API_URL, uploadFile } from "../../lib/api";
import { prepareUpload } from "../../lib/image";

/** Choose one of the restaurant's uploaded photos, or upload a new one. */
export default function ImagePicker({ images, value, onChange, uploadPath, onUploaded, multiple = false, values = [] }: {
  images: { id: string; url: string }[];
  value?: string | null;
  values?: string[];
  multiple?: boolean;
  onChange: (next: string | null | string[]) => void;
  uploadPath: string;
  onUploaded: (img: { id: string; url: string }) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selected = multiple ? values : value ? [value] : [];

  function toggle(id: string) {
    if (multiple) onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
    else onChange(value === id ? null : id);
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      for (const f of Array.from(files)) {
        const { blob, name } = await prepareUpload(f);
        const img = await uploadFile<{ id: string; url: string }>(uploadPath, blob, name);
        onUploaded(img);
        if (!multiple) onChange(img.id);
        else selected.push(img.id);
      }
      if (multiple) onChange([...selected]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="image-grid">
        {images.map((img) => (
          <button key={img.id} type="button" className={`image-tile ${selected.includes(img.id) ? "on" : ""}`} onClick={() => toggle(img.id)}
            aria-pressed={selected.includes(img.id)}>
            <img src={`${API_URL}${img.url}`} alt="" loading="lazy" />
          </button>
        ))}
        <button type="button" className="image-tile add" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "…" : "+ Upload"}
        </button>
      </div>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" multiple={multiple} hidden onChange={(e) => void upload(e.target.files)} />
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
