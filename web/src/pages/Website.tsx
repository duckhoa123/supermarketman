import { useCallback, useEffect, useRef, useState } from "react";
import SectionEditor from "../components/site/SectionEditor";
import { defaults, type FieldContext, FieldList } from "../components/site/SiteFields";
import { useApp } from "../context";
import { api, fetchBlob, type SiteContent, type SiteEditor, type SiteSection, type SiteTemplate } from "../lib/api";
import { timeAgo } from "../lib/format";

type Tab = "design" | "sections" | "settings";
const DESKTOP_WIDTH = 1280;
type Version = { id: string; created_at: string; template: string; live: boolean };

/** Loads every template font once, so the template cards show their real type. */
function useTemplateFonts(fonts: string[]) {
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
  return (
    <button className={`template-card ${on ? "on" : ""}`} onClick={onPick} aria-pressed={on}>
      <span className="template-swatch" style={{ background: t.layout.header === "transparent" || t.group === "photo" ? th.dark : th.background, color: t.group === "photo" ? "#fff" : th.text }}>
        <span className="tpl-script" style={{ fontFamily: `'${th.script_font}', cursive`, color: th.accent }}>Welcome</span>
        <span className="tpl-head" style={{ fontFamily: `'${th.heading_font}', serif` }}>Aa</span>
        <span className="tpl-dots">
          {[th.background, th.accent, th.dark].map((c, i) => <i key={i} style={{ background: c }} />)}
        </span>
      </span>
      <strong>{t.label}</strong>
      <span className="small muted">{t.best_for}</span>
    </button>
  );
}

