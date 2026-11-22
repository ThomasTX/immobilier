/**
 * Isochrones train : Connection Scan Algorithm (CSA) inversé sur les horaires SNCF + Transilien
 * d'un mardi type. Pour une destination et une heure d'arrivée souhaitée, calcule pour chaque gare
 * la durée minimale du trajet (gare → destination), en testant plusieurs heures limites dans la
 * fenêtre [arriveBy - window, arriveBy].
 */
import type { AccessMode, Destination, TransitData } from "../data/types";
import { F_TRAIN, F_BUS } from "../data/types";
import { PointGrid } from "./geo";

const TRANSFER_SLACK = 4; // minutes de correspondance minimale
const STEP = 10; // pas entre heures limites testées
const MAX_JOURNEY = 6 * 60;

export interface TrainResult {
  /** durée (min) gare → destination, Infinity si injoignable */
  dur: Float32Array;
  /** heure de départ (min) correspondant à la meilleure durée */
  depTime: Float32Array;
  /** heure d'arrivée limite utilisée */
  arrTime: Float32Array;
}

let stationGrid: PointGrid | null = null;
let gridFor: TransitData | null = null;
export function stationsGrid(t: TransitData): PointGrid {
  if (!stationGrid || gridFor !== t) {
    stationGrid = new PointGrid(
      t.stations.map((s) => s[1]),
      t.stations.map((s) => s[2]),
      0.2,
    );
    gridFor = t;
  }
  return stationGrid;
}

/** Temps (min) pour rejoindre la destination depuis une gare d'arrivée. */
function egressStations(t: TransitData, lat: number, lon: number): [number, number][] {
  const g = stationsGrid(t);
  const res: [number, number][] = [];
  for (const [s, d] of g.near(lat, lon, 1.6)) res.push([s, 2 + d * 1.3 * 13]); // à pied 4,6 km/h
  // si pas de gare à pied : rabattement bus/taxi/voiture vers les gares proches
  const far = g.near(lat, lon, 25).filter(([s]) => !(t.stations[s][3] & F_BUS) || true);
  for (const [s, d] of far.slice(0, 6)) {
    if (d <= 1.6) continue;
    res.push([s, 6 + d * 1.3 * 1.6]);
  }
  return res;
}

export function computeTrain(t: TransitData, dest: Pick<Destination, "lat" | "lon" | "arriveBy" | "window">): TrainResult {
  const ns = t.stations.length;
  const dur = new Float32Array(ns).fill(Infinity);
  const depTime = new Float32Array(ns).fill(NaN);
  const arrTime = new Float32Array(ns).fill(NaN);
  const need = new Float64Array(ns);
  const ldep = new Float64Array(ns);
  let maxTrip = 0;
  for (let i = 0; i < t.n; i++) if (t.trip[i] > maxTrip) maxTrip = t.trip[i];
  const stamp = new Int32Array(maxTrip + 1).fill(-1);
  const egress = egressStations(t, dest.lat, dest.lon);
  if (!egress.length) return { dur, depTime, arrTime };

  // dernière connexion de départ <= T (recherche binaire sur dep trié)
  const upper = (T: number) => {
    let lo = 0,
      hi = t.n;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (t.dep[m] <= T) lo = m + 1;
      else hi = m;
    }
    return lo - 1;
  };

  const steps = Math.max(1, Math.floor(dest.window / STEP) + 1);
  for (let k = 0; k < steps; k++) {
    const T = dest.arriveBy - k * STEP;
    need.fill(-Infinity);
    ldep.fill(-Infinity);
    for (const [s, eg] of egress) {
      const v = T - eg;
      if (v > need[s]) need[s] = v;
      if (v > ldep[s]) ldep[s] = v;
    }
    // correspondances à pied autour des gares d'arrivée
    for (const [s] of egress)
      for (const [w, m] of t.foot[s]) {
        const v = need[s] - m;
        if (v > need[w]) need[w] = v;
        if (v > ldep[w]) ldep[w] = v;
      }
    const stopAt = T - MAX_JOURNEY;
    for (let i = upper(T); i >= 0; i--) {
      const dep = t.dep[i];
      if (dep < stopAt) break;
      const trip = t.trip[i];
      const v = t.to[i];
      if (stamp[trip] !== k && t.arr[i] > need[v]) continue;
      stamp[trip] = k;
      const u = t.from[i];
      if (dep > ldep[u]) ldep[u] = dep;
      const nu = dep - TRANSFER_SLACK;
      if (nu > need[u]) {
        need[u] = nu;
        for (const [w, m] of t.foot[u]) {
          const vw = nu - m;
          if (vw > need[w]) need[w] = vw;
          if (dep - m > ldep[w]) ldep[w] = dep - m;
        }
      }
    }
    for (let s = 0; s < ns; s++) {
      if (ldep[s] === -Infinity) continue;
      const d = T - ldep[s];
      if (d < dur[s]) {
        dur[s] = d;
        depTime[s] = ldep[s];
        arrTime[s] = T;
      }
    }
  }
  return { dur, depTime, arrTime };
}

/** minutes pour parcourir d km (vol d'oiseau) selon le mode de rabattement vers la gare */
export function accessMinutes(mode: AccessMode, km: number): number {
  switch (mode) {
    case "walk":
      return km * 1.3 * 13; // 4,6 km/h
    case "bike":
      return km * 1.3 * 4; // 15 km/h
    case "car":
      return 4 + km * 1.3 * 1.4; // ~43 km/h moyen + stationnement
  }
}

export function accessRadiusKm(mode: AccessMode, maxMin: number): number {
  switch (mode) {
    case "walk":
      return maxMin / (1.3 * 13);
    case "bike":
      return maxMin / (1.3 * 4);
    case "car":
      return Math.max(0, maxMin - 4) / (1.3 * 1.4);
  }
}

export interface PointTrain {
  minutes: number;
  station: number;
  access: number;
  depTime: number;
  arrTime: number;
}

/** Meilleur trajet train depuis un point (domicile) vers la destination. */
export function trainFromPoint(
  t: TransitData,
  r: TrainResult,
  lat: number,
  lon: number,
  mode: AccessMode,
  maxAccess: number,
): PointTrain | null {
  const radius = accessRadiusKm(mode, maxAccess);
  let best: PointTrain | null = null;
  for (const [s, d] of stationsGrid(t).near(lat, lon, radius)) {
    const dd = r.dur[s];
    if (!isFinite(dd)) continue;
    const acc = accessMinutes(mode, d);
    const tot = acc + dd + 2;
    if (!best || tot < best.minutes)
      best = { minutes: tot, station: s, access: acc, depTime: r.depTime[s] - acc - 2, arrTime: r.arrTime[s] };
  }
  return best;
}

/** Temps train pour chaque commune (Float32Array, Infinity si inaccessible). */
export function trainForCommunes(
  t: TransitData,
  r: TrainResult,
  lat: number[],
  lon: number[],
  mode: AccessMode,
  maxAccess: number,
): { minutes: Float32Array; station: Int32Array } {
  const n = lat.length;
  const minutes = new Float32Array(n).fill(Infinity);
  const station = new Int32Array(n).fill(-1);
  for (let i = 0; i < n; i++) {
    const b = trainFromPoint(t, r, lat[i], lon[i], mode, maxAccess);
    if (b) {
      minutes[i] = b.minutes;
      station[i] = b.station;
    }
  }
  return { minutes, station };
}

export function isRailStation(t: TransitData, s: number) {
  return (t.stations[s][3] & F_TRAIN) !== 0;
}
