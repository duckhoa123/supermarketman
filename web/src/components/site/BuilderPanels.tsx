import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, uploadFile, type SiteContent, type SiteEditor, type SiteTemplate } from "../../lib/api";
import { prepareUpload } from "../../lib/image";
import SectionEditor from "./SectionEditor";
import { type FieldContext, FieldList } from "./SiteFields";
import Ico from "./Ico";

export type Change = (next: SiteContent, opts?: { rerender?: boolean }) => void;
export type Action = (path: string, body?: unknown, done?: string) => Promise<void>;
export type Version = { id: string; created_at: string; template: string; live: boolean };
type Img = { id: string; url: string };

export const SECTION_BLURBS: Record<string, string> = {
  hero: "The first thing visitors see: your name, a tagline and a big photo",
  announcement: "A thin bar for news, like new hours or a special",
  features: "3–4 short highlights with icons",
  about: "Your story, with one or two photos",
  dishes: "Cards for your best dishes, with photos and prices",
  menu: "Your full menu and prices, straight from your POS",
  occasions: "Events and parties you host, over a photo",
  reserve: "A booking form, with guest reviews beside it",
  chef: "A portrait and a few words from your chef",
  gallery: "A grid of your photos",
  reviews: "Your best Google reviews",
  visit: "Opening hours, address and a map",
  cta: "A banner asking people to order or book",
};

/** A panel that slides over the edge of the canvas, so the page keeps the whole screen until it's needed. */
export function Flyout({ title, onClose, children, wide = false }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean;
}) {
  return (
    <aside className={`sb-flyout ${wide ? "wide" : ""}`} aria-label={title}>
      <div className="sb-flyout-head">
        <h2>{title}</h2>
        <button className="sb-icon" onClick={onClose} aria-label="Close panel"><Ico name="close" /></button>
      </div>
      <div className="sb-flyout-body">{children}</div>
    </aside>
  );
}

/** Loads every template font once, so the template cards and font menus show the real type. */
export function useTemplateFonts(fonts: string[]) {
  useEffect(() => {
    if (!fonts.length || document.getElementById("site-fonts")) return;
    const link = document.createElement("link");
    link.id = "site-fonts";
    link.rel = "stylesheet";
    link.href = `https://fonts.googleapis.com/css2?${fonts.map((f) => `family=${f.replace(/ /g, "+")}`).join("&")}&display=swap`;
    document.head.appendChild(link);
  }, [fonts]);
}

function TemplateCard({ t, on, onPick }: { t: SiteTemplate; on: boolean; onPick: () => void }) {
  const th = t.theme;
  const photo = t.group === "photo";
  return (
    <button className={`template-card ${on ? "on" : ""}`} onClick={onPick} aria-pressed={on}>
      <span className="template-swatch" style={{ background: photo ? th.dark : th.background, color: photo ? "#fff" : th.text }}>
        <span className="tpl-script" style={{ fontFamily: `'${th.script_font}', cursive`, color: th.accent }}>Welcome</span>
        <span className="tpl-head" style={{ fontFamily: `'${th.heading_font}', serif` }}>Aa</span>
        <span className="tpl-dots">{[th.background, th.accent, th.dark].map((c, i) => <i key={i} style={{ background: c }} />)}</span>
      </span>
      <strong>{t.label}</strong>
      <span className="small muted">{t.best_for}</span>
      {on && <span className="tpl-on"><Ico name="check" size={14} /> In use</span>}
    </button>
  );
}

