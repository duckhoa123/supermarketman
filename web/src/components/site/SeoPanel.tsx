import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type SeoCheck, type SeoOverview, type SiteContent, type SiteEditor, type SiteSeo } from "../../lib/api";
import { timeAgo } from "../../lib/format";
import { thumb, type Change } from "./BuilderPanels";
import Ico from "./Ico";

type Tool = "google" | "keywords" | "alt" | "share" | "business" | "search" | "plan";

const TOOLS: { id: Exclude<Tool, "plan">; icon: string; title: string; text: string; pro: boolean }[] = [
  { id: "google", icon: "search", title: "Google preview", text: "Your title and description in search results", pro: false },
  { id: "keywords", icon: "tag", title: "Search phrases", text: "Pick what you want to be found for", pro: true },
  { id: "business", icon: "store", title: "Business details", text: "Address, hours, menu and prices for Google", pro: true },
  { id: "alt", icon: "image", title: "Photo descriptions", text: "Show up in Google Images", pro: true },
  { id: "share", icon: "share", title: "Share picture", text: "When your link is shared on social apps", pro: true },
  { id: "search", icon: "globe", title: "Search engines", text: "Search Console, Bing, sitemap and Analytics", pro: true },
];
const FIX_TOOL: Record<string, Tool> = { google: "google", keywords: "keywords", alt: "alt", share: "share", search: "search", upgrade: "plan" };
const PRO_TOOLS = new Set<Tool>(TOOLS.filter((t) => t.pro).map((t) => t.id));

type Suggestion = { title: string; description: string; keywords: string[]; ai: boolean };

function message(e: unknown) {
  return e instanceof Error ? e.message : "That didn't work.";
}

/** Paste the whole <meta …> tag or just its code: keep the code. */
function verifyCode(v: string) {
  const m = v.match(/content\s*=\s*["']([^"']+)["']/i);
  return (m ? m[1] : v).trim();
}

function addressText(a: unknown): string {
  if (typeof a === "string") return a;
  const x = a as Record<string, string>;
  return [x.streetAddress, x.addressLocality, [x.addressRegion, x.postalCode].filter(Boolean).join(" ")].filter(Boolean).join(", ");
}

function ScoreRing({ score }: { score: number }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  const tone = score >= 80 ? "good" : score >= 55 ? "ok" : "low";
  return (
    <svg className={`seo-ring ${tone}`} width="84" height="84" viewBox="0 0 84 84" role="img" aria-label={`SEO score ${score} out of 100`}>
      <circle cx="42" cy="42" r={r} className="track" />
      <circle cx="42" cy="42" r={r} className="bar" strokeDasharray={c} strokeDashoffset={c * (1 - score / 100)} />
      <text x="42" y="47" textAnchor="middle">{score}</text>
    </svg>
  );
}

function Counter({ n, min, max }: { n: number; min: number; max: number }) {
  const tone = n === 0 ? "low" : n < min || n > max ? "ok" : "good";
  return <span className={`seo-count ${tone}`}>{n} / {max}</span>;
}

function CopyRow({ label, value, flash }: { label: string; value: string; flash: (t: string) => void }) {
  return (
    <div className="seo-copy">
      <span className="small muted">{label}</span>
      <div className="seo-copy-row">
        <code title={value}>{value}</code>
        <button className="sb-icon" aria-label={`Copy ${label}`} title="Copy"
          onClick={() => void navigator.clipboard?.writeText(value).then(() => flash("Copied."), () => flash(value))}>
          <Ico name="copy" size={16} />
        </button>
        <a className="sb-icon" href={value} target="_blank" rel="noreferrer" aria-label={`Open ${label}`} title="Open"><Ico name="external" size={16} /></a>
      </div>
    </div>
  );
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return <li className="seo-step"><span>{n}</span><div>{children}</div></li>;
}

/** SEO: a health check with one-click fixes, and the tools to get found on Google. The free part is the check and
 * the Google preview; the SEO plan (a paid upgrade) unlocks the rest. */
