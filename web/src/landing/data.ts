// The landing page's data: the newest Volt incident from the key-free demo API, or the bundled
// recorded incident when the API is asleep, slow, outside the service's 09:00-21:00 IST window,
// or the live incident has no version change to show. One dataset drives every readout and scene.
import { API_BASE } from "../config";
import { noTimes } from "../app/format";
import fallback from "./fallback-incident.json";

export interface Point { t: string; request_id: string | null; distance: number | null; flagged: boolean; nonanswer: boolean;
                         prompt_version?: string | null; model_version?: string | null }
export interface Version { field: "prompt_version" | "model_version"; value: string; first_seen: string; answers?: number }
interface Claim { text: string; evidence_ids: string[] }
export interface Data {
  source: "live" | "snapshot";
  company: string;
  captured_at?: string;
  incident: { id: number; severity: number; severity_label: string; opened_at: string; closed_at: string | null; reasons: string[] };
  note: { cause: string; confidence: number; written_by: string; labels?: Record<string, { label: string; title: string }>;
          report: { headline: string; claims: Claim[]; example_ids: string[]; suggested_check: string } } | null;
  distance_series: { threshold: number | null; points: Point[] };
  version_timeline?: Version[];
}

/** What the scenes need, derived once. */
export interface Scene {
  d: Data;
  pts: Point[];              // answers with a distance, in order
  version: Version;          // the change the incident is about
  changeAt: number;          // index of the first answer on the new version
  label: string;             // "prompt v2"
  date: string;              // "12 nov" (dates only, never clock times)
}

export const snapshot = fallback as unknown as Data;

const inServiceWindow = (now = new Date()) => {
  const ist = (now.getUTCHours() * 60 + now.getUTCMinutes() + 330) % 1440;
  return ist >= 9 * 60 && ist < 21 * 60;
};

async function getJSON<T>(path: string, ms: number): Promise<T> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(API_BASE + path, { signal: ctl.signal });
    if (!r.ok) throw new Error(String(r.status));
    return (await r.json()) as T;
  } finally { clearTimeout(t); }
}

// "12 nov" in IST, by arithmetic: the first time-zone-aware Intl format costs ~100+ ms (ICU data) at load.
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
const day = (iso: string) => { const d = new Date(Date.parse(iso) + 330 * 60_000); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };

/** The version that appeared last before the incident opened and after the first point. */
function newVersion(d: Data, p: Point[]): Version | null {
  const opened = Date.parse(d.incident.opened_at), first = p.length ? Date.parse(p[0].t) : Infinity;
  const vs = (d.version_timeline ?? []).filter(v => Date.parse(v.first_seen) > first && Date.parse(v.first_seen) <= opened + 60_000);
  return vs.sort((a, b) => Date.parse(b.first_seen) - Date.parse(a.first_seen))[0] ?? null;
}

export function toScene(d: Data): Scene | null {
  const pts = d.distance_series.points.filter(p => p.distance != null);
  const v = newVersion(d, pts);
  if (!v || !d.note || pts.length < 10) return null;
  const changeAt = pts.findIndex(p => p[v.field] === v.value);
  if (changeAt < 1) return null;
  return { d, pts, version: v, changeAt, label: `${v.field === "prompt_version" ? "prompt" : "model"} ${v.value}`, date: day(v.first_seen) };
}

export const snapshotScene = toScene(snapshot)!;

/** Live data when it can tell the same story; the recorded incident otherwise. Never wakes Render outside its window. */
export async function load(): Promise<Scene> {
  if (!inServiceWindow()) return snapshotScene;
  try {
    const list = await getJSON<{ incidents: { id: number; cause: string | null }[] }>("/demo/volt/incidents", 4000);
    const inc = list.incidents.find(i => i.cause);
    if (!inc) return snapshotScene;
    const d = await getJSON<Omit<Data, "source">>(`/demo/volt/incidents/${inc.id}`, 4000);
    return toScene({ ...d, source: "live" }) ?? snapshotScene;
  } catch { return snapshotScene; }
}

// ---- the investigation note, with its cited ids ---------------------------------------

export type Seg = { text: string; id?: boolean };

export function noteSegments(d: Data): Seg[] {
  const r = d.note!.report;
  const lab = (id: string) => d.note!.labels?.[id]?.label ?? id;
  const segs: Seg[] = [{ text: noTimes(r.headline) + "\n" }];
  for (const c of r.claims.slice(0, 3)) {
    segs.push({ text: "\n– " + noTimes(c.text).replace(/\s*\((?:[A-Z]\d+(?:[,‑\- ]+[A-Z]?\d+)*)\)\.?$/, "") + " " });
    for (const id of c.evidence_ids.slice(0, 3)) segs.push({ text: lab(id), id: true }, { text: " " });
  }
  return segs;
}