export function DesignPanel({ site, draft, change, action, fieldCtx, onZone }: {
  site: SiteEditor; draft: SiteContent; change: Change; action: Action; fieldCtx: FieldContext;
  onZone: (z: "header" | "footer") => void;
}) {
  const [simple, setSimple] = useState(false);
  const setTheme = (patch: Partial<SiteContent["theme"]>) => change({ ...draft, theme: { ...draft.theme, ...patch } });
  const current = site.templates.find((t) => t.id === draft.template);
  return (
    <div className="stack">
      <section className="stack" style={{ gap: 8 }}>
        <h3>Template</h3>
        <p className="small muted">Pick a look. Your words and photos stay; the colours, fonts and layout change.</p>
        <div className="template-grid">
          {site.templates.filter((t) => t.group === "photo" || simple).map((t) => (
            <TemplateCard key={t.id} t={t} on={draft.template === t.id}
              onPick={() => draft.template !== t.id && void action("/template", { template: t.id }, `Switched to ${t.label}.`)} />
          ))}
        </div>
        <button className="linkish small" style={{ alignSelf: "start" }} onClick={() => setSimple(!simple)}>
          {simple ? "Hide simple templates" : "Show simple templates too"}
        </button>
        {current && <p className="small muted">{current.description}</p>}
        <button className="linkish small" style={{ alignSelf: "start" }}
          onClick={() => window.confirm("Start over with this template's sample page? Your current page is replaced (you can undo).") &&
            void action("/template", { template: draft.template, start_fresh: true }, "Started over with the sample page.")}>
          Start over with the sample page
        </button>
      </section>
      <section className="stack" style={{ gap: 8 }}>
        <h3>Colours</h3>
        <div className="color-row">
          {([["accent", "Accent"], ["background", "Background"], ["text", "Text"], ["dark", "Dark areas"]] as const).map(([k, l]) => (
            <label key={k} className="stack color-field" style={{ gap: 4 }}>
              <input type="color" value={draft.theme[k]} onChange={(e) => setTheme({ [k]: e.target.value })} aria-label={l} />
              <span className="small">{l}</span>
            </label>
          ))}
        </div>
      </section>
      <section className="stack" style={{ gap: 8 }}>
        <h3>Fonts</h3>
        {([["heading_font", "Headings", site.fonts], ["body_font", "Text", site.fonts], ["script_font", "Handwritten touches", site.script_fonts]] as const).map(([k, l, list]) => (
          <label key={k} className="stack" style={{ gap: 4 }}>
            <span className="small">{l}</span>
            <select className="select" style={{ fontFamily: `'${draft.theme[k]}'` }} value={draft.theme[k]} onChange={(e) => setTheme({ [k]: e.target.value })}>
              {list.map((f) => <option key={f} style={{ fontFamily: `'${f}'` }}>{f}</option>)}
            </select>
          </label>
        ))}
        <FieldList fields={site.theme_fields} values={draft.theme as unknown as Record<string, unknown>} ctx={fieldCtx}
          onChange={(v) => setTheme(v as Partial<SiteContent["theme"]>)} />
      </section>
      <section className="stack" style={{ gap: 8 }}>
        <h3>Header and footer</h3>
        <p className="small muted">Or click the top or bottom of your page.</p>
        <div className="row">
          <button className="btn small" onClick={() => onZone("header")}>Edit header</button>
          <button className="btn small" onClick={() => onZone("footer")}>Edit footer</button>
        </div>
      </section>
    </div>
  );
}

const HEADER_FIELDS = ["header", "info_bar", "header_button", "logo", "logo_sub"];
const FOOTER_FIELDS = ["footer", "footer_text"];

export function ZonePanel({ zone, site, draft, change, fieldCtx }: {
  zone: "header" | "footer"; site: SiteEditor; draft: SiteContent; change: Change; fieldCtx: FieldContext;
}) {
  const names = zone === "header" ? HEADER_FIELDS : FOOTER_FIELDS;
  const fields = site.layout_fields.filter((f) => names.includes(f.name));
  return (
    <div className="stack">
      <p className="small muted">
        {zone === "header"
          ? "The bar at the top of every page: your name or logo, the menu links and a button."
          : "The bottom of your page: a short line about you, links, your hours and contact details."}
      </p>
      <FieldList fields={fields} values={draft.layout} ctx={fieldCtx} onChange={(v) => change({ ...draft, layout: v })} />
      {zone === "footer" && (
        <div className="stack" style={{ gap: 6 }}>
          <span className="small">Social links (shown as icons)</span>
          {(["instagram", "facebook", "tiktok"] as const).map((k) => (
            <input key={k} className="input" placeholder={`https://${k}.com/yourrestaurant`} value={draft.links[k]}
              onChange={(e) => change({ ...draft, links: { ...draft.links, [k]: e.target.value } })} />
          ))}
        </div>
      )}
    </div>
  );
}

export function SectionPanel({ site, draft, sid, change, fieldCtx, locationId }: {
  site: SiteEditor; draft: SiteContent; sid: string; change: Change; fieldCtx: FieldContext; locationId: string;
}) {
  const s = draft.sections.find((x) => x.id === sid);
  if (!s) return <p className="small muted">This section was removed.</p>;
  const spec = site.sections.find((x) => x.type === s.type);
  const applyCopy = (c: { tagline: string; about: string; seo_description: string }) =>
    change({
      ...draft,
      seo: { ...draft.seo, description: draft.seo.description || c.seo_description },
      sections: draft.sections.map((x) =>
        x.type === "hero" ? { ...x, props: { ...x.props, tagline: c.tagline } }
          : x.type === "about" ? { ...x, props: { ...x.props, body: c.about } } : x),
    });
  return (
    <SectionEditor section={s} spec={spec} ctx={fieldCtx} locationId={locationId} onCopy={applyCopy}
      onChange={(props) => change({ ...draft, sections: draft.sections.map((x) => (x.id === sid ? { ...x, props } : x)) })} />
  );
}

