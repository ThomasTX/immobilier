/**
 * Isochrones voiture via le service de calcul d'isochrones de la Géoplateforme IGN
 * (gratuit, sans clé, réseau routier BD TOPO). On demande plusieurs paliers de temps et on
 * attribue à chaque commune le plus petit palier qui contient son centre.
 */
import { polygonContains, preparePolygon, type PreparedPolygon } from "./geo";

const IGN = "https://data.geopf.fr/navigation";
const cache = new Map<string, Promise<GeoJSON.Geometry>>();

export function carBands(maxMinutes: number): number[] {
  const step = maxMinutes <= 60 ? 5 : maxMinutes <= 120 ? 10 : 15;
  const bands: number[] = [];
  for (let m = step; m < maxMinutes; m += step) bands.push(m);
  bands.push(maxMinutes);
  return bands;
}

export function fetchIsochrone(lat: number, lon: number, minutes: number): Promise<GeoJSON.Geometry> {
  const url =
    `${IGN}/isochrone?resource=bdtopo-valhalla&point=${lon.toFixed(5)},${lat.toFixed(5)}` +
    `&costValue=${Math.round(minutes * 60)}&costType=time&profile=car&direction=arrival&geometryFormat=geojson`;
  let p = cache.get(url);
  if (!p) {
    p = fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`Isochrone IGN : erreur ${r.status}`);
        return r.json();
      })
      .then((j) => j.geometry as GeoJSON.Geometry);
    p.catch(() => cache.delete(url));
    cache.set(url, p);
  }
  return p;
}

export interface CarResult {
  bands: number[];
  polygons: { minutes: number; geometry: GeoJSON.Geometry }[];
  /** temps estimé par commune (borne haute du palier), Infinity au-delà du dernier palier */
  minutes: Float32Array;
}

export async function computeCar(
  lat: number,
  lon: number,
  maxMinutes: number,
  clat: number[],
  clon: number[],
): Promise<CarResult> {
  const bands = carBands(maxMinutes);
  const geoms = await Promise.all(bands.map((b) => fetchIsochrone(lat, lon, b)));
  const n = clat.length;
  const minutes = new Float32Array(n).fill(Infinity);
  const prepared = geoms.map((g) => preparePolygon(g)).filter(Boolean) as PreparedPolygon[];
  // du plus grand au plus petit : on ne teste dans un palier que les communes du palier supérieur
  let candidates: number[] = [];
  const big = prepared[prepared.length - 1];
  if (big) for (let i = 0; i < n; i++) if (polygonContains(big, clon[i], clat[i])) candidates.push(i);
  for (const i of candidates) minutes[i] = bands[bands.length - 1];
  for (let b = prepared.length - 2; b >= 0; b--) {
    const inside: number[] = [];
    for (const i of candidates) if (polygonContains(prepared[b], clon[i], clat[i])) inside.push(i);
    for (const i of inside) minutes[i] = bands[b];
    candidates = inside;
  }
  return { bands, polygons: bands.map((m, i) => ({ minutes: m, geometry: geoms[i] })), minutes };
}

/** Temps de trajet voiture exact entre deux points (itinéraire IGN), en minutes. */
export async function carRoute(fromLat: number, fromLon: number, toLat: number, toLon: number): Promise<number | null> {
  const url =
    `${IGN}/itineraire?resource=bdtopo-osrm&start=${fromLon},${fromLat}&end=${toLon},${toLat}` +
    `&profile=car&optimization=fastest&getSteps=false&getBbox=false&timeUnit=minute&geometryFormat=geojson`;
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    const j = await r.json();
    return typeof j.duration === "number" ? j.duration : null;
  } catch {
    return null;
  }
}
