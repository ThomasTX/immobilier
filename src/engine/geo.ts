export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const toRad = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRad;
  const dLon = (lon2 - lon1) * toRad;
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Index spatial simple (grille) sur des points lat/lon. */
export class PointGrid {
  private cells = new Map<string, number[]>();
  constructor(
    private lat: ArrayLike<number>,
    private lon: ArrayLike<number>,
    private cell = 0.25,
  ) {
    for (let i = 0; i < lat.length; i++) {
      const k = this.key(lat[i], lon[i]);
      const c = this.cells.get(k);
      if (c) c.push(i);
      else this.cells.set(k, [i]);
    }
  }
  private key(la: number, lo: number) {
    return `${Math.floor(la / this.cell)}:${Math.floor(lo / this.cell)}`;
  }
  /** indices et distances (km) des points à moins de radiusKm, triés par distance */
  near(la: number, lo: number, radiusKm: number): [number, number][] {
    const dLat = radiusKm / 111;
    const dLon = radiusKm / (111 * Math.max(0.2, Math.cos((la * Math.PI) / 180)));
    const out: [number, number][] = [];
    const i0 = Math.floor((la - dLat) / this.cell);
    const i1 = Math.floor((la + dLat) / this.cell);
    const j0 = Math.floor((lo - dLon) / this.cell);
    const j1 = Math.floor((lo + dLon) / this.cell);
    for (let i = i0; i <= i1; i++)
      for (let j = j0; j <= j1; j++) {
        const c = this.cells.get(`${i}:${j}`);
        if (!c) continue;
        for (const p of c) {
          const d = haversineKm(la, lo, this.lat[p], this.lon[p]);
          if (d <= radiusKm) out.push([p, d]);
        }
      }
    out.sort((a, b) => a[1] - b[1]);
    return out;
  }
}

type Ring = number[][];
type Poly = Ring[];

function ringContains(ring: Ring, x: number, y: number): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0],
      yi = ring[i][1],
      xj = ring[j][0],
      yj = ring[j][1];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export interface PreparedPolygon {
  bbox: [number, number, number, number];
  polys: { bbox: [number, number, number, number]; rings: Poly }[];
}

function bboxOf(rings: Ring[]): [number, number, number, number] {
  let a = Infinity,
    b = Infinity,
    c = -Infinity,
    d = -Infinity;
  for (const r of rings)
    for (const p of r) {
      if (p[0] < a) a = p[0];
      if (p[1] < b) b = p[1];
      if (p[0] > c) c = p[0];
      if (p[1] > d) d = p[1];
    }
  return [a, b, c, d];
}

export function preparePolygon(geom: GeoJSON.Geometry): PreparedPolygon | null {
  let polys: Poly[] = [];
  if (geom.type === "Polygon") polys = [geom.coordinates as Poly];
  else if (geom.type === "MultiPolygon") polys = geom.coordinates as Poly[];
  else if (geom.type === "GeometryCollection") {
    const parts = geom.geometries.map(preparePolygon).filter(Boolean) as PreparedPolygon[];
    const all = parts.flatMap((p) => p.polys);
    if (!all.length) return null;
    return { bbox: bboxOf(all.map((p) => p.rings[0])), polys: all };
  } else return null;
  const prepared = polys.filter((p) => p.length).map((rings) => ({ bbox: bboxOf([rings[0]]), rings }));
  if (!prepared.length) return null;
  return { bbox: bboxOf(prepared.map((p) => p.rings[0])), polys: prepared };
}

export function polygonContains(pp: PreparedPolygon, lon: number, lat: number): boolean {
  const [a, b, c, d] = pp.bbox;
  if (lon < a || lon > c || lat < b || lat > d) return false;
  for (const p of pp.polys) {
    const [e, f, g, h] = p.bbox;
    if (lon < e || lon > g || lat < f || lat > h) continue;
    if (!ringContains(p.rings[0], lon, lat)) continue;
    let inHole = false;
    for (let k = 1; k < p.rings.length; k++)
      if (ringContains(p.rings[k], lon, lat)) {
        inHole = true;
        break;
      }
    if (!inHole) return true;
  }
  return false;
}

export function fmtMinutes(m: number | null | undefined): string {
  if (m == null || !isFinite(m)) return "—";
  const r = Math.round(m);
  if (r < 60) return `${r} min`;
  return `${Math.floor(r / 60)} h ${String(r % 60).padStart(2, "0")}`;
}

export function fmtClock(m: number): string {
  const r = Math.round(m);
  const h = Math.floor(r / 60) % 24;
  return `${h}:${String(r % 60).padStart(2, "0")}`;
}