export function SectionsPanel({ site, draft, change, selected, onPick, onAdd }: {
  site: SiteEditor; draft: SiteContent; change: Change; selected: string | null;
  onPick: (sid: string) => void; onAdd: () => void;
}) {
  const spec = (t: string) => site.sections.find((x) => x.type === t);
  const move = (i: number, by: number) => {
    const list = draft.sections.slice();
    const j = i + by;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    change({ ...draft, sections: list });
  };
  return (
    <div className="stack" style={{ gap: 6 }}>
      <p className="small muted">Your page, top to bottom. Click one to jump to it.</p>
      <ul className="sb-layers">
        {draft.sections.map((s, i) => (
          <li key={s.id} className={`${selected === s.id ? "on" : ""} ${s.visible ? "" : "off"}`}>
            <button className="sb-layer-name" onClick={() => onPick(s.id)}>
              <span aria-hidden>{spec(s.type)?.icon}</span> {spec(s.type)?.label ?? s.type}
              {!s.visible && <span className="small muted"> (hidden)</span>}
            </button>
            <button className="sb-icon" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Ico name="up" size={16} /></button>
            <button className="sb-icon" aria-label="Move down" disabled={i === draft.sections.length - 1} onClick={() => move(i, 1)}><Ico name="down" size={16} /></button>
            <button className="sb-icon" aria-label={s.visible ? "Hide" : "Show"}
              onClick={() => change({ ...draft, sections: draft.sections.map((x) => (x.id === s.id ? { ...x, visible: !x.visible } : x)) })}>
              <Ico name={s.visible ? "eye" : "eyeOff"} size={16} />
            </button>
          </li>
        ))}
      </ul>
      <button className="btn" onClick={onAdd}><Ico name="plus" size={16} /> Add a section</button>
    </div>
  );
}

