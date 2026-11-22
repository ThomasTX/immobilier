import { useEffect, useMemo, useRef, useState } from "react";
import type { CommunesData, Listing, TransitData } from "./data/types";
import { loadCommunes, loadGeometry, loadTransit } from "./data/load";
import { evaluate, fmtValue, type TravelTimes } from "./engine/score";
import { computeCar, type CarResult } from "./engine/car";
import { computeTrain, trainForCommunes, type TrainResult } from "./engine/train";
import { fmtMinutes } from "./engine/geo";
import { download, useConfig, useListings } from "./state/store";
import { MapView, LEGEND, rankColorValues } from "./components/MapView";
import { DestinationsPanel, type DestStatus } from "./components/DestinationsPanel";
import { CriteriaPanel } from "./components/CriteriaPanel";
import { ResultsPanel } from "./components/ResultsPanel";
import { CommuneDetail } from "./components/CommuneDetail";
import { ListingsPanel, emptyListing } from "./components/ListingsPanel";

type Tab = "trajets" | "criteres" | "communes" | "biens";

export default function App() {
  const [data, setData] = useState<CommunesData | null>(null);
  const [geometry, setGeometry] = useState<unknown>(null);
  const [transit, setTransit] = useState<TransitData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cfg, setCfg] = useConfig();
  const [listings, setListings] = useListings();
  const [tab, setTab] = useState<Tab>("trajets");
  const [selected, setSelected] = useState<number | null>(null);
  const [editing, setEditing] = useState<Listing | null>(null);
  const [flyTo, setFlyTo] = useState<{ lat: number; lon: number; zoom?: number; t: number } | null>(null);
  const [carResults, setCarResults] = useState<Record<string, CarResult>>({});
  const [trainResults, setTrainResults] = useState<Record<string, TrainResult>>({});
  const [trainCommunes, setTrainCommunes] = useState<Record<string, { minutes: Float32Array; station: Int32Array }>>({});
  const [status, setStatus] = useState<Record<string, DestStatus>>({});

  useEffect(() => {
    Promise.all([loadCommunes(), loadGeometry()])
      .then(([d, g]) => {
        setData(d);
        setGeometry(g);
      })
      .catch((e) => setError(String(e.message ?? e)));
    loadTransit()
      .then(setTransit)
      .catch((e) => console.warn("Réseau ferré indisponible", e));
  }, []);

  // ---- calcul voiture (asynchrone, IGN) ----
  const carKeys = cfg.destinations
    .filter((d) => d.enabled && d.mode !== "train")
    .map((d) => `${d.id}|${d.lat}|${d.lon}|${d.maxMinutes}`)
    .join(";");
  useEffect(() => {
    if (!data) return;
    let cancelled = false;
    const t = window.setTimeout(() => {
      for (const d of cfg.destinations) {
        if (!d.enabled || d.mode === "train") continue;
        const key = `${d.lat}|${d.lon}|${d.maxMinutes}`;
        if ((carResults[d.id] as any)?._key === key) continue;
        setStatus((s) => ({ ...s, [d.id]: { ...s[d.id], loading: true, error: undefined } }));
        computeCar(d.lat, d.lon, d.maxMinutes, data.lat, data.lon)
          .then((r) => {
            if (cancelled) return;
            (r as any)._key = key;
            setCarResults((c) => ({ ...c, [d.id]: r }));
            setStatus((s) => ({ ...s, [d.id]: { ...s[d.id], loading: false } }));
          })
          .catch((e) => {
            if (cancelled) return;
            setStatus((s) => ({ ...s, [d.id]: { loading: false, error: `voiture : ${e.message ?? e}` } }));
          });
      }
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [carKeys, data]);

  // ---- calcul train (synchrone, local) ----
  const trainKeys = cfg.destinations
    .filter((d) => d.enabled && d.mode !== "car")
    .map((d) => `${d.id}|${d.lat}|${d.lon}|${d.arriveBy}|${d.window}|${d.access}|${d.maxAccess}`)
    .join(";");
  useEffect(() => {
    if (!data || !transit) return;
    const t = window.setTimeout(() => {
      const tr: Record<string, TrainResult> = {};
      const tc: Record<string, { minutes: Float32Array; station: Int32Array }> = {};
      for (const d of cfg.destinations) {
        if (!d.enabled || d.mode === "car") continue;
        tr[d.id] = computeTrain(transit, d);
        tc[d.id] = trainForCommunes(transit, tr[d.id], data.lat, data.lon, d.access, d.maxAccess);
      }
      setTrainResults(tr);
      setTrainCommunes(tc);
    }, 150);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trainKeys, data, transit]);

  // ---- temps combinés par destination ----
  const travel: TravelTimes[] = useMemo(() => {
    if (!data) return [];
    const out: TravelTimes[] = [];
    for (const d of cfg.destinations) {
      if (!d.enabled) continue;
      const car = d.mode !== "train" ? carResults[d.id]?.minutes : undefined;
      const train = d.mode !== "car" ? trainCommunes[d.id]?.minutes : undefined;
      if ((d.mode === "car" && !car) || (d.mode === "train" && !train) || (d.mode === "best" && !car && !train)) continue;
      let minutes: Float32Array;
      if (car && train) {
        minutes = new Float32Array(car.length);
        for (let i = 0; i < car.length; i++) minutes[i] = Math.min(car[i], train[i]);
      } else minutes = (car ?? train)!;
      out.push({ destId: d.id, minutes, car, train, trainStation: trainCommunes[d.id]?.station });
    }
    return out;
  }, [data, cfg.destinations, carResults, trainCommunes]);

  useEffect(() => {
    for (const t of travel) {
      const d = cfg.destinations.find((x) => x.id === t.destId);
      if (!d) continue;
      let n = 0;
      for (let i = 0; i < t.minutes.length; i++) if (t.minutes[i] <= d.maxMinutes) n++;
      setStatus((s) => (s[d.id]?.reachable === n ? s : { ...s, [d.id]: { ...s[d.id], reachable: n } }));
    }
  }, [travel, cfg.destinations]);

  const evaluation = useMemo(
    () => (data ? evaluate(data, cfg.filters, cfg.destinations, travel, cfg.settings.departements) : null),
    [data, cfg.filters, cfg.destinations, travel, cfg.settings.departements],
  );

  // ---- valeurs de coloration ----
  const colorValues = useMemo(() => {
    if (!data || !evaluation) return null;
    const cb = cfg.settings.colorBy;
    if (cb.startsWith("time:")) {
      const t = travel.find((x) => x.destId === cb.slice(5));
      const d = cfg.destinations.find((x) => x.id === cb.slice(5));
      if (t && d) {
        const v = new Float32Array(t.minutes.length);
        for (let i = 0; i < v.length; i++) v[i] = isFinite(t.minutes[i]) ? 1 - Math.min(1, t.minutes[i] / (d.maxMinutes * 1.2)) : NaN;
        return { values: v, label: `Trajet → ${d.name}`, fmt: (i: number) => fmtMinutes(t.minutes[i]) };
      }
    }
    const ind = data.indicators.find((x) => x.id === cb);
    if (ind) {
      const pref = cfg.filters[ind.id]?.prefer ?? ind.better;
      return { values: rankColorValues(data, ind.id, pref), label: ind.label, fmt: (i: number) => fmtValue(ind, data.values[ind.id][i]) };
    }
    const v = new Float32Array(evaluation.score.length);
    for (let i = 0; i < v.length; i++) v[i] = isFinite(evaluation.score[i]) ? evaluation.score[i] / 100 : NaN;
    return { values: v, label: "Score", fmt: (i: number) => (isFinite(evaluation.score[i]) ? `${Math.round(evaluation.score[i])}` : "—") };
  }, [data, evaluation, cfg.settings.colorBy, travel, cfg.destinations, cfg.filters]);

  const importRef = useRef<HTMLInputElement>(null);

  if (error)
    return (
      <div className="loading">
        <h2>Impossible de charger les données</h2>
        <p>{error}</p>
      </div>
    );
  if (!data || !geometry || !evaluation || !colorValues)
    return (
      <div className="loading">
        <div className="spinner" />
        <p>Chargement des 35 000 communes…</p>
      </div>
    );

  const setSettings = (p: Partial<typeof cfg.settings>) => setCfg({ ...cfg, settings: { ...cfg.settings, ...p } });
  const anyScore = Object.values(cfg.filters).some((f) => f.weight > 0) || cfg.destinations.some((d) => d.enabled && d.weight > 0);

  return (
    <div className="app">
      <aside className="sidebar">
        <header>
          <h1>🏡 Où acheter ?</h1>
          <span className="muted small">{evaluation.nPass.toLocaleString("fr-FR")} / {data.codes.length.toLocaleString("fr-FR")} communes</span>
        </header>
        <nav className="tabs">
          {(
            [
              ["trajets", `Trajets${cfg.destinations.length ? ` (${cfg.destinations.length})` : ""}`],
              ["criteres", "Critères"],
              ["communes", "Communes"],
              ["biens", `Mes biens${listings.length ? ` (${listings.length})` : ""}`],
            ] as [Tab, string][]
          ).map(([t, l]) => (
            <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {l}
            </button>
          ))}
        </nav>
        <div className="tab-body">
          {tab === "trajets" && (
            <DestinationsPanel
              destinations={cfg.destinations}
              onChange={(d) => {
                const added = d.find((x) => !cfg.destinations.some((y) => y.id === x.id));
                if (added) setFlyTo({ lat: added.lat, lon: added.lon, zoom: 8, t: Date.now() });
                setCfg({ ...cfg, destinations: d });
              }}
              status={status}
              serviceDate={transit?.meta.service_date}
            />
          )}
          {tab === "criteres" && (
            <CriteriaPanel
              data={data}
              filters={cfg.filters}
              onChange={(f) => setCfg({ ...cfg, filters: f })}
              departements={cfg.settings.departements}
              onDepartements={(s) => setSettings({ departements: s })}
              evaluation={evaluation}
              destinations={cfg.destinations}
            />
          )}
          {tab === "communes" && (
            <ResultsPanel
              data={data}
              evaluation={evaluation}
              destinations={cfg.destinations}
              travel={travel}
              filters={cfg.filters}
              onSelect={(i) => {
                setSelected(i);
                setFlyTo({ lat: data.lat[i], lon: data.lon[i], zoom: 11, t: Date.now() });
              }}
            />
          )}
          {tab === "biens" && (
            <ListingsPanel
              listings={listings}
              setListings={setListings}
              data={data}
              evaluation={evaluation}
              destinations={cfg.destinations}
              trainResults={trainResults}
              transit={transit}
              editing={editing}
              setEditing={setEditing}
              onLocate={(l) => l.lat != null && setFlyTo({ lat: l.lat, lon: l.lon!, zoom: 14, t: Date.now() })}
            />
          )}
        </div>
        <footer className="small muted">
          <button className="link" onClick={() => download("recherche_immo_config.json", JSON.stringify(cfg, null, 1))}>
            Sauver ma recherche
          </button>
          {" · "}
          <button className="link" onClick={() => importRef.current?.click()}>
            Charger
          </button>
          <input
            ref={importRef}
            type="file"
            accept=".json"
            hidden
            onChange={(e) =>
              e.target.files?.[0]?.text().then((t) => {
                try {
                  setCfg(JSON.parse(t));
                } catch {
                  alert("Fichier de recherche invalide");
                }
              })
            }
          />
          {" · "}données au {data.generated}
        </footer>
      </aside>
      <main className="main">
        <MapView
          data={data}
          geometry={geometry}
          evaluation={evaluation}
          colorValues={colorValues}
          travel={travel}
          destinations={cfg.destinations}
          carResults={carResults}
          trainResults={trainResults}
          transit={transit}
          listings={listings}
          settings={cfg.settings}
          selected={selected}
          onSelect={setSelected}
          onSelectListing={(id) => {
            const l = listings.find((x) => x.id === id);
            if (l) {
              setTab("biens");
              setEditing(l);
            }
          }}
          flyTo={flyTo}
        />
        <div className="map-controls">
          <label className="small">Colorer par</label>
          <select value={cfg.settings.colorBy} onChange={(e) => setSettings({ colorBy: e.target.value })}>
            <option value="score">Score global{anyScore ? "" : " (aucun poids défini)"}</option>
            {cfg.destinations
              .filter((d) => travel.some((t) => t.destId === d.id))
              .map((d) => (
                <option key={d.id} value={`time:${d.id}`}>
                  Trajet → {d.name}
                </option>
              ))}
            {[...new Set(data.indicators.map((i) => i.group))].map((g) => (
              <optgroup key={g} label={g}>
                {data.indicators
                  .filter((i) => i.group === g && i.type !== "boolean")
                  .map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.label}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
          <div className="legend" title="Rouge = défavorable, vert = favorable (classement entre communes)">
            <span className="small">−</span>
            {LEGEND.map((c) => (
              <i key={c} style={{ background: c }} />
            ))}
            <span className="small">+</span>
          </div>
          <label className="small">
            <input type="checkbox" checked={cfg.settings.showFailing} onChange={(e) => setSettings({ showFailing: e.target.checked })} /> griser les exclues
          </label>
          <label className="small">
            <input type="checkbox" checked={cfg.settings.showStations} onChange={(e) => setSettings({ showStations: e.target.checked })} /> gares
          </label>
          <select className="small" value={cfg.settings.basemap} onChange={(e) => setSettings({ basemap: e.target.value as any })}>
            <option value="positron">Plan clair</option>
            <option value="ign">Plan IGN</option>
            <option value="ortho">Photos aériennes</option>
          </select>
        </div>
        {selected != null && (
          <CommuneDetail
            data={data}
            i={selected}
            evaluation={evaluation}
            destinations={cfg.destinations}
            travel={travel}
            trainResults={trainResults}
            transit={transit}
            filters={cfg.filters}
            onClose={() => setSelected(null)}
            onAddListing={() => {
              setTab("biens");
              setEditing(emptyListing({ insee: data.codes[selected], address: `${data.noms[selected]} (${data.cps[selected]})`, lat: data.lat[selected], lon: data.lon[selected] }));
            }}
          />
        )}
      </main>
    </div>
  );
}
