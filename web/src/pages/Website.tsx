import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Canvas, { type Box, type Device, type Selection } from "../components/site/Canvas";
import {
  AddSectionModal, DesignPanel, Flyout, MenuPagePanel, PhotoModal, PhotosPanel, SectionPanel, SectionsPanel,
  SettingsPanel, useTemplateFonts, type Version, ZonePanel,
} from "../components/site/BuilderPanels";
import Ico from "../components/site/Ico";
import SeoPanel from "../components/site/SeoPanel";
import { defaults, type FieldContext } from "../components/site/SiteFields";
import { useApp } from "../context";
import { API_URL, api, fetchBlob, type SiteContent, type SiteEditor } from "../lib/api";
import { timeAgo } from "../lib/format";
import { fieldFor, newSectionId, parseTarget, readTarget, writeTarget } from "../lib/sitePath";

type Panel = "design" | "sections" | "photos" | "seo" | "settings" | "section" | "header" | "footer" | "menupage" | null;
/** Which page of the site is on the canvas: the home page, or the separate menu page. */
type Page = "home" | "menu";
type Menu = "layout" | "bg" | null;

const TIPS_KEY = "pass.siteTips";
const HISTORY = 60;

function tipsSeen(): boolean {
  try {
    return window.localStorage.getItem(TIPS_KEY) === "1";
  } catch {
    return false;
  }
}

/** Build and maintain the restaurant's website, Canva-style: edit text and photos right on the page, with a
 * floating toolbar for each section. Nothing is public until "Publish". */
