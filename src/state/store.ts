import { useEffect, useState } from "react";
import type { AppConfig, Destination, Listing } from "../data/types";

const CONFIG_KEY = "immo.config.v1";
const LISTINGS_KEY = "immo.listings.v1";

export const DEST_COLORS = ["#e4572e", "#2e86ab", "#8f3fb5", "#17a673", "#f2a541", "#d6336c"];

export const DEFAULT_CONFIG: AppConfig = {
  destinations: [],
  filters: {},
  settings: {
    colorBy: "score",
    showFailing: true,
    departements: "",
    basemap: "positron",
    showStations: true,
  },
};

function read<T>(key: string, fallback: T): T {
  try {
    const s = localStorage.getItem(key);
    if (!s) return fallback;
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

function write(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* stockage indisponible : on continue sans persister */
  }
}

export function usePersistent<T>(key: string, fallback: T): [T, (v: T | ((p: T) => T)) => void] {
  const [v, setV] = useState<T>(() => read(key, fallback));
  useEffect(() => write(key, v), [key, v]);
  return [v, setV];
}

export function useConfig() {
  const [cfg, setCfg] = usePersistent<AppConfig>(CONFIG_KEY, DEFAULT_CONFIG);
  // migration douce : réglages ajoutés après coup
  const merged: AppConfig = { ...DEFAULT_CONFIG, ...cfg, settings: { ...DEFAULT_CONFIG.settings, ...cfg.settings } };
  return [merged, setCfg] as const;
}

export function useListings() {
  return usePersistent<Listing[]>(LISTINGS_KEY, []);
}

export function uid() {
  return Math.random().toString(36).slice(2, 10);
}

export function newDestination(existing: Destination[], p: { name: string; label: string; lat: number; lon: number }): Destination {
  return {
    id: uid(),
    name: p.name,
    label: p.label,
    lat: p.lat,
    lon: p.lon,
    mode: "best",
    maxMinutes: 60,
    required: true,
    weight: 2,
    arriveBy: 9 * 60,
    window: 60,
    access: "car",
    maxAccess: 20,
    color: DEST_COLORS[existing.length % DEST_COLORS.length],
    enabled: true,
  };
}

export function download(filename: string, content: string, mime = "application/json") {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
