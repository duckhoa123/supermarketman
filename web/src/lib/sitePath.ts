import type { SiteContent, SiteEditor, SiteField } from "./api";

/** What a click on the page points at: "<section id>:<field path>", "layout:<field>" (header and footer) or
 * "menu_page:<field>" (the separate menu page). */
export type Target = { where: string; path: string[] };

export function parseTarget(raw: string): Target {
  const i = raw.indexOf(":");
  return { where: raw.slice(0, i), path: raw.slice(i + 1).split(".") };
}

function getDeep(obj: unknown, path: string[]): unknown {
  return path.reduce<unknown>((o, k) => (o == null ? undefined : (o as Record<string, unknown>)[k]), obj);
}

function setDeep(obj: unknown, path: string[], value: unknown): unknown {
  if (!path.length) return value;
  const [k, ...rest] = path;
  if (Array.isArray(obj)) {
    const copy = obj.slice();
    copy[Number(k)] = setDeep(copy[Number(k)], rest, value);
    return copy;
  }
  const base = (obj && typeof obj === "object" ? obj : {}) as Record<string, unknown>;
  return { ...base, [k]: setDeep(base[k], rest, value) };
}

export function readTarget(content: SiteContent, t: Target): unknown {
  if (t.where === "layout") return getDeep(content.layout, t.path);
  if (t.where === "menu_page") return getDeep(content.menu_page, t.path);
  return getDeep(content.sections.find((s) => s.id === t.where)?.props, t.path);
}

export function writeTarget(content: SiteContent, t: Target, value: unknown): SiteContent {
  if (t.where === "layout") return { ...content, layout: setDeep(content.layout, t.path, value) as SiteContent["layout"] };
  if (t.where === "menu_page") return { ...content, menu_page: setDeep(content.menu_page, t.path, value) as Record<string, unknown> };
  return {
    ...content,
    sections: content.sections.map((s) =>
      s.id === t.where ? { ...s, props: setDeep(s.props, t.path, value) as Record<string, unknown> } : s),
  };
}

/** The declared field for a target, to know its length limit and label. */
export function fieldFor(site: SiteEditor, content: SiteContent, t: Target): SiteField | undefined {
  let fields: SiteField[] | undefined;
  if (t.where === "layout") fields = site.layout_fields;
  else if (t.where === "menu_page") fields = site.menu_page_fields;
  else {
    const type = content.sections.find((s) => s.id === t.where)?.type;
    fields = site.sections.find((x) => x.type === type)?.fields;
  }
  let field = fields?.find((f) => f.name === t.path[0]);
  if (field?.kind === "items" && t.path.length === 3) field = field.fields?.find((f) => f.name === t.path[2]);
  return field;
}

export function newSectionId(type: string): string {
  return `${type}-${Math.random().toString(16).slice(2, 10)}`;
}
