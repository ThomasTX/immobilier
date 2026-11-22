import type { CommunesData, Destination, NumFilter, TransitData } from "../data/types";
import type { Evaluation, TravelTimes } from "../engine/score";
import { filterActive, fmtValue, passesFilter, ranks } from "../engine/score";
import { fmtClock, fmtMinutes } from "../engine/geo";
import type { TrainResult } from "../engine/train";
import { trainFromPoint } from "../engine/train";
import { ScoreBadge } from "./ResultsPanel";

function slug(s: string) {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function communeLinks(nom: string, cp: string, insee: string) {
  return [
    { label: "Leboncoin", url: `https://www.leboncoin.fr/recherche?category=9&locations=${encodeURIComponent(nom)}_${cp}` },
    { label: "Bien'ici", url: `https://www.bienici.com/recherche/achat/${slug(nom)}-${cp}` },
    { label: "SeLoger", url: `https://www.google.com/search?q=${encodeURIComponent(`site:seloger.com achat ${nom} ${cp}`)}` },
    { label: "Ventes DVF", url: `https://explore.data.gouv.fr/fr/immobilier?onglet=carte&filtre=tous&level=commune&code=${insee}` },
    { label: "Géorisques", url: `https://www.georisques.gouv.fr/mes-risques/connaitre-les-risques-pres-de-chez-moi/rapport2?form-commune=true&codeInsee=${insee}` },
    { label: "Wikipédia", url: `https://fr.wikipedia.org/w/index.php?search=${encodeURIComponent(`${nom} ${cp.slice(0, 2)}`)}` },
  ];
}

export function CommuneDetail({
  data,
  i,
  evaluation,
  destinations,
  travel,
  trainResults,
  transit,
  filters,
  onClose,
  onAddListing,
}: {
  data: CommunesData;
  i: number;
  evaluation: Evaluation;
  destinations: Destination[];
  travel: TravelTimes[];
  trainResults: Record<string, TrainResult>;
  transit: TransitData | null;
  filters: Record<string, NumFilter>;
  onClose: () => void;
  onAddListing: () => void;
}) {
  const groups = new Map<string, typeof data.indicators>();
  for (const ind of data.indicators) {
    if (!groups.has(ind.group)) groups.set(ind.group, []);
    groups.get(ind.group)!.push(ind);
  }
  const failing: string[] = [];
  for (const ind of data.indicators) {
    const f = filters[ind.id];
    if (filterActive(f) && !passesFilter(ind, f!, data.values[ind.id][i])) failing.push(ind.label);
  }
  for (const d of destinations) {
    const t = travel.find((x) => x.destId === d.id);
    if (d.enabled && d.required && t && !(t.minutes[i] <= d.maxMinutes)) failing.push(`Trajet → ${d.name}`);
  }
  return (
    <div className="detail">
      <div className="row">
        <h2>
          {data.noms[i]} <span className="muted">({data.cps[i]})</span>
        </h2>
        <span className="grow" />
        <button className="icon" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="row">
        <ScoreBadge v={evaluation.score[i]} />
        {evaluation.pass[i] ? (
          <span className="ok">✓ correspond à vos critères</span>
        ) : (
          <span className="err" title={failing.join(", ")}>
            ✕ hors critères{failing.length ? ` : ${failing.slice(0, 3).join(", ")}${failing.length > 3 ? "…" : ""}` : " (département)"}
          </span>
        )}
      </div>
      <div className="links">
        {communeLinks(data.noms[i], data.cps[i], data.codes[i]).map((l) => (
          <a key={l.label} href={l.url} target="_blank" rel="noreferrer">
            {l.label} ↗
          </a>
        ))}
        <button className="small" onClick={onAddListing}>
          ➕ Ajouter un bien ici
        </button>
      </div>

      {destinations.filter((d) => d.enabled).length > 0 && (
        <div className="section">
          <h3>Trajets</h3>
          {destinations
            .filter((d) => d.enabled)
            .map((d) => {
              const t = travel.find((x) => x.destId === d.id);
              const tr = trainResults[d.id];
              const pt = tr && transit ? trainFromPoint(transit, tr, data.lat[i], data.lon[i], d.access, d.maxAccess) : null;
              return (
                <div key={d.id} className="trip">
                  <span style={{ color: d.color }}>★</span> <b>{d.name}</b>
                  {t?.car && <div>🚗 Voiture : {isFinite(t.car[i]) ? `≤ ${fmtMinutes(t.car[i])}` : `> ${fmtMinutes(d.maxMinutes)}`}</div>}
                  {tr && (
                    <div>
                      🚆 Train :{" "}
                      {pt ? (
                        <>
                          <b>{fmtMinutes(pt.minutes)}</b> — {d.access === "car" ? "voiture" : d.access === "bike" ? "vélo" : "à pied"}{" "}
                          {fmtMinutes(pt.access)} jusqu'à <b>{transit!.stations[pt.station][0]}</b>, départ {fmtClock(pt.depTime + pt.access + 2)}, arrivée
                          avant {fmtClock(pt.arrTime)}
                        </>
                      ) : (
                        <span className="muted">pas de gare utile à moins de {d.maxAccess} min</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      )}

      {[...groups.entries()].map(([g, inds]) => (
        <div key={g} className="section">
          <h3>{g}</h3>
          <table className="kv">
            <tbody>
              {inds.map((ind) => {
                const v = data.values[ind.id][i];
                const r = ranks(data, ind.id)[i];
                const f = filters[ind.id];
                const bad = filterActive(f) && !passesFilter(ind, f!, v);
                let good = r;
                if (ind.better === "lower") good = 1 - r;
                return (
                  <tr key={ind.id} className={bad ? "bad" : ""} title={`${ind.description ?? ""}\nSource : ${ind.source ?? ""}`}>
                    <td>{ind.label}</td>
                    <td className="nowrap">
                      <b>{fmtValue(ind, v)}</b>
                      {ind.id === "dist_gare_km" && data.texts.gare_proche?.[i] ? (
                        <span className="muted small"> ({data.texts.gare_proche[i]})</span>
                      ) : null}
                      {ind.id === "dist_gare_tgv_km" && data.texts.gare_tgv_proche?.[i] ? (
                        <span className="muted small"> ({data.texts.gare_tgv_proche[i]})</span>
                      ) : null}
                    </td>
                    <td className="bar-cell">
                      {ind.type === "number" && isFinite(r) && (
                        <div className="bar" title={`${Math.round(r * 100)}e percentile`}>
                          <div
                            style={{
                              width: `${Math.max(3, r * 100)}%`,
                              background: ind.better ? `hsl(${Math.round(good * 120)} 60% 45%)` : "#7a8a9a",
                            }}
                          />
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
      <p className="muted small">
        Barres : position de la commune parmi toutes les communes françaises (vert = favorable). Survolez une ligne pour la source.
      </p>
    </div>
  );
}
