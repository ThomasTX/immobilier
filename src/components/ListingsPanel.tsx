import { useMemo, useState } from "react";
import type { CommunesData, Destination, Listing, ListingStatus, ListingType, TransitData } from "../data/types";
import type { Evaluation } from "../engine/score";
import { fmtMinutes } from "../engine/geo";
import { carRoute } from "../engine/car";
import type { TrainResult } from "../engine/train";
import { trainFromPoint } from "../engine/train";
import { download, uid } from "../state/store";
import { AddressSearch } from "./AddressSearch";
import { STATUS_COLORS } from "./MapView";
import { ScoreBadge } from "./ResultsPanel";

export const STATUS_LABELS: Record<ListingStatus, string> = {
  repere: "Repéré",
  a_visiter: "À visiter",
  visite: "Visité",
  offre: "Offre faite",
  ecarte: "Écarté",
};
const TYPE_LABELS: Record<ListingType, string> = { appartement: "Appartement", maison: "Maison", terrain: "Terrain", autre: "Autre" };
const FEATURES = ["jardin", "garage", "parking", "balcon/terrasse", "piscine", "cave", "ascenseur", "plain-pied", "vue", "travaux", "calme"];
const DPE = ["A", "B", "C", "D", "E", "F", "G"];

export function emptyListing(p: Partial<Listing> = {}): Listing {
  return {
    id: uid(),
    title: "",
    url: "",
    type: "maison",
    price: null,
    surface: null,
    land: null,
    rooms: null,
    bedrooms: null,
    dpe: "",
    address: "",
    lat: null,
    lon: null,
    insee: "",
    status: "repere",
    rating: 0,
    notes: "",
    features: [],
    createdAt: new Date().toISOString(),
    ...p,
  };
}

/** Extraction best-effort des infos d'une annonce collée (texte copié depuis le site). */
export function parseListingText(text: string): Partial<Listing> {
  const t = text.replace(/ | /g, " ");
  const out: Partial<Listing> = {};
  const num = (s: string) => Number(s.replace(/[\s.]/g, "").replace(",", "."));
  const price = t.match(/(\d{1,3}(?:[ .]\d{3})+|\d{4,7})\s*(?:€|euros?)/i);
  if (price) out.price = num(price[1]);
  const land = t.match(/terrain[^\d]{0,25}(\d[\d .]*(?:,\d+)?)\s*m(?:²|2)/i) || t.match(/(\d[\d .]*)\s*m(?:²|2)\s*de terrain/i);
  if (land) out.land = num(land[1]);
  const surf = t.match(/(\d{2,4}(?:,\d+)?)\s*m(?:²|2)(?!\s*de terrain)/i);
  if (surf && (!land || num(surf[1]) !== out.land)) out.surface = num(surf[1]);
  const rooms = t.match(/(\d{1,2})\s*pi[eè]ces?/i);
  if (rooms) out.rooms = +rooms[1];
  const beds = t.match(/(\d{1,2})\s*chambres?/i);
  if (beds) out.bedrooms = +beds[1];
  const dpe = t.match(/(?:DPE|classe [ée]nerg[ée]tique|diagnostic de performance [ée]nerg[ée]tique)[^A-G]{0,20}\b([A-G])\b/i);
  if (dpe) out.dpe = dpe[1].toUpperCase();
  if (/\bterrain\b/i.test(t.slice(0, 80)) && !/maison|appartement/i.test(t.slice(0, 80))) out.type = "terrain";
  else if (/appartement|\bT\d\b|\bF\d\b|studio/i.test(t)) out.type = "appartement";
  else if (/maison|villa|longère|corps de ferme|chalet/i.test(t)) out.type = "maison";
  const url = t.match(/https?:\/\/\S+/);
  if (url) out.url = url[0];
  out.features = FEATURES.filter((f) => new RegExp(f.split("/")[0], "i").test(t));
  const first = t.split("\n").map((l) => l.trim()).find((l) => l.length > 5 && !/^https?:/.test(l));
  if (first) out.title = first.slice(0, 90);
  return out;
}

