import { useEffect, useRef, useState } from "react";

export interface GeoResult {
  label: string;
  name: string;
  lat: number;
  lon: number;
  citycode: string;
  city: string;
}

/** Géocodage BAN via la Géoplateforme IGN. */
export async function geocode(q: string, limit = 6): Promise<GeoResult[]> {
  if (q.trim().length < 3) return [];
  const r = await fetch(`https://data.geopf.fr/geocodage/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!r.ok) return [];
  const j = await r.json();
  return (j.features ?? []).map((f: any) => ({
    label: f.properties.label,
    name: f.properties.name ?? f.properties.label,
    lat: f.geometry.coordinates[1],
    lon: f.geometry.coordinates[0],
    citycode: f.properties.citycode ?? "",
    city: f.properties.city ?? "",
  }));
}

export function AddressSearch({
  placeholder,
  onSelect,
  initial = "",
}: {
  placeholder?: string;
  onSelect: (r: GeoResult) => void;
  initial?: string;
}) {
  const [q, setQ] = useState(initial);
  const [res, setRes] = useState<GeoResult[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<number>();
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!open) return;
    timer.current = window.setTimeout(() => {
      geocode(q).then(setRes).catch(() => setRes([]));
    }, 250);
  }, [q, open]);
  return (
    <div className="addr">
      <input
        value={q}
        placeholder={placeholder ?? "Adresse, lieu, gare…"}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
      />
      {open && res.length > 0 && (
        <ul className="addr-list">
          {res.map((r, i) => (
            <li
              key={i}
              onMouseDown={() => {
                onSelect(r);
                setQ(r.label);
                setOpen(false);
              }}
            >
              {r.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