export default function SeoPanel({ site, draft, change, apiBase, base, reloadKey, flash, onFix }: {
  site: SiteEditor; draft: SiteContent; change: Change; apiBase: string; base: string; reloadKey: number;
  flash: (t: string) => void; onFix: (fix: string) => void;
}) {
  const [data, setData] = useState<SeoOverview | null>(null);
  const [error, setError] = useState("");
  const [tool, setTool] = useState<Tool | null>(null);
  const [busy, setBusy] = useState("");
  const [tip, setTip] = useState<Suggestion | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [phrase, setPhrase] = useState("");
  const tipRef = useRef<HTMLDivElement>(null);
  useEffect(() => tipRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), [tip]);

  const load = useCallback(async () => {
    try {
      setData(await api<SeoOverview>(`${base}/seo`));
      setError("");
    } catch (e) {
      setError(message(e));
    }
  }, [base]);
  // The check reads the saved draft, so it runs again shortly after each save.
  useEffect(() => {
    const t = window.setTimeout(() => void load(), 450);
    return () => window.clearTimeout(t);
  }, [load, site.draft, site.published_at, reloadKey]);

  const seo = draft.seo;
  const setSeo = (patch: Partial<SiteSeo>) => change({ ...draft, seo: { ...seo, ...patch } }, { rerender: false });
  const pro = !!data?.plan.active;

  const open = (t: Tool) => setTool(PRO_TOOLS.has(t) && !pro ? "plan" : t);
  function fix(c: SeoCheck) {
    if (!c.fix) return;
    const t = FIX_TOOL[c.fix];
    if (t) open(t);
    else onFix(c.fix);
  }

  async function upgrade() {
    setBusy("upgrade");
    try {
      const r = await api<{ status: string; checkout_url?: string }>(`${base}/seo/upgrade`, { method: "POST" });
      if (r.checkout_url) {
        window.location.href = r.checkout_url;
        return;
      }
      flash("The SEO plan is on. 🎉 Google now gets your business details.");
      await load();
      setTool(null);
    } catch (e) {
      flash(message(e));
    } finally {
      setBusy("");
    }
  }
  async function cancel() {
    if (!window.confirm("Stop the SEO plan? Google stops getting your business details and the tools lock.")) return;
    setBusy("cancel");
    try {
      await api(`${base}/seo/cancel`, { method: "POST" });
      flash("The SEO plan is stopped.");
      await load();
    } catch (e) {
      flash(message(e));
    } finally {
      setBusy("");
    }
  }
  async function suggest() {
    setBusy("suggest");
    try {
      setTip(await api<Suggestion>(`${base}/seo/suggest`, { method: "POST" }));
    } catch (e) {
      flash(message(e));
    } finally {
      setBusy("");
    }
  }
  async function describe(ids: string[]) {
    setBusy("describe");
    try {
      const r = await api<{ alts: Record<string, string> }>(`${base}/seo/describe-photos`, { method: "POST", body: { ids } });
      setSeo({ alts: { ...seo.alts, ...r.alts } });
      flash(`${Object.keys(r.alts).length} photo${Object.keys(r.alts).length === 1 ? "" : "s"} described. Check them and edit any you like.`);
    } catch (e) {
      flash(message(e));
    } finally {
      setBusy("");
    }
  }
  async function notify() {
    setBusy("notify");
    try {
      const r = await api<{ ok: boolean; message: string }>(`${base}/seo/notify`, { method: "POST" });
      flash(r.message);
      await load();
    } catch (e) {
      flash(message(e));
    } finally {
      setBusy("");
    }
  }
  function addPhrases(words: string[]) {
    const list = [...seo.keywords];
    for (const w of words.map((x) => x.trim().toLowerCase().replace(/\s+/g, " ")).filter(Boolean)) {
      if (!list.includes(w) && list.length < 10) list.push(w);
    }
    setSeo({ keywords: list });
  }

  if (!data) return error ? <p className="small error">{error}</p> : <p className="small muted">Checking your website…</p>;

  const pageUrl = data.urls.page;
  const host = pageUrl.replace(/^https?:\/\//, "");
  const shownTitle = seo.title || data.name;
  const shareId = seo.share_image ?? data.share_image;
  const back = (
    <button className="seo-back" onClick={() => { setTool(null); setTip(null); }}><Ico name="back" size={16} /> All SEO tools</button>
  );

  // --- Upgrade / plan ---------------------------------------------------------------------------------------------
  const upsell = (
    <div className="seo-upsell">
      <div className="seo-upsell-head">
        <Ico name="sparkle" size={22} />
        <div>
          <h3>Get found on Google</h3>
          <p>Let us do your SEO: everything below, set up for you and kept up to date.</p>
        </div>
      </div>
      <ul className="seo-features">
        {data.plan.features.map((f) => (
          <li key={f.id}><Ico name="check" size={16} /><div><strong>{f.title}</strong><span>{f.text}</span></div></li>
        ))}
      </ul>
      <div className="seo-price"><strong>{data.plan.price.replace("/month", "")}</strong><span>/ month per website · cancel anytime</span></div>
      <button className="btn primary seo-cta" disabled={busy === "upgrade"} onClick={() => void upgrade()}>
        {busy === "upgrade" ? "One moment…" : data.plan.status === "canceling" ? "Keep my SEO plan" : "Upgrade to the SEO plan"}
      </button>
      {data.plan.billing === "test" && <p className="small muted">Test mode: no card is needed and nothing is charged.</p>}
      {data.plan.billing === "stripe" && <p className="small muted">You'll pay securely on Stripe and come straight back here.</p>}
    </div>
  );

  if (tool === "plan") {
    return (
      <div className="seo">
        {back}
        {pro ? (
          <div className="stack">
            <h3>Your SEO plan</h3>
            <p className="small">
              <span className="pill good">On</span> {data.plan.price}
              {data.plan.since && <> · since {new Date(data.plan.since).toLocaleDateString()}</>}
              {data.plan.billing === "test" && <> · test mode, nothing is charged</>}
            </p>
            {data.plan.status === "canceling" && data.plan.ends_at && (
              <p className="small">Stops on {new Date(data.plan.ends_at).toLocaleDateString()}.
                <button className="btn small" style={{ marginLeft: 8 }} onClick={() => void upgrade()}>Keep it</button></p>
            )}
            {data.plan.status !== "canceling" && (
              <button className="btn small danger" style={{ alignSelf: "start" }} disabled={busy === "cancel"} onClick={() => void cancel()}>Stop the SEO plan</button>
            )}
          </div>
        ) : upsell}
      </div>
    );
  }

  // --- Tools -----------------------------------------------------------------------------------------------------
  if (tool === "google") {
    return (
      <div className="seo">
        {back}
        <p className="small muted">This is roughly how your restaurant shows on Google. Change the words below.</p>
        <div className="seo-serp">
          <div className="seo-serp-site">
            <span className="seo-fav">{data.name.slice(0, 1)}</span>
            <div><strong>{data.name}</strong><span>{host.replace(/\//g, " › ")}</span></div>
          </div>
          <h4>{shownTitle.length > 62 ? `${shownTitle.slice(0, 60)}…` : shownTitle}</h4>
          <p>{seo.description || <em>No description yet: Google will pick some text from your page.</em>}</p>
        </div>
        <label className="stack seo-field">
          <span className="spread"><span className="small"><strong>Page title</strong> — the blue link</span><Counter n={seo.title.length} min={30} max={60} /></span>
          <input className="input" maxLength={70} value={seo.title} placeholder={data.name} onChange={(e) => setSeo({ title: e.target.value })} />
        </label>
        <label className="stack seo-field">
          <span className="spread"><span className="small"><strong>Description</strong> — the text under it</span><Counter n={seo.description.length} min={120} max={160} /></span>
          <textarea className="textarea" rows={3} maxLength={160} value={seo.description} onChange={(e) => setSeo({ description: e.target.value })} />
        </label>
        <label className="stack seo-field">
          <span className="small"><strong>Neighbourhood</strong> (optional) — people search “ramen near Kitsilano”</span>
          <input className="input" maxLength={80} value={seo.area} placeholder={data.city || "e.g. Kitsilano"} onChange={(e) => setSeo({ area: e.target.value })} />
        </label>
        <p className="small muted">Tip: say what you serve and where, e.g. “{data.name} | {data.cuisine || "Italian"} in {seo.area || data.city || "Vancouver"}”.</p>
        {pro ? (
          <button className="btn seo-ai" disabled={busy === "suggest"} onClick={() => void suggest()}>
            <Ico name="sparkle" size={16} /> {busy === "suggest" ? "Writing…" : "Write them for me"}
          </button>
        ) : (
          <button className="btn seo-ai" onClick={() => setTool("plan")}><Ico name="lock" size={16} /> Write them for me (SEO plan)</button>
        )}
        {tip && (
          <div className="seo-tip" ref={tipRef}>
            <p className="small muted">{tip.ai ? "Suggested for you" : "A starting point"}</p>
            <p><strong>{tip.title}</strong></p>
            <p className="small">{tip.description}</p>
            <div className="row">
              <button className="btn small primary" onClick={() => { setSeo({ title: tip.title, description: tip.description }); setTip(null); flash("Title and description updated."); }}>Use both</button>
              <button className="btn small" onClick={() => { setSeo({ title: tip.title }); }}>Title only</button>
              <button className="btn small" onClick={() => setTip(null)}>No thanks</button>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (tool === "keywords") {
    const cov = new Map(data.keywords.map((k) => [k.phrase, k]));
    return (
      <div className="seo">
        {back}
        <p className="small muted">The words guests type into Google. Pick 2–5, then use them in your title, headline and description.</p>
        <form className="row" onSubmit={(e) => { e.preventDefault(); addPhrases([phrase]); setPhrase(""); }}>
          <input className="input" style={{ flex: 1 }} maxLength={60} placeholder={`e.g. ${(data.cuisine || "pizza").toLowerCase()} ${(seo.area || data.city || "vancouver").toLowerCase()}`}
            value={phrase} onChange={(e) => setPhrase(e.target.value)} />
          <button className="btn" disabled={!phrase.trim() || seo.keywords.length >= 10}>Add</button>
        </form>
        <button className="btn seo-ai" disabled={busy === "suggest"} onClick={() => void suggest()}>
          <Ico name="sparkle" size={16} /> {busy === "suggest" ? "Thinking…" : "Suggest phrases for my area"}
        </button>
        {tip && tip.keywords.length > 0 && (
          <div className="seo-chips">
            {tip.keywords.filter((k) => !seo.keywords.includes(k)).map((k) => (
              <button key={k} className="seo-chip add" onClick={() => addPhrases([k])}><Ico name="plus" size={14} /> {k}</button>
            ))}
          </div>
        )}
        {seo.keywords.length === 0 && <p className="small muted">No phrases yet.</p>}
        <ul className="seo-kw">
          {seo.keywords.map((k) => {
            const c = cov.get(k);
            return (
              <li key={k}>
                <div className="spread">
                  <strong>{k}</strong>
                  <button className="sb-icon" aria-label={`Remove ${k}`} onClick={() => setSeo({ keywords: seo.keywords.filter((x) => x !== k) })}><Ico name="close" size={14} /></button>
                </div>
                {c ? (
                  <div className="seo-where">
                    {(["title", "headline", "description", "page"] as const).map((w) => (
                      <span key={w} className={c[w] ? "yes" : "no"}>{c[w] ? "✓" : "✗"} {w === "page" ? "on the page" : w}</span>
                    ))}
                  </div>
                ) : <span className="small muted">Checking…</span>}
              </li>
            );
          })}
        </ul>
        {seo.keywords.length > 0 && (
          <button className="btn small" style={{ alignSelf: "start" }} onClick={() => setTool("google")}>Edit title and description</button>
        )}
      </div>
    );
  }

  if (tool === "alt") {
    const missing = data.photos.filter((p) => p.needs_alt && !seo.alts[p.id]);
    return (
      <div className="seo">
        {back}
        <p className="small muted">Short descriptions help your photos show in Google Images, and help people who use screen readers.</p>
        {data.photos.length === 0 && <p className="small muted">No photos on your page yet.</p>}
        {data.photos.length > 0 && (
          <button className="btn seo-ai" disabled={busy === "describe"}
            onClick={() => void describe((missing.length ? missing : data.photos).map((p) => p.id).slice(0, 6))}>
            <Ico name="sparkle" size={16} /> {busy === "describe" ? "Looking at your photos…" : missing.length ? `Describe ${Math.min(6, missing.length)} photo${missing.length === 1 ? "" : "s"} for me` : "Describe them again"}
          </button>
        )}
        <ul className="seo-photos">
          {data.photos.map((p) => (
            <li key={p.id}>
              <img src={thumb(apiBase, p.url)} alt="" />
              <div className="stack" style={{ gap: 4, flex: 1, minWidth: 0 }}>
                <span className="small muted">{p.where}{p.size && p.size > 1_000_000 ? ` · ${(p.size / 1e6).toFixed(1)} MB, large` : ""}</span>
                <input className="input" maxLength={125} value={seo.alts[p.id] ?? ""} placeholder={p.auto_alt || "Describe this photo"}
                  onChange={(e) => {
                    const alts = { ...seo.alts };
                    if (e.target.value) alts[p.id] = e.target.value;
                    else delete alts[p.id];
                    setSeo({ alts });
                  }} />
              </div>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (tool === "share") {
    const img = shareId ? site.images.find((i) => i.id === shareId) : undefined;
    return (
      <div className="seo">
        {back}
        <p className="small muted">What people see when your link is shared on Facebook, WhatsApp, iMessage or X.</p>
        <div className="seo-card">
          {img ? <img src={thumb(apiBase, img.url)} alt="" /> : <div className="seo-card-empty"><Ico name="image" size={28} /></div>}
          <div>
            <span>{host.split("/")[0].toUpperCase()}</span>
            <strong>{shownTitle}</strong>
            <p>{seo.description}</p>
          </div>
        </div>
        <h3>Choose the picture</h3>
        <div className="seo-pick">
          <button className={`seo-pick-auto ${!seo.share_image ? "on" : ""}`} onClick={() => setSeo({ share_image: null })}>
            Automatic<span>your top photo</span>
          </button>
          {site.images.map((i) => (
            <button key={i.id} className={seo.share_image === i.id ? "on" : ""} onClick={() => setSeo({ share_image: i.id })} aria-label="Use this photo">
              <img src={thumb(apiBase, i.url)} alt="" />
            </button>
          ))}
        </div>
        {site.images.length === 0 && <button className="btn small" onClick={() => onFix("photos")}>Upload photos</button>}
      </div>
    );
  }

  if (tool === "business") {
    const sd = data.structured_data as Record<string, unknown> & { hasMenu?: { hasMenuSection?: { hasMenuItem?: unknown[] }[] } };
    const dishes = (sd.hasMenu?.hasMenuSection ?? []).reduce((n, s) => n + (s.hasMenuItem?.length ?? 0), 0);
    const rows: [string, boolean, string][] = [
      ["Name", !!sd.name, String(sd.name ?? "")],
      ["Address", !!sd.address, sd.address ? addressText(sd.address) : "Add it in Pass settings"],
      ["Phone", !!sd.telephone, String(sd.telephone ?? "Add it in Pass settings")],
      ["Opening hours", Array.isArray(sd.openingHoursSpecification) && sd.openingHoursSpecification.length > 0, Array.isArray(sd.openingHoursSpecification) && sd.openingHoursSpecification.length ? "From Pass settings" : "Add them in Pass settings"],
      ["Cuisine", !!sd.servesCuisine, String(sd.servesCuisine ?? "Add it in Pass settings")],
      ["Menu with prices", dishes > 0, dishes ? `${dishes} dishes` : "Add the Menu section to your page"],
      ["Map location", !!sd.geo, sd.geo ? "Set" : "Add your address in Pass settings"],
      ["Social profiles", Array.isArray(sd.sameAs), Array.isArray(sd.sameAs) ? `${sd.sameAs.length} linked` : "Add them in the footer"],
      ["Order and booking links", Array.isArray(sd.potentialAction), Array.isArray(sd.potentialAction) ? "Included" : "Turn on ordering or bookings"],
    ];
    return (
      <div className="seo">
        {back}
        <p className="small muted">We send Google these details in the format it reads for Maps and rich results. They come from your Pass data, so they're always up to date.</p>
        <div className="stack seo-field">
          <span className="small"><strong>Price range</strong></span>
          <div className="seg">
            {(["$", "$$", "$$$", "$$$$"] as const).map((p) => (
              <button key={p} className={seo.price_range === p ? "on" : ""} onClick={() => setSeo({ price_range: seo.price_range === p ? "" : p })}>{p}</button>
            ))}
          </div>
        </div>
        <ul className="seo-facts">
          {rows.map(([k, ok, v]) => (
            <li key={k}><span className={ok ? "yes" : "no"}>{ok ? "✓" : "!"}</span><strong>{k}</strong><span className="small muted">{v}</span></li>
          ))}
        </ul>
        <div className="row">
          <button className="btn small" onClick={() => onFix("restaurant")}>Change address or hours</button>
          {site.is_published && <a className="btn small" href={data.urls.rich_results} target="_blank" rel="noreferrer"><Ico name="external" size={14} /> Test with Google</a>}
        </div>
        <details className="seo-json">
          <summary className="small">See exactly what Google reads</summary>
          <pre>{JSON.stringify(sd, null, 2)}</pre>
        </details>
      </div>
    );
  }

  if (tool === "search") {
    return (
      <div className="seo">
        {back}
        <label className="seo-toggle">
          <input type="checkbox" checked={seo.index} onChange={(e) => setSeo({ index: e.target.checked })} />
          <div><strong>Show my website in search results</strong><span className="small muted">Turn off only while you're still building it.</span></div>
        </label>

        <section className="seo-box">
          <h3><Ico name="search" size={16} /> Google Search Console</h3>
          <p className="small muted">Free from Google: see the searches that bring guests to you, and get alerts if something's wrong.</p>
          <ol className="seo-steps">
            <Step n={1}>Open <a href={data.urls.search_console} target="_blank" rel="noreferrer">Search Console</a>, choose <strong>URL prefix</strong> and enter your address:<CopyRow label="Your address" value={pageUrl} flash={flash} /></Step>
            <Step n={2}>Pick <strong>HTML tag</strong>, copy it and paste it here:
              <input className="input" placeholder='<meta name="google-site-verification" content="…">' value={seo.google_verify}
                onChange={(e) => setSeo({ google_verify: verifyCode(e.target.value) })} />
            </Step>
            <Step n={3}><strong>Publish</strong>, then press <strong>Verify</strong> in Search Console.</Step>
            <Step n={4}>Under <strong>Sitemaps</strong>, add this:<CopyRow label="Sitemap" value={data.urls.sitemap} flash={flash} /></Step>
          </ol>
        </section>

        <section className="seo-box">
          <h3><Ico name="globe" size={16} /> Bing and other search engines</h3>
          <p className="small muted">
            Each time you publish, we tell Bing, Yandex and other IndexNow search engines straight away.
            {data.indexnow.pinged_at && <> Last told {timeAgo(data.indexnow.pinged_at)}{data.indexnow.result && data.indexnow.result !== "ok" ? ` (${data.indexnow.result})` : ""}.</>}
          </p>
          <button className="btn small" style={{ alignSelf: "start" }} disabled={busy === "notify" || !site.is_published} onClick={() => void notify()}>
            {busy === "notify" ? "Telling them…" : "Tell them now"}
          </button>
          <label className="stack" style={{ gap: 4 }}>
            <span className="small">Bing Webmaster Tools code (optional; in <a href={data.urls.bing} target="_blank" rel="noreferrer">Bing</a> choose “HTML Meta Tag”)</span>
            <input className="input" placeholder='<meta name="msvalidate.01" content="…">' value={seo.bing_verify}
              onChange={(e) => setSeo({ bing_verify: verifyCode(e.target.value) })} />
          </label>
        </section>

        <section className="seo-box">
          <h3><Ico name="chart" size={16} /> Google Analytics</h3>
          <p className="small muted">Count your visitors. In <a href={data.urls.analytics} target="_blank" rel="noreferrer">Google Analytics</a>, create a web stream and copy its Measurement ID.</p>
          <input className="input" placeholder="G-XXXXXXXXXX" maxLength={20} value={seo.ga_id} onChange={(e) => setSeo({ ga_id: e.target.value.trim().toUpperCase() })} />
          {seo.ga_id && !/^G-[A-Z0-9]{4,16}$/.test(seo.ga_id) && <p className="small error">That doesn't look like a Measurement ID (it starts with G-).</p>}
        </section>

        <section className="seo-box">
          <h3><Ico name="settings" size={16} /> Technical</h3>
          <CopyRow label="Sitemap" value={data.urls.sitemap} flash={flash} />
          <CopyRow label="robots.txt" value={data.urls.robots} flash={flash} />
          <a className="btn small" style={{ alignSelf: "start" }} href={data.urls.pagespeed} target="_blank" rel="noreferrer"><Ico name="external" size={14} /> Test speed with Google</a>
        </section>
      </div>
    );
  }

  // --- Home: score, checklist, tools ------------------------------------------------------------------------------
  const todo = data.checks.filter((c) => c.status !== "good");
  const done = data.checks.filter((c) => c.status === "good");
  return (
    <div className="seo">
      <div className="seo-score">
        <ScoreRing score={data.score} />
        <div>
          <strong>{data.score >= 80 ? "Looking good" : data.score >= 55 ? "A good start" : "Needs some work"}</strong>
          <p className="small muted">{todo.length ? `${todo.length} thing${todo.length === 1 ? "" : "s"} to do to get found more often.` : "Everything we check is in place."}</p>
          {pro && <span className="pill good">SEO plan on</span>}
        </div>
      </div>

      {!pro && upsell}

      {todo.length > 0 && (
        <section className="stack" style={{ gap: 8 }}>
          <h3>To do</h3>
          <ul className="seo-checks">
            {todo.map((c) => (
              <li key={c.id} className={c.status}>
                <span className="seo-dot" aria-hidden />
                <div>
                  <strong>{c.label}{c.pro && !pro && <Ico name="lock" size={13} />}</strong>
                  <p>{c.detail}</p>
                </div>
                {c.fix && <button className="btn small" onClick={() => fix(c)}>{c.pro && !pro ? "Unlock" : "Fix"}</button>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="stack" style={{ gap: 8 }}>
        <h3>SEO tools</h3>
        <div className="seo-tools">
          {TOOLS.map((t) => (
            <button key={t.id} className="seo-tool" onClick={() => open(t.id)}>
              <span className="seo-tool-icon"><Ico name={t.icon} size={18} /></span>
              <span className="seo-tool-text"><strong>{t.title}</strong><span>{t.text}</span></span>
              {t.pro && !pro ? <Ico name="lock" size={15} /> : <Ico name="right" size={15} />}
            </button>
          ))}
        </div>
      </section>

      {done.length > 0 && (
        <section className="stack" style={{ gap: 8 }}>
          <button className="seo-done-toggle" onClick={() => setShowDone(!showDone)}>
            <Ico name="check" size={15} /> {done.length} done {showDone ? "▴" : "▾"}
          </button>
          {showDone && (
            <ul className="seo-checks">
              {done.map((c) => (
                <li key={c.id} className="good"><span className="seo-dot" aria-hidden /><div><strong>{c.label}</strong><p>{c.detail}</p></div></li>
              ))}
            </ul>
          )}
        </section>
      )}

      {pro && <button className="seo-plan-link small" onClick={() => setTool("plan")}>Manage your SEO plan</button>}
    </div>
  );
}