/** Build and maintain the restaurant's website: edit a draft with a live preview, then publish. */
export default function Website() {
  const { location } = useApp();
  const base = `/locations/${location.id}/website`;
  const [site, setSite] = useState<SiteEditor | null>(null);
  const [draft, setDraft] = useState<SiteContent | null>(null);
  const [tab, setTab] = useState<Tab>("design");
  const [device, setDevice] = useState<"desktop" | "phone">("desktop");
  const [html, setHtml] = useState("");
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [showAllTemplates, setShowAllTemplates] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [boxSize, setBoxSize] = useState({ w: 0, h: 0 });
  const scroll = useRef(0);
  const timer = useRef<number | null>(null);

  const refreshPreview = useCallback(async () => {
    scroll.current = frame.current?.contentWindow?.scrollY ?? 0;
    setHtml(await (await fetchBlob(`${base}/preview`)).text());
  }, [base]);

  const load = useCallback(async () => {
    const s = await api<SiteEditor>(base);
    setSite(s);
    setDraft(s.draft);
    void refreshPreview();
    void api<Version[]>(`${base}/versions`).then(setVersions);
  }, [base, refreshPreview]);

  useEffect(() => {
    void load();
    void api<{ items: { category: string | null }[] }>(`/locations/${location.id}/inventory`).then((r) =>
      setCategories(Array.from(new Set(r.items.map((i) => i.category).filter(Boolean) as string[])).sort()));
  }, [load, location.id]);

  // The desktop preview is drawn at a real laptop width and scaled down to fit.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const cs = getComputedStyle(el);
      const w = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      const h = el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
      setBoxSize({ w: Math.min(w, DESKTOP_WIDTH), h });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [site !== null]);

  // Save the draft shortly after each change, then refresh the preview.
  function change(next: SiteContent) {
    setDraft(next);
    setSaving("saving");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        const s = await api<SiteEditor>(`${base}/draft`, { method: "PUT", body: { content: next } });
        setSite((old) => (old ? { ...old, ...s } : old));
        setSaving("saved");
        void refreshPreview();
      } catch (e) {
        setSaving("error");
        setMessage(e instanceof Error ? e.message : "Couldn't save.");
      }
    }, 700);
  }

  async function action(path: string, body: unknown = {}, done = "") {
    setMessage("");
    try {
      const s = await api<SiteEditor>(`${base}${path}`, { method: path === "/settings" ? "PUT" : "POST", body });
      setSite((old) => (old ? { ...old, ...s } : old));
      setDraft(s.draft);
      void refreshPreview();
      void api<Version[]>(`${base}/versions`).then(setVersions);
      if (done) setMessage(done);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "That didn't work.");
    }
  }

  useTemplateFonts(site ? [...site.fonts, ...site.script_fonts] : []);
  if (!site || !draft) return <p className="muted">Loading…</p>;

  const setSection = (id: string, patch: Partial<SiteSection>) =>
    change({ ...draft, sections: draft.sections.map((s) => (s.id === id ? { ...s, ...patch } : s)) });
  const move = (i: number, by: number) => {
    const list = draft.sections.slice();
    const j = i + by;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    change({ ...draft, sections: list });
  };
  const addSection = (type: string) => {
    if (!type) return;
    const id = `${type}-${Math.random().toString(16).slice(2, 10)}`;
    const spec = site.sections.find((x) => x.type === type);
    change({ ...draft, sections: [...draft.sections, { id, type, visible: true, props: spec ? defaults(spec.fields) : {} }] });
    setOpen(id);
  };
  const applyCopy = (c: { tagline: string; about: string; seo_description: string }) =>
    change({
      ...draft,
      seo: { ...draft.seo, description: draft.seo.description || c.seo_description },
      sections: draft.sections.map((s) =>
        s.type === "hero" ? { ...s, props: { ...s.props, tagline: c.tagline } }
          : s.type === "about" ? { ...s, props: { ...s.props, body: c.about } } : s),
    });
  const onUploaded = (img: { id: string; url: string }) => setSite((s) => (s ? { ...s, images: [img, ...s.images] } : s));
  const spec = (type: string) => site.sections.find((x) => x.type === type);
  const label = (type: string) => spec(type)?.label ?? type;
  const fieldCtx: FieldContext = {
    images: site.images, uploadPath: `${base}/images`, onUploaded, categories, menuItems: site.menu_items, icons: site.icons,
  };
  const current = site.templates.find((t) => t.id === draft.template);
  const setTheme = (patch: Partial<SiteContent["theme"]>) => change({ ...draft, theme: { ...draft.theme, ...patch } });
  const status = !site.is_published ? ["Not published yet", "warn"] : site.has_unpublished_changes ? ["Unpublished changes", "warn"] : ["Live", "good"];

  return (
    <div className="site-editor">
      <div className="page-head">
        <div>
          <h1>Website</h1>
          <p>
            <span className={`pill ${status[1]}`}>{status[0]}</span>{" "}
            {site.is_published ? <a href={site.url} target="_blank" rel="noreferrer">{site.url.replace(/^https?:\/\//, "")}</a> : "Edit freely: nothing is public until you publish."}
            {site.published_at && <span className="small muted"> · published {timeAgo(site.published_at)}</span>}
          </p>
        </div>
        <div className="row">
          <span className="small muted">{saving === "saving" ? "Saving…" : saving === "saved" ? "Draft saved" : saving === "error" ? "Not saved" : ""}</span>
          {site.is_published && site.has_unpublished_changes && (
            <button className="btn" onClick={() => window.confirm("Throw away your changes and go back to the live version?") && void action("/discard", {}, "Changes discarded.")}>
              Discard changes
            </button>
          )}
          <button className="btn primary" disabled={saving === "saving" || (site.is_published && !site.has_unpublished_changes)}
            onClick={() => void action("/publish", {}, "Published! Your changes are live.")}>
            {site.is_published ? "Publish changes" : "Publish website"}
          </button>
        </div>
      </div>
      {message && <p className="panel small" role="status">{message}</p>}

      <div className="site-layout">
        <aside className="panel site-panel">
          <div className="seg" role="tablist">
            {(["design", "sections", "settings"] as const).map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{{ design: "Design", sections: "Sections", settings: "Settings" }[t]}</button>
            ))}
          </div>

          {tab === "design" && (
            <div className="stack">
              <h3>Template</h3>
              <p className="small muted">
                Switching template changes the look and adds that template's sections. Your words and photos stay.
              </p>
              {(["photo", "simple"] as const).map((g) => (
                (g === "photo" || showAllTemplates) && (
                  <div key={g} className="stack" style={{ gap: 6 }}>
                    <span className="small strong">{g === "photo" ? "Photo-led" : "Simple"}</span>
                    <div className="template-grid">
                      {site.templates.filter((t) => t.group === g).map((t) => (
                        <TemplateCard key={t.id} t={t} on={draft.template === t.id}
                          onPick={() => draft.template !== t.id && void action("/template", { template: t.id })} />
                      ))}
                    </div>
                  </div>
                )
              ))}
              <button className="linkish small" style={{ alignSelf: "start" }} onClick={() => setShowAllTemplates(!showAllTemplates)}>
                {showAllTemplates ? "Hide simple templates" : "Show simple templates"}
              </button>
              {current && <p className="small muted">{current.description}</p>}
              <p className="small muted">To start over with this template's sample page,{" "}
                <button className="linkish" onClick={() => window.confirm("Replace your page with this template's starter page?") && void action("/template", { template: draft.template, start_fresh: true })}>reset to its starter page</button>.
              </p>

              <h3>Colours</h3>
              <div className="color-row">
                {([["accent", "Accent"], ["background", "Background"], ["text", "Text"], ["dark", "Dark bands"]] as const).map(([k, l]) => (
                  <label key={k} className="stack color-field" style={{ gap: 4 }}>
                    <span className="small">{l}</span>
                    <input type="color" value={draft.theme[k]} onChange={(e) => setTheme({ [k]: e.target.value })} />
                  </label>
                ))}
              </div>
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

              <h3>Header and footer</h3>
              <FieldList fields={site.layout_fields} values={draft.layout} ctx={fieldCtx}
                onChange={(v) => change({ ...draft, layout: v })} />
            </div>
          )}

          {tab === "sections" && (
            <div className="stack" style={{ gap: 8 }}>
              <p className="small muted">The page shows these from top to bottom. Click one to edit it.</p>
              {draft.sections.map((s, i) => (
                <div key={s.id} className={`section-row ${s.visible ? "" : "hidden-section"}`}>
                  <div className="section-head">
                    <button className="section-title" onClick={() => setOpen(open === s.id ? null : s.id)} aria-expanded={open === s.id}>
                      <span aria-hidden>{spec(s.type)?.icon}</span> {label(s.type)}
                    </button>
                    <button className="icon-btn" title="Move up" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                    <button className="icon-btn" title="Move down" aria-label="Move down" disabled={i === draft.sections.length - 1} onClick={() => move(i, 1)}>↓</button>
                    <button className="icon-btn" title={s.visible ? "Hide" : "Show"} aria-label={s.visible ? "Hide" : "Show"} onClick={() => setSection(s.id, { visible: !s.visible })}>
                      {s.visible ? "👁" : "🚫"}
                    </button>
                  </div>
                  {open === s.id && (
                    <div className="section-body">
                      <SectionEditor section={s} spec={spec(s.type)} onChange={(props) => setSection(s.id, { props })}
                        ctx={fieldCtx} locationId={location.id} onCopy={applyCopy} />
                      <button className="btn small danger" style={{ marginTop: 12 }}
                        onClick={() => change({ ...draft, sections: draft.sections.filter((x) => x.id !== s.id) })}>Remove section</button>
                    </div>
                  )}
                </div>
              ))}
              <select className="select" value="" onChange={(e) => addSection(e.target.value)} aria-label="Add a section">
                <option value="">+ Add a section…</option>
                {site.sections.map((s) => <option key={s.type} value={s.type}>{s.icon} {s.label}</option>)}
              </select>
            </div>
          )}

          {tab === "settings" && <Settings site={site} draft={draft} versions={versions} change={change} action={action} />}
        </aside>

        <section className="site-preview">
          <div className="spread" style={{ marginBottom: 8 }}>
            <div className="seg">
              <button className={device === "desktop" ? "on" : ""} onClick={() => setDevice("desktop")}>Desktop</button>
              <button className={device === "phone" ? "on" : ""} onClick={() => setDevice("phone")}>Phone</button>
            </div>
            <span className="small muted">Preview of your draft</span>
          </div>
          <div ref={box} className={`preview-frame ${device}`}>
            <iframe ref={frame} title="Website preview" srcDoc={html} sandbox="allow-same-origin"
              style={device === "desktop" && boxSize.w ? {
                width: DESKTOP_WIDTH, height: boxSize.h / (boxSize.w / DESKTOP_WIDTH), maxWidth: "none", flexShrink: 0,
                transform: `scale(${boxSize.w / DESKTOP_WIDTH})`, transformOrigin: "0 0",
              } : undefined}
              onLoad={() => frame.current?.contentWindow?.scrollTo(0, scroll.current)} />
          </div>
        </section>
      </div>
    </div>
  );
}

function Settings({ site, draft, versions, change, action }: {
  site: SiteEditor; draft: SiteContent; versions: Version[];
  change: (c: SiteContent) => void; action: (path: string, body?: unknown, done?: string) => Promise<void>;
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
      <h3>Search engines and sharing</h3>
      <label className="stack" style={{ gap: 4 }}>
        <span className="small">Page title</span>
        <input className="input" maxLength={70} value={draft.seo.title} onChange={(e) => change({ ...draft, seo: { ...draft.seo, title: e.target.value } })} />
      </label>
      <label className="stack" style={{ gap: 4 }}>
        <span className="small">Description (shown in Google results, up to 160 characters)</span>
        <textarea className="textarea" rows={3} maxLength={160} value={draft.seo.description} onChange={(e) => change({ ...draft, seo: { ...draft.seo, description: e.target.value } })} />
      </label>
      <h3>Social links</h3>
      {(["instagram", "facebook", "tiktok"] as const).map((k) => (
        <input key={k} className="input" placeholder={`https://${k}.com/yourrestaurant`} value={draft.links[k]}
          onChange={(e) => change({ ...draft, links: { ...draft.links, [k]: e.target.value } })} />
      ))}
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