function UploadTile({ uploadPath, onUploaded, label = "Upload photos" }: {
  uploadPath: string; onUploaded: (img: Img) => void; label?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      for (const f of Array.from(files)) {
        const { blob, name } = await prepareUpload(f);
        onUploaded(await uploadFile<Img>(uploadPath, blob, name));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  return (
    <>
      <button type="button" className="sb-upload" onClick={() => input.current?.click()} disabled={busy}>
        <Ico name="plus" size={22} />
        <span>{busy ? "Uploading…" : label}</span>
      </button>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={(e) => void upload(e.target.files)} />
      {error && <p className="small error" style={{ gridColumn: "1 / -1" }}>{error}</p>}
    </>
  );
}

function thumb(apiBase: string, url: string) {
  return url.startsWith("http") ? url : `${apiBase}${url}`;
}

export function PhotosPanel({ site, apiBase, uploadPath, onUploaded, onDeleted, base }: {
  site: SiteEditor; apiBase: string; uploadPath: string; onUploaded: (img: Img) => void;
  onDeleted: (id: string) => void; base: string;
}) {
  const [error, setError] = useState("");
  async function remove(id: string) {
    setError("");
    try {
      await api(`${base}/images/${id}`, { method: "DELETE" });
      onDeleted(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't delete it.");
    }
  }
  return (
    <div className="stack">
      <p className="small muted">All your photos. To put one on the page, click a photo spot on your page.</p>
      <div className="sb-photo-grid">
        <UploadTile uploadPath={uploadPath} onUploaded={onUploaded} />
        {site.images.map((img) => (
          <figure key={img.id} className="sb-photo">
            <img src={thumb(apiBase, img.url)} alt="" loading="lazy" />
            <button className="sb-photo-del" aria-label="Delete photo" onClick={() => void remove(img.id)}><Ico name="trash" size={14} /></button>
          </figure>
        ))}
      </div>
      {error && <p className="small error">{error}</p>}
    </div>
  );
}

export function SettingsPanel({ site, draft, versions, change, action }: {
  site: SiteEditor; draft: SiteContent; versions: Version[]; change: Change; action: Action;
}) {
  const [slug, setSlug] = useState(site.slug);
  const [domain, setDomain] = useState(site.custom_domain ?? "");
  const apiHost = site.url.startsWith("http") && !site.custom_domain ? site.url.split("/sites/")[0] : "";
  return (
    <div className="stack">
      <h3>Web address</h3>
      <div className="row">
        <span className="small muted">{apiHost || "…"}/sites/</span>
        <input className="input" style={{ flex: 1, minWidth: 120 }} value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} />
        <button className="btn small" disabled={slug === site.slug} onClick={() => void action("/settings", { slug }, "Web address saved.")}>Save</button>
      </div>
      <h3>Your own domain (optional)</h3>
      <div className="row">
        <input className="input" style={{ flex: 1 }} placeholder="www.yourrestaurant.ca" value={domain} onChange={(e) => setDomain(e.target.value)} />
        <button className="btn small" onClick={() => void action("/settings", domain ? { custom_domain: domain } : { clear_domain: true }, "Domain saved.")}>Save</button>
      </div>
      <p className="small muted">
        At your domain registrar, point the domain at this app with a CNAME record, and add the same domain to the
        api service in Railway (Settings → Networking → Custom domain). Ask us if you need a hand.
      </p>
      <h3>Google and sharing</h3>
      <label className="stack" style={{ gap: 4 }}>
        <span className="small">Page title</span>
        <input className="input" maxLength={70} value={draft.seo.title} onChange={(e) => change({ ...draft, seo: { ...draft.seo, title: e.target.value } }, { rerender: false })} />
      </label>
      <label className="stack" style={{ gap: 4 }}>
        <span className="small">Description (shown in Google results, up to 160 characters)</span>
        <textarea className="textarea" rows={3} maxLength={160} value={draft.seo.description}
          onChange={(e) => change({ ...draft, seo: { ...draft.seo, description: e.target.value } }, { rerender: false })} />
      </label>
      <h3>Published versions</h3>
      {versions.length === 0 && <p className="small muted">Nothing published yet.</p>}
      <ul className="list small">
        {versions.map((v) => (
          <li key={v.id} className="spread">
            <span>{new Date(v.created_at).toLocaleString()} · {v.template}{v.live && <span className="pill good" style={{ marginLeft: 6 }}>live</span>}</span>
            {!v.live && <button className="btn small" onClick={() => void action(`/versions/${v.id}/restore`, {}, "Loaded into your draft. Check it, then publish.")}>Restore</button>}
          </li>
        ))}
      </ul>
      {site.is_published && (
        <button className="btn small danger" style={{ alignSelf: "start" }}
          onClick={() => window.confirm("Take the website offline? Visitors will see a 'not published' page.") && void action("/unpublish", {}, "The website is offline.")}>
          Take website offline
        </button>
      )}
    </div>
  );
}

function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="sb-modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sb-modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="sb-flyout-head">
          <h2>{title}</h2>
          <button className="sb-icon" onClick={onClose} aria-label="Close"><Ico name="close" /></button>
        </div>
        <div className="sb-modal-body">{children}</div>
        {footer && <div className="sb-modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** Choose a photo for one spot (or several, for the gallery), or upload new ones. */
export function PhotoModal({ images, apiBase, uploadPath, onUploaded, multi, current, onDone, onClose }: {
  images: Img[]; apiBase: string; uploadPath: string; onUploaded: (img: Img) => void;
  multi: boolean; current: string[]; onDone: (ids: string[]) => void; onClose: () => void;
}) {
  const [picked, setPicked] = useState<string[]>(current);
  const toggle = (id: string) => {
    if (!multi) return onDone([id]);
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  };
  return (
    <Modal title={multi ? "Choose photos for your gallery" : "Choose a photo"} onClose={onClose}
      footer={
        <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
          {current.length > 0 && !multi ? (
            <button className="btn small danger" onClick={() => onDone([])}>Remove photo</button>
          ) : <span className="small muted">{multi ? `${picked.length} selected · tap to add or remove` : "Tap a photo to use it"}</span>}
          {multi && <button className="btn primary" onClick={() => onDone(picked)}>Done</button>}
        </div>
      }>
      <div className="sb-photo-grid big">
        <UploadTile uploadPath={uploadPath} label="Upload from your device"
          onUploaded={(img) => { onUploaded(img); if (multi) setPicked((p) => [...p, img.id]); else onDone([img.id]); }} />
        {images.map((img) => {
          const n = picked.indexOf(img.id);
          return (
            <button key={img.id} className={`sb-photo pick ${n >= 0 ? "on" : ""}`} onClick={() => toggle(img.id)}>
              <img src={thumb(apiBase, img.url)} alt="" loading="lazy" />
              {n >= 0 && <span className="sb-photo-n">{multi ? n + 1 : <Ico name="check" size={14} />}</span>}
            </button>
          );
        })}
      </div>
      {images.length === 0 && <p className="small muted">No photos yet. Upload some from your phone or computer.</p>}
    </Modal>
  );
}

export function AddSectionModal({ site, onPick, onClose }: { site: SiteEditor; onPick: (type: string) => void; onClose: () => void }) {
  return (
    <Modal title="Add a section" onClose={onClose}>
      <div className="sb-add-grid">
        {site.sections.map((s) => (
          <button key={s.type} className="sb-add-card" onClick={() => onPick(s.type)}>
            <span className="sb-add-ic" aria-hidden>{s.icon}</span>
            <strong>{s.label}</strong>
            <span className="small muted">{SECTION_BLURBS[s.type]}</span>
          </button>
        ))}
      </div>
    </Modal>
  );
}
