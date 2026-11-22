import { useEffect, useRef } from "react";
import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { feature } from "topojson-client";
import type { CommunesData, Destination, Listing, Settings, TransitData } from "../data/types";
import { F_TRAIN } from "../data/types";
import type { Evaluation, TravelTimes } from "../engine/score";
import { fmtValue, ranks } from "../engine/score";
import type { TrainResult } from "../engine/train";
import type { CarResult } from "../engine/car";
import { fmtClock, fmtMinutes } from "../engine/geo";

// palette séquentielle (mauvais → bon)
const PALETTE = ["#b2182b", "#e0603f", "#f4a259", "#f7d774", "#c4e17a", "#7cc46a", "#2f9e57", "#11693b"];
export function paletteColor(t: number): string {
  if (!isFinite(t)) return "#9aa0a6";
  const x = Math.max(0, Math.min(1, t)) * (PALETTE.length - 1);
  return PALETTE[Math.round(x)];
}
export const LEGEND = PALETTE;

export const STATUS_COLORS: Record<string, string> = {
  repere: "#2e86ab",
  a_visiter: "#f2a541",
  visite: "#8f3fb5",
  offre: "#17a673",
  ecarte: "#888888",
};

const IGN_WMTS = (layer: string, fmt: string) =>
  `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=${layer}&STYLE=normal&TILEMATRIXSET=PM&FORMAT=${fmt}&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}`;

// Fond vectoriel OpenFreeMap (gratuit, sans clé) + fonds raster IGN ajoutés au chargement
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const RASTERS: Record<string, { tiles: string[]; attribution: string }> = {
  ign: { tiles: [IGN_WMTS("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", "image/png")], attribution: "© IGN Géoplateforme" },
  ortho: { tiles: [IGN_WMTS("ORTHOIMAGERY.ORTHOPHOTOS", "image/jpeg")], attribution: "© IGN Géoplateforme" },
};

export interface MapProps {
  data: CommunesData;
  geometry: any;
  evaluation: Evaluation;
  colorValues: { values: Float32Array; label: string; fmt: (i: number) => string };
  travel: TravelTimes[];
  destinations: Destination[];
  carResults: Record<string, CarResult>;
  trainResults: Record<string, TrainResult>;
  transit: TransitData | null;
  listings: Listing[];
  settings: Settings;
  selected: number | null;
  onSelect: (i: number | null) => void;
  onSelectListing: (id: string) => void;
  flyTo: { lat: number; lon: number; zoom?: number; t: number } | null;
}

