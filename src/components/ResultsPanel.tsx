import { useMemo, useState } from "react";
import type { CommunesData, Destination, NumFilter } from "../data/types";
import type { Evaluation, TravelTimes } from "../engine/score";
import { filterActive, fmtValue } from "../engine/score";
import { fmtMinutes } from "../engine/geo";
import { download } from "../state/store";
import { Diagnostic } from "./Diagnostic";

const LIMIT = 300;

export function ResultsPanel({
  data,
  evaluation,
  destinations,
  travel,
  filters,
  onSelect,
}: {
  data: CommunesData;
  evaluation: Evaluation;
  destinations: Destination[];
  travel: TravelTimes[];
  filters: Record<string, NumFilter>;
  onSelect: (i: number) => void;
}) {
  const [sortBy, setSortBy] = useState<string>("score");
  const [minPop, setMinPop] = useState(0);
  const [q, setQ] = useState("");
  const dests = destinations.filter((d) => d.enabled && travel.some((t) => t.destId === d.id));
  // colonnes : critères actifs + prix
  const cols = useMemo(() => {
    const ids = new Set<string>(["prix_m2_maison", "prix_m2_appart"]);
    for (const [k, f] of Object.entries(filters)) if (filterActive(f) || f.weight > 0) ids.add(k);
    return data.indicators.filter((i) => ids.has(i.id)).slice(0, 8);
  }, [data, filters]);

  const rows = useMemo(() => {
    const out: number[] = [];
    const needle = q.trim().toLowerCase();
    const pop = data.values.population;
    for (let i = 0; i < data.codes.length; i++) {
      if (!evaluation.pass[i]) continue;
      if (minPop && (pop?.[i] ?? 0) < minPop) continue;
      if (needle && !data.noms[i].toLowerCase().includes(needle) && !data.cps[i].startsWith(needle)) continue;
      out.push(i);
    }
    const key = (i: number): number => {
      if (sortBy === "score") return -(isFinite(evaluation.score[i]) ? evaluation.score[i] : -1);
      if (sortBy.startsWith("time:")) {
        const t = travel.find((x) => x.destId === sortBy.slice(5));
        return t ? t.minutes[i] : Infinity;
      }
      const v = data.values[sortBy]?.[i];
      const ind = data.indicators.find((x) => x.id === sortBy);
      const dir = filters[sortBy]?.prefer ?? ind?.better ?? "lower";
      if (v == null) return Infinity;
      return dir === "higher" ? -v : v;
    };
    out.sort((a, b) => key(a) - key(b));
    return out;
  }, [data, evaluation, sortBy, travel, filters, minPop, q]);

  const exportCsv = () => {
    const header = ["code_insee", "commune", "departement", "code_postal", "score", ...dests.map((d) => `trajet_${d.name}_min`), ...data.indicators.map((i) => i.id)];
    const lines = [header.join(";")];
    for (const i of rows) {
      lines.push(
        [
          data.codes[i],
          `"${data.noms[i]}"`,
          data.deps[i],
          data.cps[i],
          isFinite(evaluation.score[i]) ? evaluation.score[i].toFixed(0) : "",
          ...dests.map((d) => {
            const m = travel.find((t) => t.destId === d.id)!.minutes[i];
            return isFinite(m) ? Math.round(m) : "";
          }),
          ...data.indicators.map((ind) => String(data.values[ind.id][i] ?? "").replace(".", ",")),
        ].join(";"),
      );
    }
    download("communes_selection.csv", "﻿" + lines.join("\n"), "text/csv");
  };

  return (
    <div className="panel">
      <div className="row">
        <b>{evaluation.nPass.toLocaleString("fr-FR")}</b>&nbsp;communes correspondent
        <span className="grow" />
        <button className="small" onClick={exportCsv} title="Exporter la sélection (CSV, toutes les colonnes)">
          ⬇ CSV
        </button>
      </div>
      <Diagnostic data={data} evaluation={evaluation} destinations={destinations} />
      <div className="row">
        <input className="grow" placeholder="Filtrer par nom ou code postal" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={minPop} onChange={(e) => setMinPop(+e.target.value)} title="Population minimum (affichage)">
          <option value={0}>toutes tailles</option>
          <option value={1000}>≥ 1 000 hab.</option>
          <option value={5000}>≥ 5 000 hab.</option>
          <option value={20000}>≥ 20 000 hab.</option>
        </select>
      </div>
      <div className="row">
        <label>Trier par</label>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="score">Score</option>
          {dests.map((d) => (
            <option key={d.id} value={`time:${d.id}`}>
              Trajet → {d.name}
            </option>
          ))}
          {data.indicators.map((i) => (
            <option key={i.id} value={i.id}>
              {i.label}
            </option>
          ))}
        </select>
      </div>
      <div className="table-wrap">
        <table className="results">
          <thead>
            <tr>
              <th>Commune</th>
              <th>Score</th>
              {dests.map((d) => (
                <th key={d.id} style={{ color: d.color }} title={`Trajet vers ${d.name}`}>
                  ★ {d.name.slice(0, 10)}
                </th>
              ))}
              {cols.map((c) => (
                <th key={c.id} title={c.label}>
                  {c.label.length > 16 ? c.label.slice(0, 15) + "…" : c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, LIMIT).map((i) => (
              <tr key={i} onClick={() => onSelect(i)}>
                <td>
                  {data.noms[i]} <span className="muted small">{data.deps[i]}</span>
                </td>
                <td>
                  <ScoreBadge v={evaluation.score[i]} />
                </td>
                {dests.map((d) => {
                  const t = travel.find((x) => x.destId === d.id)!;
                  return (
                    <td key={d.id} className="nowrap">
                      {fmtMinutes(t.minutes[i])}
                      {t.car && t.train && (
                        <span className="muted small">{isFinite(t.train[i]) && t.train[i] <= (t.car[i] ?? Infinity) ? " 🚆" : " 🚗"}</span>
                      )}
                    </td>
                  );
                })}
                {cols.map((c) => (
                  <td key={c.id} className="nowrap">
                    {fmtValue({ ...c, unit: "" }, data.values[c.id][i])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > LIMIT && <div className="muted small">… {rows.length - LIMIT} autres (affinez ou exportez en CSV)</div>}
      </div>
    </div>
  );
}

export function ScoreBadge({ v }: { v: number }) {
  if (!isFinite(v)) return <span className="muted">—</span>;
  const hue = Math.round((v / 100) * 120);
  return (
    <span className="badge" style={{ background: `hsl(${hue} 65% 42%)` }}>
      {Math.round(v)}
    </span>
  );
}
