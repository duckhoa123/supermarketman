import { useState } from "react";
import { api, type SiteEditor, type SiteSection } from "../../lib/api";
import { type FieldContext, FieldList } from "./SiteFields";

const LOOK = ["layout", "bg"];

/** The fields for one section of the page, built from the server's declaration of that section type. */
export default function SectionEditor({ section, spec, onChange, ctx, locationId, onCopy }: {
  section: SiteSection;
  spec: SiteEditor["sections"][number] | undefined;
  onChange: (props: Record<string, unknown>) => void;
  ctx: FieldContext;
  locationId: string;
  onCopy: (copy: { tagline: string; about: string; seo_description: string }) => void;
}) {
  const [writing, setWriting] = useState(false);
  if (!spec) return null;
  const look = spec.fields.filter((f) => LOOK.includes(f.name));

  async function writeForMe() {
    setWriting(true);
    try {
      onCopy(await api(`/locations/${locationId}/website/write-copy`, { method: "POST", body: {} }));
    } finally {
      setWriting(false);
    }
  }

  return (
    <div className="stack">
      {spec.help && <p className="small muted">{spec.help}</p>}
      {spec.ai && (
        <button type="button" className="btn small" style={{ alignSelf: "start" }} disabled={writing} onClick={() => void writeForMe()}>
          {writing ? "Writing…" : "✨ Write the words for me"}
        </button>
      )}
      <FieldList fields={spec.fields} values={section.props} onChange={onChange} ctx={ctx} skip={LOOK} />
      {look.length > 0 && (
        <div className="look-box">
          <span className="small strong">Look</span>
          <FieldList fields={look} values={section.props} onChange={onChange} ctx={ctx} />
        </div>
      )}
    </div>
  );
}
