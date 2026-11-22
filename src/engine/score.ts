import type { CommunesData, Destination, Indicator, NumFilter } from "../data/types";

/** Rang percentile (0..1) de chaque commune pour un indicateur, mis en cache. */
const rankCache = new Map<string, Float32Array>();
export function ranks(data: CommunesData, id: string): Float32Array {
  let r = rankCache.get(id);
  if (r) return r;
  const vals = data.values[id];
  const n = vals.length;
  const idx: number[] = [];
  for (let i = 0; i < n; i++) if (vals[i] != null) idx.push(i);
  idx.sort((a, b) => (vals[a] as number) - (vals[b] as number));
  r = new Float32Array(n).fill(NaN);
  // ex aequo : rang moyen
  let i = 0;
  const m = idx.length;
  while (i < m) {
    let j = i;
    while (j + 1 < m && vals[idx[j + 1]] === vals[idx[i]]) j++;
    const rank = m > 1 ? (i + j) / 2 / (m - 1) : 0.5;
    for (let k = i; k <= j; k++) r[idx[k]] = rank;
    i = j + 1;
  }
  rankCache.set(id, r);
  return r;
}

export interface TravelTimes {
  destId: string;
  minutes: Float32Array; // Infinity = injoignable / au-delà
  car?: Float32Array;
  train?: Float32Array;
  trainStation?: Int32Array;
}

export interface Evaluation {
  pass: Uint8Array;
  score: Float32Array; // 0..100, NaN si aucun critère pondéré
  nPass: number;
  /** par critère (id d'indicateur, "dest:<id>" ou "dep") : nb de communes éliminées par ce seul critère */
  blockedOnlyBy: Record<string, number>;
  /** par critère : nb total de communes qui ne le respectent pas */
  failing: Record<string, number>;
}

export function parseDeps(s: string): Set<string> | null {
  const parts = s
    .split(/[\s,;]+/)
    .map((p) => p.trim().toUpperCase())
    .filter(Boolean)
    .map((p) => (p.length === 1 ? "0" + p : p));
  return parts.length ? new Set(parts) : null;
}

export function filterActive(f: NumFilter | undefined): boolean {
  if (!f) return false;
  return f.min != null || f.max != null || !!f.bool || (f.cats != null && f.cats.length > 0) || !!f.excludeMissing;
}

export function passesFilter(ind: Indicator, f: NumFilter, v: number | null): boolean {
  if (v == null) return !f.excludeMissing;
  if (ind.type === "boolean") {
    if (f.bool === "yes" && v !== 1) return false;
    if (f.bool === "no" && v !== 0) return false;
    return true;
  }
  if (ind.type === "category" && f.cats && f.cats.length) return f.cats.includes(String(v));
  if (f.min != null && v < f.min) return false;
  if (f.max != null && v > f.max) return false;
  return true;
}

export function direction(ind: Indicator, f: NumFilter | undefined): "higher" | "lower" | null {
  return (f?.prefer ?? ind.better) || null;
}

export function evaluate(
  data: CommunesData,
  filters: Record<string, NumFilter>,
  destinations: Destination[],
  travel: TravelTimes[],
  deps: string,
): Evaluation {
  const n = data.codes.length;
  const pass = new Uint8Array(n).fill(1);
  const nFail = new Uint8Array(n);
  const lastFail: string[] = new Array(n);
  const failing: Record<string, number> = {};
  const fail = (i: number, key: string) => {
    pass[i] = 0;
    if (nFail[i] < 255) nFail[i]++;
    lastFail[i] = key;
    failing[key] = (failing[key] ?? 0) + 1;
  };
  const num = new Float32Array(n);
  const den = new Float32Array(n);
  const depSet = parseDeps(deps);
  if (depSet) for (let i = 0; i < n; i++) if (!depSet.has(data.deps[i])) fail(i, "dep");

  for (const ind of data.indicators) {
    const f = filters[ind.id];
    if (!f) continue;
    const vals = data.values[ind.id];
    if (filterActive(f)) for (let i = 0; i < n; i++) if (!passesFilter(ind, f, vals[i])) fail(i, ind.id);
    const dir = direction(ind, f);
    if (f.weight > 0 && dir) {
      const r = ranks(data, ind.id);
      for (let i = 0; i < n; i++) {
        let s = r[i];
        if (Number.isNaN(s)) s = 0.4;
        else if (dir === "lower") s = 1 - s;
        num[i] += f.weight * s;
        den[i] += f.weight;
      }
    }
  }

  for (const d of destinations) {
    if (!d.enabled) continue;
    const tt = travel.find((t) => t.destId === d.id);
    if (!tt) continue;
    const m = tt.minutes;
    if (d.required) for (let i = 0; i < n; i++) if (!(m[i] <= d.maxMinutes)) fail(i, `dest:${d.id}`);
    if (d.weight > 0)
      for (let i = 0; i < n; i++) {
        const s = isFinite(m[i]) ? Math.max(0, 1 - m[i] / (d.maxMinutes * 1.25)) : 0;
        num[i] += d.weight * s;
        den[i] += d.weight;
      }
  }

  const score = new Float32Array(n);
  let nPass = 0;
  const blockedOnlyBy: Record<string, number> = {};
  for (let i = 0; i < n; i++) {
    score[i] = den[i] > 0 ? (100 * num[i]) / den[i] : NaN;
    nPass += pass[i];
    if (nFail[i] === 1) blockedOnlyBy[lastFail[i]] = (blockedOnlyBy[lastFail[i]] ?? 0) + 1;
  }
  return { pass, score, nPass, blockedOnlyBy, failing };
}

export function fmtValue(ind: Indicator | undefined, v: number | null | undefined): string {
  if (v == null || (typeof v === "number" && !isFinite(v))) return "—";
  if (!ind) return String(v);
  if (ind.type === "boolean") return v ? "Oui" : "Non";
  if (ind.type === "category") return ind.categories?.[String(v)] ?? String(v);
  const dec = ind.decimals ?? 1;
  const s = v.toLocaleString("fr-FR", { maximumFractionDigits: dec, minimumFractionDigits: 0 });
  return ind.unit ? `${s} ${ind.unit}` : s;
}
