import type { AccessMode, Destination, TravelMode } from "../data/types";
import { fmtClock } from "../engine/geo";
import { newDestination } from "../state/store";
import { AddressSearch } from "./AddressSearch";

export interface DestStatus {
  loading?: boolean;
  error?: string;
  reachable?: number;
}

export function DestinationsPanel({
  destinations,
  onChange,
  status,
  serviceDate,
}: {
  destinations: Destination[];
  onChange: (d: Destination[]) => void;
  status: Record<string, DestStatus>;
  serviceDate?: string;
}) {
  const update = (id: string, patch: Partial<Destination>) =>
    onChange(destinations.map((d) => (d.id === id ? { ...d, ...patch } : d)));

  return (
    <div className="panel">
      <p className="hint">
        Ajoutez les lieux où vous devez vous rendre (travail, école, famille…) avec un temps de trajet maximum. La carte
        calcule les zones accessibles en <b>voiture</b> (isochrones IGN) et en <b>train</b> (horaires SNCF + RER/Transilien
        d'un mardi type{serviceDate ? ` : ${serviceDate.slice(6, 8)}/${serviceDate.slice(4, 6)}/${serviceDate.slice(0, 4)}` : ""}).
      </p>
      <AddressSearch
        placeholder="➕ Ajouter une destination (adresse, ville, gare)…"
        onSelect={(r) =>
          onChange([...destinations, newDestination(destinations, { name: r.city || r.name, label: r.label, lat: r.lat, lon: r.lon })])
        }
      />
      {destinations.map((d) => {
        const st = status[d.id] ?? {};
        return (
          <div key={d.id} className={`card dest ${d.enabled ? "" : "disabled"}`} style={{ borderLeftColor: d.color }}>
            <div className="row">
              <input type="checkbox" checked={d.enabled} onChange={(e) => update(d.id, { enabled: e.target.checked })} title="Activer" />
              <input className="dest-name" value={d.name} onChange={(e) => update(d.id, { name: e.target.value })} />
              <button className="icon" title="Supprimer" onClick={() => onChange(destinations.filter((x) => x.id !== d.id))}>
                ✕
              </button>
            </div>
            <div className="muted small">{d.label}</div>
            <div className="row">
              <label>Mode</label>
              <div className="seg">
                {(
                  [
                    ["car", "🚗 Voiture"],
                    ["train", "🚆 Train"],
                    ["best", "⚡ Le + rapide"],
                  ] as [TravelMode, string][]
                ).map(([m, l]) => (
                  <button key={m} className={d.mode === m ? "on" : ""} onClick={() => update(d.id, { mode: m })}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
            <div className="row">
              <label>Trajet max</label>
              <input
                type="range"
                min={10}
                max={180}
                step={5}
                value={d.maxMinutes}
                onChange={(e) => update(d.id, { maxMinutes: +e.target.value })}
              />
              <b className="val">{d.maxMinutes} min</b>
            </div>
            {d.mode !== "car" && (
              <>
                <div className="row">
                  <label>Arrivée avant</label>
                  <input
                    type="time"
                    value={fmtClock(d.arriveBy).padStart(5, "0")}
                    onChange={(e) => {
                      const [h, m] = e.target.value.split(":").map(Number);
                      if (!isNaN(h)) update(d.id, { arriveBy: h * 60 + (m || 0) });
                    }}
                  />
                  <label className="ml">souplesse</label>
                  <select value={d.window} onChange={(e) => update(d.id, { window: +e.target.value })}>
                    {[0, 30, 60, 90, 120, 180].map((w) => (
                      <option key={w} value={w}>
                        {w} min
                      </option>
                    ))}
                  </select>
                </div>
                <div className="row">
                  <label>Accès gare</label>
                  <select value={d.access} onChange={(e) => update(d.id, { access: e.target.value as AccessMode })}>
                    <option value="walk">à pied</option>
                    <option value="bike">à vélo</option>
                    <option value="car">en voiture</option>
                  </select>
                  <label className="ml">max</label>
                  <select value={d.maxAccess} onChange={(e) => update(d.id, { maxAccess: +e.target.value })}>
                    {[5, 10, 15, 20, 25, 30, 40, 45].map((w) => (
                      <option key={w} value={w}>
                        {w} min
                      </option>
                    ))}
                  </select>
                </div>
              </>
            )}
            <div className="row">
              <label title="Les communes au-delà du temps max sont exclues">
                <input type="checkbox" checked={d.required} onChange={(e) => update(d.id, { required: e.target.checked })} />{" "}
                Éliminatoire
              </label>
              <label className="ml">Poids</label>
              <Weight value={d.weight} onChange={(w) => update(d.id, { weight: w })} />
            </div>
            <div className="small status">
              {st.loading && <span className="spin">⏳ calcul…</span>}
              {st.error && <span className="err">⚠ {st.error}</span>}
              {!st.loading && !st.error && st.reachable != null && (
                <span>{st.reachable.toLocaleString("fr-FR")} communes à moins de {d.maxMinutes} min</span>
              )}
            </div>
          </div>
        );
      })}
      {destinations.length === 0 && (
        <div className="empty">
          Aucune destination. Exemple : votre lieu de travail avec « Arrivée avant 9:00 », 60 min max, mode « Le + rapide ».
        </div>
      )}
      <details className="help">
        <summary>Comment sont calculés les temps ?</summary>
        <ul>
          <li>
            <b>Voiture</b> : isochrones du service IGN (réseau BD TOPO, vitesses moyennes, sans trafic) par paliers ; le temps
            affiché est la borne haute du palier contenant le centre de la commune.
          </li>
          <li>
            <b>Train</b> : horaires réels (TGV, OUIGO, Intercités, TER, cars TER, RER, Transilien) d'un mardi type. On cherche le
            trajet le plus court arrivant dans la fenêtre « arrivée avant − souplesse ». Correspondances : 4 min minimum, à pied
            entre gares proches, métro parisien estimé. On ajoute le rabattement du centre de la commune vers la gare (estimé à
            vol d'oiseau × 1,3) et le trajet gare → destination.
          </li>
          <li>
            <b>Le + rapide</b> : le meilleur des deux. Métro, tram et bus urbains hors Île-de-France ne sont pas modélisés.
          </li>
        </ul>
      </details>
    </div>
  );
}

export function Weight({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <span className="weight" title="Importance dans le score (0 = ignoré)">
      {[1, 2, 3].map((w) => (
        <button key={w} className={value >= w ? "on" : ""} onClick={() => onChange(value === w ? w - 1 : w)}>
          ●
        </button>
      ))}
    </span>
  );
}
