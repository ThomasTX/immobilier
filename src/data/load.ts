import type { CommunesData, TransitData } from "./types";

const base = import.meta.env.BASE_URL;

export async function loadCommunes(): Promise<CommunesData> {
  const r = await fetch(`${base}data/communes.json`);
  if (!r.ok) throw new Error("Données communes introuvables (lancez le pipeline : npm run data)");
  return r.json();
}

export async function loadGeometry(): Promise<unknown> {
  const r = await fetch(`${base}data/communes.topojson`);
  if (!r.ok) throw new Error("Contours des communes introuvables");
  return r.json();
}

export async function loadTransit(): Promise<TransitData> {
  const [st, bin, fp, meta] = await Promise.all([
    fetch(`${base}data/transit/stations.json`).then((r) => r.json()),
    fetch(`${base}data/transit/connections.bin`).then((r) => r.arrayBuffer()),
    fetch(`${base}data/transit/footpaths.json`).then((r) => r.json()),
    fetch(`${base}data/transit/meta.json`).then((r) => r.json()),
  ]);
  const n = new Uint32Array(bin, 0, 1)[0];
  let off = 4;
  const u16 = () => {
    const a = new Uint16Array(bin, off, n);
    off += n * 2;
    return a;
  };
  const from = u16();
  const to = u16();
  const dep = u16();
  const arr = u16();
  if (off % 4) off += 2;
  const trip = new Uint32Array(bin, off, n);
  const foot: [number, number][][] = st.map(() => []);
  for (const [a, b, m] of fp as [number, number, number][]) {
    foot[a].push([b, m]);
    foot[b].push([a, m]);
  }
  return { stations: st, n, from, to, dep, arr, trip, foot, meta };
}