export function MapView(p: MapProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const readyRef = useRef(false);
  const propsRef = useRef(p);
  propsRef.current = p;
  const codeIndex = useRef<Map<string, number>>(new Map());
  const markers = useRef<maplibregl.Marker[]>([]);
  const prevSelected = useRef<string | null>(null);

  // --- initialisation ---
  useEffect(() => {
    const map = new maplibregl.Map({
      container: ref.current!,
      style: STYLE_URL,
      center: [2.4, 46.6],
      zoom: 5.2,
      minZoom: 3,
      maxZoom: 17,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    p.data.codes.forEach((c, i) => codeIndex.current.set(c, i));

    map.on("load", () => {
      // premier calque de libellés : nos calques de remplissage passent dessous
      const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
      for (const [id, r] of Object.entries(RASTERS)) {
        map.addSource(id, { type: "raster", tiles: r.tiles, tileSize: 256, maxzoom: 19, attribution: r.attribution });
        map.addLayer({ id, type: "raster", source: id, layout: { visibility: "none" } }, firstSymbol);
      }
      const topo = p.geometry;
      const fc = feature(topo, topo.objects[Object.keys(topo.objects)[0]]) as unknown as GeoJSON.FeatureCollection;
      map.addSource("communes", { type: "geojson", data: fc, promoteId: "c", tolerance: 0.3 });
      map.addLayer({
        id: "communes-fill",
        type: "fill",
        source: "communes",
        paint: {
          "fill-color": ["coalesce", ["feature-state", "color"], "rgba(0,0,0,0)"],
          "fill-opacity": [
            "case",
            ["boolean", ["feature-state", "fail"], false],
            ["case", ["boolean", ["feature-state", "showFail"], true], 0.1, 0],
            0.62,
          ],
        },
      });
      map.addLayer({
        id: "communes-line",
        type: "line",
        source: "communes",
        minzoom: 7.5,
        paint: { "line-color": "#555", "line-opacity": 0.25, "line-width": 0.5 },
      });
      map.addLayer({
        id: "communes-hover",
        type: "line",
        source: "communes",
        paint: {
          "line-color": ["case", ["boolean", ["feature-state", "selected"], false], "#111", "#333"],
          "line-width": [
            "case",
            ["boolean", ["feature-state", "selected"], false],
            3,
            ["boolean", ["feature-state", "hover"], false],
            1.5,
            0,
          ],
        },
      });
      if (firstSymbol) for (const id of ["communes-fill", "communes-line", "communes-hover"]) map.moveLayer(id, firstSymbol);

      map.addSource("iso", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "iso-line",
        type: "line",
        source: "iso",
        paint: {
          "line-color": ["get", "color"],
          "line-width": ["case", ["get", "outer"], 2.5, 0.8],
          "line-opacity": ["case", ["get", "outer"], 0.95, 0.5],
          "line-dasharray": [3, 1.5],
        },
      });
      map.addSource("stations", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "stations",
        type: "circle",
        source: "stations",
        minzoom: 5,
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 2, 9, 4.5, 13, 7],
          "circle-color": ["get", "color"],
          "circle-stroke-color": "#fff",
          "circle-stroke-width": 1,
        },
      });
      map.addSource("listings", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "listings",
        type: "circle",
        source: "listings",
        paint: {
          "circle-radius": 7,
          "circle-color": ["get", "color"],
          "circle-stroke-color": "#111",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "listings-label",
        type: "symbol",
        source: "listings",
        minzoom: 9,
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.3], "text-anchor": "top", "text-font": ["Noto Sans Regular"] },
        paint: { "text-halo-color": "#fff", "text-halo-width": 1.5 },
      });

      // --- interactions ---
      let hovered: string | null = null;
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, maxWidth: "320px", className: "tip" });
      map.on("mousemove", (e) => {
        const feats = map.queryRenderedFeatures(e.point, { layers: ["listings", "stations", "communes-fill"] });
        const f = feats[0];
        map.getCanvas().style.cursor = f && f.layer.id !== "communes-fill" ? "pointer" : f ? "crosshair" : "";
        const code = feats.find((x) => x.layer.id === "communes-fill")?.properties?.c as string | undefined;
        if (hovered !== (code ?? null)) {
          if (hovered) map.setFeatureState({ source: "communes", id: hovered }, { hover: false });
          hovered = code ?? null;
          if (hovered) map.setFeatureState({ source: "communes", id: hovered }, { hover: true });
        }
        if (!f) {
          popup.remove();
          return;
        }
        let html = "";
        if (f.layer.id === "listings") {
          html = `<b>${esc(f.properties!.title)}</b><br>${esc(f.properties!.sub)}`;
        } else if (f.layer.id === "stations") {
          html = `<b>🚆 ${esc(f.properties!.name)}</b><br>${f.properties!.info}`;
        } else if (code) {
          const i = codeIndex.current.get(code);
          if (i == null) return;
          html = tooltipHtml(propsRef.current, i);
        }
        popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
      });
      map.on("mouseout", () => popup.remove());
      map.on("click", (e) => {
        const feats = map.queryRenderedFeatures(e.point, { layers: ["listings", "communes-fill"] });
        const lf = feats.find((x) => x.layer.id === "listings");
        if (lf) {
          propsRef.current.onSelectListing(lf.properties!.id);
          return;
        }
        const code = feats.find((x) => x.layer.id === "communes-fill")?.properties?.c as string | undefined;
        propsRef.current.onSelect(code != null ? codeIndex.current.get(code) ?? null : null);
      });

      readyRef.current = true;
      map.fire("appready");
    });
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const whenReady = (fn: (m: MLMap) => void) => {
    const m = mapRef.current;
    if (!m) return;
    if (readyRef.current) fn(m);
    else m.once("appready" as any, () => fn(m));
  };

  // --- coloration des communes ---
  useEffect(() => {
    whenReady((map) => {
      const { data, evaluation, colorValues, settings } = p;
      const n = data.codes.length;
      for (let i = 0; i < n; i++) {
        const fail = !evaluation.pass[i];
        const v = colorValues.values[i];
        map.setFeatureState(
          { source: "communes", id: data.codes[i] },
          {
            // pas de score (aucun critère pondéré) : on ne colore que les communes exclues ;
            // donnée manquante pour un indicateur : gris clair
            color: fail ? "#7a7a7a" : isFinite(v) ? paletteColor(v) : colorValues.label === "Score" ? "rgba(0,0,0,0)" : "#d0d0d0",
            fail,
            showFail: settings.showFailing,
          },
        );
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.evaluation, p.colorValues, p.settings.showFailing]);

  // --- sélection ---
  useEffect(() => {
    whenReady((map) => {
      if (prevSelected.current) map.setFeatureState({ source: "communes", id: prevSelected.current }, { selected: false });
      prevSelected.current = p.selected != null ? p.data.codes[p.selected] : null;
      if (prevSelected.current) map.setFeatureState({ source: "communes", id: prevSelected.current }, { selected: true });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.selected]);

  // --- fond de carte ---
  useEffect(() => {
    whenReady((map) => {
      for (const b of ["ign", "ortho"]) map.setLayoutProperty(b, "visibility", p.settings.basemap === b ? "visible" : "none");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.settings.basemap]);

  // --- isochrones voiture + marqueurs destinations ---
  useEffect(() => {
    whenReady((map) => {
      const feats: GeoJSON.Feature[] = [];
      for (const d of p.destinations) {
        if (!d.enabled || d.mode === "train") continue;
        const cr = p.carResults[d.id];
        if (!cr) continue;
        cr.polygons.forEach((pg, k) =>
          feats.push({
            type: "Feature",
            geometry: pg.geometry,
            properties: { color: d.color, minutes: pg.minutes, outer: k === cr.polygons.length - 1 },
          }),
        );
      }
      (map.getSource("iso") as GeoJSONSource).setData({ type: "FeatureCollection", features: feats });
      markers.current.forEach((m) => m.remove());
      markers.current = p.destinations
        .filter((d) => d.enabled)
        .map((d) => {
          const el = document.createElement("div");
          el.className = "dest-marker";
          el.style.background = d.color;
          el.title = `${d.name} — ${d.label}`;
          el.textContent = "★";
          return new maplibregl.Marker({ element: el }).setLngLat([d.lon, d.lat]).addTo(map);
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.destinations, p.carResults]);

  // --- gares (temps train) ---
  useEffect(() => {
    whenReady((map) => {
      const feats: GeoJSON.Feature[] = [];
      const t = p.transit;
      const dests = p.destinations.filter((d) => d.enabled && d.mode !== "car" && p.trainResults[d.id]);
      if (t && p.settings.showStations && dests.length) {
        t.stations.forEach((s, i) => {
          if (!(s[3] & F_TRAIN)) return;
          let worst = 0;
          const lines: string[] = [];
          for (const d of dests) {
            const r = p.trainResults[d.id];
            const m = r.dur[i];
            const rel = isFinite(m) ? m / d.maxMinutes : Infinity;
            worst = Math.max(worst, rel);
            lines.push(
              isFinite(m)
                ? `→ ${esc(d.name)} : <b>${fmtMinutes(m)}</b> (dép. ${fmtClock(r.depTime[i])}, arr. ≤ ${fmtClock(r.arrTime[i])})`
                : `→ ${esc(d.name)} : injoignable`,
            );
          }
          if (worst > 1.5) return;
          feats.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: [s[2], s[1]] },
            properties: {
              name: s[0],
              info: lines.join("<br>") + `<br><span class="muted">${s[4]} passages/jour</span>`,
              color: worst <= 1 ? paletteColor(1 - worst * 0.85) : "#9aa0a6",
            },
          });
        });
      }
      (map.getSource("stations") as GeoJSONSource).setData({ type: "FeatureCollection", features: feats });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.trainResults, p.destinations, p.transit, p.settings.showStations]);

  // --- annonces ---
  useEffect(() => {
    whenReady((map) => {
      const feats: GeoJSON.Feature[] = p.listings
        .filter((l) => l.lat != null && l.lon != null)
        .map((l) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [l.lon!, l.lat!] },
          properties: {
            id: l.id,
            title: l.title || l.address,
            sub: [l.price ? `${l.price.toLocaleString("fr-FR")} €` : "", l.surface ? `${l.surface} m²` : "", l.type]
              .filter(Boolean)
              .join(" · "),
            label: l.price ? `${Math.round(l.price / 1000)} k€` : "",
            color: STATUS_COLORS[l.status] ?? "#2e86ab",
          },
        }));
      (map.getSource("listings") as GeoJSONSource).setData({ type: "FeatureCollection", features: feats });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.listings]);

  useEffect(() => {
    if (p.flyTo) mapRef.current?.flyTo({ center: [p.flyTo.lon, p.flyTo.lat], zoom: p.flyTo.zoom ?? 11, speed: 1.6 });
  }, [p.flyTo]);

  return <div ref={ref} className="map" />;
}

function esc(s: unknown) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

function tooltipHtml(p: MapProps, i: number): string {
  const { data, evaluation, colorValues, destinations, travel } = p;
  const lines = [`<b>${esc(data.noms[i])}</b> <span class="muted">(${data.deps[i]})</span>`];
  if (!evaluation.pass[i]) lines.push(`<span class="err">✕ hors critères</span>`);
  const sc = evaluation.score[i];
  if (isFinite(sc)) lines.push(`Score : <b>${Math.round(sc)}</b>/100`);
  if (colorValues.label !== "Score") lines.push(`${esc(colorValues.label)} : <b>${colorValues.fmt(i)}</b>`);
  for (const d of destinations) {
    if (!d.enabled) continue;
    const tt = travel.find((t) => t.destId === d.id);
    if (!tt) continue;
    const parts: string[] = [];
    if (tt.car) parts.push(`🚗 ${fmtMinutes(tt.car[i])}`);
    if (tt.train) parts.push(`🚆 ${fmtMinutes(tt.train[i])}`);
    lines.push(`<span style="color:${d.color}">★</span> ${esc(d.name)} : ${parts.join(" · ")}`);
  }
  const pm = data.indicators.find((x) => x.id === "prix_m2_maison");
  if (pm) lines.push(`Maison : ${fmtValue(pm, data.values.prix_m2_maison[i])}`);
  return lines.join("<br>");
}

export function rankColorValues(data: CommunesData, id: string, prefer: "higher" | "lower" | null) {
  const r = ranks(data, id);
  const out = new Float32Array(r.length);
  for (let i = 0; i < r.length; i++) out[i] = prefer === "lower" ? 1 - r[i] : r[i];
  return out;
}