export default function Website() {
  const { location } = useApp();
  const navigate = useNavigate();
  const base = `/locations/${location.id}/website`;
  const [site, setSite] = useState<SiteEditor | null>(null);
  const [draft, setDraft] = useState<SiteContent | null>(null);
  const [html, setHtml] = useState("");
  const [device, setDevice] = useState<Device>(() => (window.innerWidth < 760 ? "phone" : "desktop"));
  const [editing, setEditing] = useState(true);
  const [page, setPage] = useState<Page>("home");
  const [panel, setPanel] = useState<Panel>(null);
  const [sel, setSel] = useState<Selection>(null);
  const [menu, setMenu] = useState<Menu>(null);
  const [reveal, setReveal] = useState<{ sid: string; n: number } | null>(null);
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [toast, setToast] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [photo, setPhoto] = useState<{ target: string; multi: boolean } | null>(null);
  const [adding, setAdding] = useState<{ after: string | null } | null>(null);
  const [tips, setTips] = useState(!tipsSeen());
  const [seoTick, setSeoTick] = useState(0);
  const [, setHistoryTick] = useState(0);
  const past = useRef<SiteContent[]>([]);
  const future = useRef<SiteContent[]>([]);
  const timer = useRef<number | null>(null);
  const rerenderNext = useRef(false);
  const draftRef = useRef<SiteContent | null>(null);
  const editingRef = useRef(editing);
  const pageRef = useRef(page);
  draftRef.current = draft;
  editingRef.current = editing;
  pageRef.current = page;

  const flash = useCallback((text: string) => {
    setToast(text);
    window.setTimeout(() => setToast((t) => (t === text ? "" : t)), 3800);
  }, []);

  const refreshPreview = useCallback(async () => {
    const q = new URLSearchParams();
    if (editingRef.current) q.set("edit", "1");
    if (pageRef.current === "menu") q.set("page", "menu");
    const qs = q.toString();
    setHtml(await (await fetchBlob(`${base}/preview${qs ? `?${qs}` : ""}`)).text());
  }, [base]);

  useEffect(() => {
    void (async () => {
      const s = await api<SiteEditor>(base);
      setSite(s);
      setDraft(s.draft);
      void refreshPreview();
      void api<Version[]>(`${base}/versions`).then(setVersions);
    })();
    void api<{ items: { category: string | null }[] }>(`/locations/${location.id}/inventory`).then((r) =>
      setCategories(Array.from(new Set(r.items.map((i) => i.category).filter(Boolean) as string[])).sort()));
  }, [base, location.id, refreshPreview]);

  useEffect(() => {
    if (!site) return;
    void refreshPreview();
    if (!editing) {
      setSel(null);
      setPanel(null);
    }
    // Only when switching between editing and preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  // Back from paying for the SEO plan on Stripe.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const result = q.get("seo");
    if (!result) return;
    window.history.replaceState(null, "", window.location.pathname);
    setPanel("seo");
    const checkout = q.get("seo_session");
    if (result === "success" && checkout) {
      void api(`${base}/seo/confirm`, { method: "POST", body: { session_id: checkout } })
        .then(() => flash("Welcome to the SEO plan! 🎉 Your tools are unlocked."))
        .catch((e) => flash(e instanceof Error ? e.message : "We couldn't confirm the payment yet."))
        .finally(() => setSeoTick((n) => n + 1));
    } else if (result === "cancelled") {
      flash("No problem: nothing was charged.");
    }
  }, [base, flash]);

  useEffect(() => {
    if (site) void refreshPreview();
    // Only when switching pages.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  useTemplateFonts(site ? [...site.fonts, ...site.script_fonts] : []);

  /** Every edit goes through here: remembered for undo, saved shortly after, and the page redrawn when needed
   * (text typed on the page is already showing, so it isn't redrawn). */
  const change = useCallback((next: SiteContent, opts: { rerender?: boolean; record?: boolean } = {}) => {
    const { rerender = true, record = true } = opts;
    const prev = draftRef.current;
    if (record && prev) {
      past.current = [...past.current.slice(-HISTORY + 1), prev];
      future.current = [];
    }
    setHistoryTick((n) => n + 1);
    setDraft(next);
    draftRef.current = next;
    if (rerender) rerenderNext.current = true;
    setSaving("saving");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        const s = await api<SiteEditor>(`${base}/draft`, { method: "PUT", body: { content: draftRef.current } });
        setSite((old) => (old ? { ...old, ...s } : old));
        setSaving("saved");
        if (rerenderNext.current) {
          rerenderNext.current = false;
          void refreshPreview();
        }
      } catch (e) {
        setSaving("error");
        flash(e instanceof Error ? e.message : "Couldn't save.");
      }
    }, rerender ? 350 : 700);
  }, [base, refreshPreview, flash]);

  const action = useCallback(async (path: string, body: unknown = {}, done = "") => {
    try {
      const s = await api<SiteEditor>(`${base}${path}`, { method: path === "/settings" ? "PUT" : "POST", body });
      if (draftRef.current) {
        past.current = [...past.current.slice(-HISTORY + 1), draftRef.current];
        future.current = [];
        setHistoryTick((n) => n + 1);
      }
      setSite((old) => (old ? { ...old, ...s } : old));
      setDraft(s.draft);
      void refreshPreview();
      void api<Version[]>(`${base}/versions`).then(setVersions);
      if (done) flash(done);
    } catch (e) {
      flash(e instanceof Error ? e.message : "That didn't work.");
    }
  }, [base, refreshPreview, flash]);

  const undo = useCallback(() => {
    const prev = past.current.pop();
    if (!prev || !draftRef.current) return;
    future.current.push(draftRef.current);
    change(prev, { record: false });
  }, [change]);
  const redo = useCallback(() => {
    const next = future.current.pop();
    if (!next || !draftRef.current) return;
    past.current.push(draftRef.current);
    change(next, { record: false });
  }, [change]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
      } else if (e.key === "Escape") {
        setMenu(null);
        setSel(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  if (!site || !draft) return <div className="sb-root sb-loading"><p className="muted">Opening your website…</p></div>;

  const spec = (type: string) => site.sections.find((x) => x.type === type);
  const onUploaded = (img: { id: string; url: string }) => setSite((s) => (s ? { ...s, images: [img, ...s.images] } : s));
  const fieldCtx: FieldContext = {
    images: site.images, uploadPath: `${base}/images`, onUploaded, categories, menuItems: site.menu_items, icons: site.icons,
  };
  const status = !site.is_published ? ["Not published yet", "warn"] : site.has_unpublished_changes ? ["Unpublished changes", "warn"] : ["Live", "good"];
  const section = sel?.kind === "section" ? draft.sections.find((s) => s.id === sel.sid) : undefined;
  const sectionIndex = section ? draft.sections.indexOf(section) : -1;

  function onText(raw: string, value: string) {
    const t = parseTarget(raw);
    const f = fieldFor(site!, draftRef.current!, t);
    let v = value;
    if (f?.limit && v.length > f.limit) {
      v = v.slice(0, f.limit);
      flash(`That's a bit long, so it was shortened to ${f.limit} characters.`);
    }
    // Emptied text may fall back to a default heading, so redraw to show what will really appear.
    change(writeTarget(draftRef.current!, t, v), { rerender: !v || v !== value });
  }

  function updateSection(sid: string, patch: Partial<SiteContent["sections"][number]>) {
    change({ ...draft!, sections: draft!.sections.map((s) => (s.id === sid ? { ...s, ...patch } : s)) });
  }
  function setProp(sid: string, key: string, value: unknown) {
    const s = draft!.sections.find((x) => x.id === sid);
    if (s) updateSection(sid, { props: { ...s.props, [key]: value } });
  }
  function move(by: number) {
    if (sectionIndex < 0) return;
    const list = draft!.sections.slice();
    const j = sectionIndex + by;
    if (j < 0 || j >= list.length) return;
    [list[sectionIndex], list[j]] = [list[j], list[sectionIndex]];
    change({ ...draft!, sections: list });
    const sid = list[j].id;
    window.setTimeout(() => setReveal({ sid, n: Date.now() }), 900);
  }
  function remove() {
    if (!section) return;
    const name = spec(section.type)?.label ?? "this section";
    if (!window.confirm(`Delete “${name}”? You can undo this.`)) return;
    change({ ...draft!, sections: draft!.sections.filter((s) => s.id !== section.id) });
    setSel(null);
    setPanel(null);
    flash(`${name} deleted. Press Undo to bring it back.`);
  }
  function addSection(type: string) {
    const s = spec(type);
    const id = newSectionId(type);
    const list = draft!.sections.slice();
    const at = adding?.after ? list.findIndex((x) => x.id === adding.after) + 1 : list.length;
    list.splice(at, 0, { id, type, visible: true, props: s ? defaults(s.fields) : {} });
    change({ ...draft!, sections: list });
    setAdding(null);
    setSel({ kind: "section", sid: id });
    window.setTimeout(() => setReveal({ sid: id, n: Date.now() }), 900);
    flash(`${s?.label ?? "Section"} added. Click its text to make it yours.`);
  }
  /** Show a section of this type: un-hide it, or add it (a top banner goes first, the rest at the end). */
  /** The page switcher: show the other page, with its own options open. */
  function goTo(p: Page) {
    if (p === page) return;
    setPage(p);
    setSel(null);
    setMenu(null);
    setPanel((cur) => (p === "menu" ? (editing ? "menupage" : cur) : cur === "menupage" ? null : cur));
  }
  function ensureSection(type: string) {
    const wasAway = page !== "home";
    if (wasAway) setPage("home");
    const found = draft!.sections.find((x) => x.type === type);
    const s = spec(type);
    let sid = found?.id;
    if (found && !found.visible) {
      updateSection(found.id, { visible: true });
      flash(`${s?.label ?? "Section"} is showing again.`);
    } else if (!found) {
      sid = newSectionId(type);
      const item = { id: sid, type, visible: true, props: s ? defaults(s.fields) : {} };
      change({ ...draft!, sections: type === "hero" ? [item, ...draft!.sections] : [...draft!.sections, item] });
      flash(`${s?.label ?? "Section"} added to your page.`);
    }
    if (sid) {
      const id = sid;
      setSel({ kind: "section", sid: id });
      window.setTimeout(() => setReveal({ sid: id, n: Date.now() }), found && !wasAway ? 0 : 900);
    }
  }
  function seoFix(fix: string) {
    if (fix.startsWith("section:")) ensureSection(fix.slice(8));
    else if (fix === "publish") void action("/publish", {}, "Published! Your changes are live. 🎉");
    else if (fix === "photos") setPanel("photos");
    else if (fix === "domain") setPanel("settings");
    else if (fix === "zone:footer") openZone("footer");
    else if (fix === "restaurant" || fix === "integrations") navigate("/settings");
  }
  function pickLayer(sid: string) {
    setSel({ kind: "section", sid });
    if (page !== "home") {
      setPage("home");  // the sections are on the home page: wait for it to load, then scroll
      window.setTimeout(() => setReveal({ sid, n: Date.now() }), 900);
    } else setReveal({ sid, n: Date.now() });
  }
  function choosePhotos(ids: string[]) {
    if (!photo) return;
    const t = parseTarget(photo.target);
    change(writeTarget(draftRef.current!, t, photo.multi ? ids : ids[0] ?? null));
    setPhoto(null);
  }
  function openZone(z: "header" | "footer") {
    setSel({ kind: z });
    setPanel(z);
  }
  function select(s: Selection) {
    setSel(s);
    setMenu(null);
    // Keep the open options panel in step with what's selected.
    const options = panel === "section" || panel === "header" || panel === "footer" || panel === "menupage";
    if (s && (s.kind === "header" || s.kind === "footer") && options) setPanel(s.kind);
    else if (s?.kind === "menupage" && options) setPanel("menupage");
    else if (s?.kind === "section" && (panel === "header" || panel === "footer" || panel === "menupage")) setPanel(null);
  }
  function dismissTips() {
    setTips(false);
    try {
      window.localStorage.setItem(TIPS_KEY, "1");
    } catch {
      /* ignore */
    }
  }

  const layoutField = section ? spec(section.type)?.fields.find((f) => f.name === "layout") : undefined;
  const bgField = section ? spec(section.type)?.fields.find((f) => f.name === "bg") : undefined;
  const th = draft.theme;
  const swatch: Record<string, string> = {
    page: th.background, alt: `color-mix(in srgb, ${th.background} 90%, ${th.text})`, dark: th.dark, accent: th.accent,
  };
  const label = (s: Selection) =>
    !s ? "" : s.kind === "section"
      ? spec(draft.sections.find((x) => x.id === s.sid)?.type ?? "")?.label ?? "Section"
      : s.kind === "header" ? "Header" : s.kind === "menupage" ? "Menu page" : "Footer";
  const menuPageOn = draft.menu_page?.enabled === true;

  const toolbar = (box: Box, canvasW: number) => {
    if (box.y + box.h < 48) return null;
    const top = box.y - 50 < 6 ? Math.max(6, Math.min(box.y + 8, box.y + box.h - 50)) : box.y - 50;
    const style = { top, left: Math.min(Math.max(8, box.x + 8), Math.max(8, canvasW - 560)) };
    if (sel?.kind === "menupage") {
      return (
        <div className="sb-toolbar" style={style}>
          <span className="sb-tb-name">🍽 Menu page</span>
          <button className="sb-tb-btn" onClick={() => setPanel("menupage")}><Ico name="sliders" size={16} /> Edit menu page</button>
        </div>
      );
    }
    if (sel?.kind === "header" || sel?.kind === "footer") {
      return (
        <div className="sb-toolbar" style={style}>
          <span className="sb-tb-name">{sel.kind === "header" ? "Header" : "Footer"}</span>
          <button className="sb-tb-btn" onClick={() => setPanel(sel.kind)}><Ico name="sliders" size={16} /> Edit {sel.kind}</button>
        </div>
      );
    }
    if (!section) return null;
    const sp = spec(section.type);
    return (
      <div className="sb-toolbar" style={style} onMouseDown={(e) => e.stopPropagation()}>
        <span className="sb-tb-name"><span aria-hidden>{sp?.icon}</span> <span className="sb-tb-label">{sp?.label}</span></span>
        <button className="sb-tb-icon" title="Move up" aria-label="Move up" disabled={sectionIndex === 0} onClick={() => move(-1)}><Ico name="up" size={16} /></button>
        <button className="sb-tb-icon" title="Move down" aria-label="Move down" disabled={sectionIndex === draft.sections.length - 1} onClick={() => move(1)}><Ico name="down" size={16} /></button>
        {section.visible && layoutField && (
          <span className="sb-tb-menu">
            <button className={`sb-tb-btn ${menu === "layout" ? "on" : ""}`} onClick={() => setMenu(menu === "layout" ? null : "layout")}><Ico name="layout" size={16} /> <span className="sb-tb-label">Layout</span></button>
            {menu === "layout" && (
              <div className="sb-pop">
                {layoutField.options?.map((o) => (
                  <button key={o.value} className={section.props.layout === o.value ? "on" : ""}
                    onClick={() => { setProp(section.id, "layout", o.value); setMenu(null); }}>
                    <span className="sb-pop-check">{section.props.layout === o.value && <Ico name="check" size={14} />}</span> {o.label}
                  </button>
                ))}
              </div>
            )}
          </span>
        )}
        {section.visible && bgField && (
          <span className="sb-tb-menu">
            <button className={`sb-tb-btn ${menu === "bg" ? "on" : ""}`} onClick={() => setMenu(menu === "bg" ? null : "bg")}>
              <i className="sb-dot" style={{ background: swatch[String(section.props.bg ?? "page")] }} /> <span className="sb-tb-label">Background</span>
            </button>
            {menu === "bg" && (
              <div className="sb-pop">
                {bgField.options?.map((o) => (
                  <button key={o.value} className={section.props.bg === o.value ? "on" : ""}
                    onClick={() => { setProp(section.id, "bg", o.value); setMenu(null); }}>
                    <i className="sb-dot" style={{ background: swatch[o.value] }} /> {o.label}
                  </button>
                ))}
              </div>
            )}
          </span>
        )}
        {section.visible && (
          <button className="sb-tb-btn" onClick={() => setPanel("section")}><Ico name="sliders" size={16} /> <span className="sb-tb-label">More options</span></button>
        )}
        <button className={`sb-tb-icon ${section.visible ? "" : "wide"}`} title={section.visible ? "Hide this section" : "Show this section"}
          aria-label={section.visible ? "Hide" : "Show"} onClick={() => updateSection(section.id, { visible: !section.visible })}>
          <Ico name={section.visible ? "eyeOff" : "eye"} size={16} />
          {!section.visible && <span>Show it</span>}
        </button>
        <button className="sb-tb-icon" title="Add a section below" aria-label="Add a section below" onClick={() => setAdding({ after: section.id })}><Ico name="plus" size={16} /></button>
        <button className="sb-tb-icon danger" title="Delete this section" aria-label="Delete" onClick={remove}><Ico name="trash" size={16} /></button>
      </div>
    );
  };

  const rail: { id: Exclude<Panel, "section" | "header" | "footer" | null>; icon: string; label: string }[] = [
    { id: "design", icon: "palette", label: "Design" },
    { id: "sections", icon: "layers", label: "Sections" },
    { id: "photos", icon: "image", label: "Photos" },
    { id: "seo", icon: "search", label: "SEO" },
    { id: "settings", icon: "settings", label: "Settings" },
  ];
  const panelTitle: Record<Exclude<Panel, null>, string> = {
    design: "Design", sections: "Sections", photos: "Photos", seo: "SEO: get found on Google", settings: "Website settings",
    section: section ? `${spec(section.type)?.label ?? "Section"}: more options` : "Section",
    header: "Header", footer: "Footer", menupage: "Menu page",
  };
  const photoCurrent = photo ? readTarget(draft, parseTarget(photo.target)) : null;

  return (
    <div className="sb-root">
      <header className="sb-top">
        <button className="sb-back" onClick={() => navigate("/")} title="Back to Pass"><Ico name="back" /> <span className="sb-hide-sm">Pass</span></button>
        <div className="sb-title">
          <strong>Website</strong>
          <span className={`pill ${status[1]}`}>{status[0]}</span>
          <span className="small muted sb-save sb-hide-sm">
            {saving === "saving" ? "Saving…" : saving === "error" ? "Not saved" : site.published_at ? `Published ${timeAgo(site.published_at)}` : "Saved as a draft"}
          </span>
        </div>
        <div className="sb-center">
          <div className="seg sb-pages" role="group" aria-label="Page">
            <button className={page === "home" ? "on" : ""} onClick={() => goTo("home")} title="Your home page">Home</button>
            <button className={page === "menu" ? "on" : ""} onClick={() => goTo("menu")}
              title={menuPageOn ? "Your menu page" : "Your menu page (not on your site yet)"}>
              Menu page{!menuPageOn && <span className="sb-hide-sm muted"> · off</span>}
            </button>
          </div>
          <div className="seg sb-devices" role="group" aria-label="Screen size">
            {(["desktop", "tablet", "phone"] as const).map((d) => (
              <button key={d} className={device === d ? "on" : ""} onClick={() => setDevice(d)} title={`See it on a ${d}`} aria-label={d}>
                <Ico name={d} size={16} />
              </button>
            ))}
          </div>
        </div>
        <div className="sb-actions">
          <button className="sb-icon" title="Undo (Ctrl+Z)" aria-label="Undo" disabled={!past.current.length} onClick={undo}><Ico name="undo" /></button>
          <button className="sb-icon" title="Redo" aria-label="Redo" disabled={!future.current.length} onClick={redo}><Ico name="redo" /></button>
          <button className="btn small" onClick={() => setEditing(!editing)}>
            <Ico name={editing ? "eye" : "pencil"} size={16} /> <span className="sb-hide-sm">{editing ? "Preview" : "Back to editing"}</span>
          </button>
          {site.is_published && (
            <a className="btn small sb-hide-sm" href={page === "menu" && menuPageOn ? `${site.url}/menu` : site.url} target="_blank" rel="noreferrer"><Ico name="external" size={16} /> View live</a>
          )}
          {site.is_published && site.has_unpublished_changes && (
            <button className="btn small sb-hide-sm" onClick={() => window.confirm("Throw away your changes and go back to the live version?") && void action("/discard", {}, "Changes thrown away.")}>
              Discard
            </button>
          )}
          <button className="btn primary" disabled={saving === "saving" || (site.is_published && !site.has_unpublished_changes)}
            onClick={() => void action("/publish", {}, "Published! Your changes are live. 🎉")}>
            {site.is_published ? "Publish changes" : "Publish"}
          </button>
        </div>
      </header>

      <div className="sb-body">
        <nav className="sb-rail" aria-label="Editor tools">
          {rail.map((r) => (
            <button key={r.id} className={panel === r.id ? "on" : ""} disabled={!editing}
              onClick={() => setPanel(panel === r.id ? null : r.id)}>
              <Ico name={r.icon} size={22} />
              <span>{r.label}</span>
              {r.id === "seo" && <em className="sb-rail-badge">PRO</em>}
            </button>
          ))}
        </nav>
        <main className="sb-stage" onMouseDown={() => setMenu(null)}>
          {panel && editing && (
            <Flyout title={panelTitle[panel]} onClose={() => setPanel(null)} wide={panel === "design" || panel === "seo"}>
              {panel === "design" && <DesignPanel site={site} draft={draft} change={change} action={action} fieldCtx={fieldCtx} onZone={openZone} />}
              {panel === "sections" && (
                <SectionsPanel site={site} draft={draft} change={change} selected={sel?.kind === "section" ? sel.sid : null}
                  onPick={pickLayer} onAdd={() => setAdding({ after: null })} />
              )}
              {panel === "photos" && (
                <PhotosPanel site={site} apiBase={API_URL} base={base} uploadPath={`${base}/images`} onUploaded={onUploaded}
                  onDeleted={(id) => setSite((s) => (s ? { ...s, images: s.images.filter((i) => i.id !== id) } : s))} />
              )}
              {panel === "seo" && (
                <SeoPanel site={site} draft={draft} change={change} apiBase={API_URL} base={base} reloadKey={seoTick} flash={flash} onFix={seoFix} />
              )}
              {panel === "settings" && <SettingsPanel site={site} draft={draft} versions={versions} change={change} action={action} onSeo={() => setPanel("seo")} />}
              {panel === "section" && sel?.kind === "section" && (
                <SectionPanel site={site} draft={draft} sid={sel.sid} change={change} fieldCtx={fieldCtx} locationId={location.id} />
              )}
              {panel === "section" && sel?.kind !== "section" && <p className="small muted">Click a section on your page.</p>}
              {(panel === "header" || panel === "footer") && (
                <ZonePanel zone={panel} site={site} draft={draft} change={change} fieldCtx={fieldCtx} />
              )}
              {panel === "menupage" && (
                <MenuPagePanel site={site} draft={draft} change={change} fieldCtx={fieldCtx} onStock={() => navigate("/inventory")} />
              )}
            </Flyout>
          )}
          <Canvas html={html} device={device} editing={editing} selected={sel} reveal={reveal} onSelect={select}
            onText={onText}
            onImage={(t) => setPhoto({ target: t, multi: false })}
            onImages={(t) => setPhoto({ target: t, multi: true })}
            onAddEnd={() => setAdding({ after: null })}
            overlay={(g) => (
              <>
                {g.hover && (
                  <div className="sb-hover" style={{ left: g.hover.x, top: g.hover.y, width: g.hover.w, height: g.hover.h }}>
                    <span className="sb-tag">{label(g.hoverLabel)}</span>
                  </div>
                )}
                {g.selected && (
                  <div className="sb-selected" style={{ left: g.selected.x, top: g.selected.y, width: g.selected.w, height: g.selected.h }} />
                )}
                {g.selected && toolbar(g.selected, g.canvas.w)}
              </>
            )} />
          {tips && editing && (
            <div className="sb-tips" role="note">
              <Ico name="sparkle" />
              <span><strong>Click any text</strong> on your page to change it. <strong>Click a photo</strong> to swap it.
                Click a section for its toolbar. Nothing goes live until you press <strong>Publish</strong>.</span>
              <button className="btn small" onClick={dismissTips}>Got it</button>
            </div>
          )}
          {!editing && (
            <div className="sb-preview-note">
              {page === "menu" && !menuPageOn ? "Your menu page isn't on your site yet: turn it on to add it." : "This is how visitors will see your website."}
            </div>
          )}
        </main>
      </div>

      {toast && <div className="sb-toast" role="status">{toast}</div>}
      {photo && (
        <PhotoModal images={site.images} apiBase={API_URL} uploadPath={`${base}/images`} onUploaded={onUploaded}
          multi={photo.multi} onClose={() => setPhoto(null)} onDone={choosePhotos}
          current={Array.isArray(photoCurrent) ? (photoCurrent as string[]) : photoCurrent ? [String(photoCurrent)] : []} />
      )}
      {adding && <AddSectionModal site={site} onPick={addSection} onClose={() => setAdding(null)} />}
    </div>
  );
}