interface LFilters {
  types: ListingType[];
  statuses: ListingStatus[];
  maxPrice: number | null;
  minSurface: number | null;
  minLand: number | null;
  maxDpe: string;
  inZone: boolean;
}

export function ListingsPanel({
  listings,
  setListings,
  data,
  evaluation,
  destinations,
  trainResults,
  transit,
  onLocate,
  editing,
  setEditing,
}: {
  listings: Listing[];
  setListings: (f: (l: Listing[]) => Listing[]) => void;
  data: CommunesData;
  evaluation: Evaluation;
  destinations: Destination[];
  trainResults: Record<string, TrainResult>;
  transit: TransitData | null;
  onLocate: (l: Listing) => void;
  editing: Listing | null;
  setEditing: (l: Listing | null) => void;
}) {
  const [f, setF] = useState<LFilters>({
    types: [],
    statuses: ["repere", "a_visiter", "visite", "offre"],
    maxPrice: null,
    minSurface: null,
    minLand: null,
    maxDpe: "",
    inZone: false,
  });
  const [sort, setSort] = useState("created");
  const codeIdx = useMemo(() => new Map(data.codes.map((c, i) => [c, i])), [data]);
  const dests = destinations.filter((d) => d.enabled);

  const enriched = useMemo(
    () =>
      listings.map((l) => {
        const ci = codeIdx.get(l.insee);
        const ppm2 = l.price && l.surface && l.type !== "terrain" ? l.price / l.surface : null;
        const median = ci != null ? data.values[l.type === "appartement" ? "prix_m2_appart" : "prix_m2_maison"]?.[ci] ?? null : null;
        const vsMedian = ppm2 && median ? (ppm2 / median - 1) * 100 : null;
        const train: Record<string, number | null> = {};
        for (const d of dests) {
          const tr = trainResults[d.id];
          if (tr && transit && l.lat != null && l.lon != null)
            train[d.id] = trainFromPoint(transit, tr, l.lat, l.lon, d.access, d.maxAccess)?.minutes ?? null;
        }
        return { l, ci, ppm2, median, vsMedian, train };
      }),
    [listings, codeIdx, data, dests, trainResults, transit],
  );

  const shown = enriched
    .filter(({ l, ci }) => {
      if (f.types.length && !f.types.includes(l.type)) return false;
      if (f.statuses.length && !f.statuses.includes(l.status)) return false;
      if (f.maxPrice && (l.price ?? 0) > f.maxPrice) return false;
      if (f.minSurface && (l.surface ?? 0) < f.minSurface) return false;
      if (f.minLand && (l.land ?? 0) < f.minLand) return false;
      if (f.maxDpe && l.dpe && l.dpe > f.maxDpe) return false;
      if (f.inZone && (ci == null || !evaluation.pass[ci])) return false;
      return true;
    })
    .sort((a, b) => {
      switch (sort) {
        case "price":
          return (a.l.price ?? Infinity) - (b.l.price ?? Infinity);
        case "ppm2":
          return (a.ppm2 ?? Infinity) - (b.ppm2 ?? Infinity);
        case "vsMedian":
          return (a.vsMedian ?? Infinity) - (b.vsMedian ?? Infinity);
        case "rating":
          return b.l.rating - a.l.rating;
        case "score":
          return (b.ci != null ? evaluation.score[b.ci] || 0 : 0) - (a.ci != null ? evaluation.score[a.ci] || 0 : 0);
        default:
          return b.l.createdAt.localeCompare(a.l.createdAt);
      }
    });

  const computeCar = async (l: Listing) => {
    if (l.lat == null || l.lon == null) return;
    const travel: Listing["travel"] = { ...(l.travel ?? {}) };
    for (const d of dests) travel[d.id] = { ...(travel[d.id] ?? {}), car: await carRoute(l.lat, l.lon, d.lat, d.lon) };
    setListings((ls) => ls.map((x) => (x.id === l.id ? { ...x, travel } : x)));
  };

  const exportCsv = () => {
    const header = ["titre", "type", "statut", "prix", "surface", "terrain", "pieces", "chambres", "dpe", "prix_m2", "ecart_mediane_pct", "adresse", "insee", "note", "url", ...dests.map((d) => `voiture_${d.name}`), ...dests.map((d) => `train_${d.name}`), "notes"];
    const rows = enriched.map(({ l, ppm2, vsMedian, train }) =>
      [
        l.title, l.type, STATUS_LABELS[l.status], l.price, l.surface, l.land, l.rooms, l.bedrooms, l.dpe,
        ppm2 ? Math.round(ppm2) : "", vsMedian != null ? Math.round(vsMedian) : "", l.address, l.insee, l.rating, l.url,
        ...dests.map((d) => (l.travel?.[d.id]?.car != null ? Math.round(l.travel[d.id].car!) : "")),
        ...dests.map((d) => (train[d.id] != null ? Math.round(train[d.id]!) : "")),
        l.notes,
      ]
        .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`)
        .join(";"),
    );
    download("mes_biens.csv", "﻿" + [header.join(";"), ...rows].join("\n"), "text/csv");
  };

  const importJson = (file: File) => {
    file.text().then((txt) => {
      try {
        const arr = JSON.parse(txt) as Listing[];
        if (!Array.isArray(arr)) throw new Error();
        setListings((ls) => {
          const ids = new Set(ls.map((x) => x.id));
          return [...ls, ...arr.filter((x) => x && x.id && !ids.has(x.id)).map((x) => emptyListing(x))];
        });
      } catch {
        alert("Fichier invalide (attendu : export JSON de « Mes biens »)");
      }
    });
  };

  if (editing) return <ListingForm initial={editing} onCancel={() => setEditing(null)} onSave={(l) => {
    setListings((ls) => (ls.some((x) => x.id === l.id) ? ls.map((x) => (x.id === l.id ? l : x)) : [l, ...ls]));
    setEditing(null);
  }} />;

  return (
    <div className="panel">
      <div className="row">
        <button className="primary" onClick={() => setEditing(emptyListing())}>
          ➕ Ajouter un bien
        </button>
        <span className="grow" />
        <button className="small" onClick={exportCsv} title="Exporter en CSV (tableur)">⬇ CSV</button>
        <button className="small" onClick={() => download("mes_biens.json", JSON.stringify(listings, null, 1))} title="Sauvegarde complète">
          ⬇ JSON
        </button>
        <label className="small btn-like" title="Importer une sauvegarde JSON">
          ⬆ <input type="file" accept=".json" hidden onChange={(e) => e.target.files?.[0] && importJson(e.target.files[0])} />
        </label>
      </div>
      <details className="group">
        <summary>Filtrer mes biens ({shown.length}/{listings.length})</summary>
        <div className="cats">
          {(Object.keys(TYPE_LABELS) as ListingType[]).map((t) => (
            <label key={t} className="small">
              <input type="checkbox" checked={f.types.includes(t)} onChange={(e) => setF({ ...f, types: e.target.checked ? [...f.types, t] : f.types.filter((x) => x !== t) })} />
              {TYPE_LABELS[t]}
            </label>
          ))}
        </div>
        <div className="cats">
          {(Object.keys(STATUS_LABELS) as ListingStatus[]).map((s) => (
            <label key={s} className="small">
              <input type="checkbox" checked={f.statuses.includes(s)} onChange={(e) => setF({ ...f, statuses: e.target.checked ? [...f.statuses, s] : f.statuses.filter((x) => x !== s) })} />
              <span style={{ color: STATUS_COLORS[s] }}>●</span> {STATUS_LABELS[s]}
            </label>
          ))}
        </div>
        <div className="row">
          <input type="number" className="num" placeholder="Prix max €" value={f.maxPrice ?? ""} onChange={(e) => setF({ ...f, maxPrice: e.target.value ? +e.target.value : null })} />
          <input type="number" className="num" placeholder="Surf. min m²" value={f.minSurface ?? ""} onChange={(e) => setF({ ...f, minSurface: e.target.value ? +e.target.value : null })} />
          <input type="number" className="num" placeholder="Terrain min" value={f.minLand ?? ""} onChange={(e) => setF({ ...f, minLand: e.target.value ? +e.target.value : null })} />
        </div>
        <div className="row">
          <label>DPE max</label>
          <select value={f.maxDpe} onChange={(e) => setF({ ...f, maxDpe: e.target.value })}>
            <option value="">indifférent</option>
            {DPE.map((d) => <option key={d}>{d}</option>)}
          </select>
          <label className="ml small">
            <input type="checkbox" checked={f.inZone} onChange={(e) => setF({ ...f, inZone: e.target.checked })} /> seulement dans les communes retenues
          </label>
        </div>
      </details>
      <div className="row">
        <label>Trier</label>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="created">plus récents</option>
          <option value="price">prix</option>
          <option value="ppm2">prix au m²</option>
          <option value="vsMedian">écart au prix du marché</option>
          <option value="rating">ma note</option>
          <option value="score">score de la commune</option>
        </select>
      </div>
      {listings.length === 0 && (
        <div className="empty">
          Ajoutez les annonces qui vous intéressent (copiez-collez le texte de l'annonce : prix, surface, pièces, DPE… sont détectés
          automatiquement). Chaque bien est comparé au prix médian de sa commune et ses temps de trajet sont calculés.
        </div>
      )}
      {shown.map(({ l, ci, ppm2, median, vsMedian, train }) => (
        <div key={l.id} className="card listing" style={{ borderLeftColor: STATUS_COLORS[l.status] }}>
          <div className="row">
            <b className="grow clickable" onClick={() => onLocate(l)} title="Voir sur la carte">
              {l.title || l.address || "(sans titre)"}
            </b>
            <select className="small" value={l.status} onChange={(e) => setListings((ls) => ls.map((x) => (x.id === l.id ? { ...x, status: e.target.value as ListingStatus } : x)))}>
              {(Object.keys(STATUS_LABELS) as ListingStatus[]).map((s) => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}
            </select>
          </div>
          <div className="small">
            {TYPE_LABELS[l.type]} · <b>{l.price ? `${l.price.toLocaleString("fr-FR")} €` : "prix ?"}</b>
            {l.surface ? ` · ${l.surface} m²` : ""}
            {l.land ? ` · terrain ${l.land.toLocaleString("fr-FR")} m²` : ""}
            {l.rooms ? ` · ${l.rooms} p.` : ""}
            {l.bedrooms ? ` · ${l.bedrooms} ch.` : ""}
            {l.dpe ? <span className={`dpe dpe-${l.dpe}`}>{l.dpe}</span> : null}
          </div>
          <div className="small muted">
            {ci != null ? `${data.noms[ci]} (${data.deps[ci]})` : l.address || "adresse inconnue"}
            {ci != null && <> · commune <ScoreBadge v={evaluation.score[ci]} /> {evaluation.pass[ci] ? "✓" : "✕ hors critères"}</>}
          </div>
          {ppm2 && (
            <div className="small">
              {Math.round(ppm2).toLocaleString("fr-FR")} €/m²
              {median ? (
                <span className={vsMedian! > 10 ? "err" : vsMedian! < -10 ? "ok" : ""}>
                  {" "}({vsMedian! > 0 ? "+" : ""}{Math.round(vsMedian!)} % vs médiane commune {Math.round(median).toLocaleString("fr-FR")} €/m²)
                </span>
              ) : null}
            </div>
          )}
          {dests.length > 0 && l.lat != null && (
            <div className="small">
              {dests.map((d) => (
                <span key={d.id} className="trip-chip">
                  <span style={{ color: d.color }}>★</span>
                  {d.name} : {l.travel?.[d.id]?.car != null ? `🚗 ${fmtMinutes(l.travel[d.id].car)}` : ""} {train[d.id] != null ? `🚆 ${fmtMinutes(train[d.id])}` : d.mode !== "car" ? "🚆 —" : ""}
                </span>
              ))}
              <button className="link small" onClick={() => computeCar(l)} title="Calcul d'itinéraire voiture exact (IGN)">
                calculer 🚗
              </button>
            </div>
          )}
          {l.features.length > 0 && <div className="small muted">{l.features.join(" · ")}</div>}
          {l.notes && <div className="small notes">{l.notes}</div>}
          <div className="row small">
            <span className="stars">
              {[1, 2, 3, 4, 5].map((s) => (
                <button key={s} className={l.rating >= s ? "on" : ""} onClick={() => setListings((ls) => ls.map((x) => (x.id === l.id ? { ...x, rating: x.rating === s ? s - 1 : s } : x)))}>
                  ★
                </button>
              ))}
            </span>
            <span className="grow" />
            {l.url && <a href={l.url} target="_blank" rel="noreferrer">annonce ↗</a>}
            <button className="link" onClick={() => setEditing(l)}>modifier</button>
            <button className="link" onClick={() => confirm("Supprimer ce bien ?") && setListings((ls) => ls.filter((x) => x.id !== l.id))}>supprimer</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function ListingForm({ initial, onSave, onCancel }: { initial: Listing; onSave: (l: Listing) => void; onCancel: () => void }) {
  const [l, setL] = useState<Listing>(initial);
  const [paste, setPaste] = useState("");
  const set = (p: Partial<Listing>) => setL((x) => ({ ...x, ...p }));
  const numIn = (k: keyof Listing, ph: string) => (
    <input type="number" className="num" placeholder={ph} value={(l[k] as number | null) ?? ""} onChange={(e) => set({ [k]: e.target.value === "" ? null : +e.target.value } as Partial<Listing>)} />
  );
  return (
    <div className="panel">
      <h3>{initial.title || initial.address ? "Modifier le bien" : "Nouveau bien"}</h3>
      <textarea
        rows={4}
        placeholder="Collez ici le texte de l'annonce (et son lien) : prix, surface, terrain, pièces, DPE… seront détectés."
        value={paste}
        onChange={(e) => setPaste(e.target.value)}
      />
      <button className="small" disabled={!paste} onClick={() => setL((x) => ({ ...x, ...Object.fromEntries(Object.entries(parseListingText(paste)).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length))) }))}>
        ✨ Remplir depuis le texte
      </button>
      <label className="lbl">Titre</label>
      <input value={l.title} onChange={(e) => set({ title: e.target.value })} />
      <label className="lbl">Lien de l'annonce</label>
      <input value={l.url} placeholder="https://…" onChange={(e) => set({ url: e.target.value })} />
      <label className="lbl">Adresse (ou commune)</label>
      <AddressSearch
        initial={l.address}
        placeholder="Adresse ou commune du bien"
        onSelect={(r) => set({ address: r.label, lat: r.lat, lon: r.lon, insee: r.citycode.length === 5 ? (r.citycode.startsWith("751") ? "75056" : r.citycode.startsWith("132") ? "13055" : r.citycode.startsWith("6938") ? "69123" : r.citycode) : "" })}
      />
      <div className="row">
        <select value={l.type} onChange={(e) => set({ type: e.target.value as ListingType })}>
          {(Object.keys(TYPE_LABELS) as ListingType[]).map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
        </select>
        {numIn("price", "Prix €")}
      </div>
      <div className="row">
        {numIn("surface", "Surface m²")}
        {numIn("land", "Terrain m²")}
      </div>
      <div className="row">
        {numIn("rooms", "Pièces")}
        {numIn("bedrooms", "Chambres")}
        <select value={l.dpe} onChange={(e) => set({ dpe: e.target.value })}>
          <option value="">DPE ?</option>
          {DPE.map((d) => <option key={d}>{d}</option>)}
        </select>
      </div>
      <div className="cats">
        {FEATURES.map((ft) => (
          <label key={ft} className="small">
            <input type="checkbox" checked={l.features.includes(ft)} onChange={(e) => set({ features: e.target.checked ? [...l.features, ft] : l.features.filter((x) => x !== ft) })} />
            {ft}
          </label>
        ))}
      </div>
      <label className="lbl">Notes</label>
      <textarea rows={3} value={l.notes} onChange={(e) => set({ notes: e.target.value })} placeholder="Impressions de visite, travaux, voisinage…" />
      <div className="row">
        <button className="primary" onClick={() => onSave(l)} disabled={!l.title && !l.address}>
          Enregistrer
        </button>
        <button onClick={onCancel}>Annuler</button>
        {l.lat == null && <span className="muted small">sans adresse, le bien n'apparaîtra pas sur la carte</span>}
      </div>
    </div>
  );
}
