import { useMemo, useState } from "react";
import type { CommunesData, Indicator, NumFilter } from "../data/types";
import { filterActive, fmtValue } from "../engine/score";
import { Weight } from "./DestinationsPanel";
import { Diagnostic } from "./Diagnostic";
import type { Destination } from "../data/types";
import type { Evaluation } from "../engine/score";

function quantiles(vals: (number | null)[]): { p10: number; p50: number; p90: number; min: number; max: number } | null {
  const v = vals.filter((x): x is number => x != null).sort((a, b) => a - b);
  if (!v.length) return null;
  const q = (p: number) => v[Math.min(v.length - 1, Math.floor(p * (v.length - 1)))];
  return { p10: q(0.1), p50: q(0.5), p90: q(0.9), min: v[0], max: v[v.length - 1] };
}

export function CriteriaPanel({
  data,
  filters,
  onChange,
  departements,
  onDepartements,
  evaluation,
  destinations,
}: {
  data: CommunesData;
  filters: Record<string, NumFilter>;
  onChange: (f: Record<string, NumFilter>) => void;
  departements: string;
  onDepartements: (s: string) => void;
  evaluation: Evaluation;
  destinations: Destination[];
}) {
  const [q, setQ] = useState("");
  const [onlyActive, setOnlyActive] = useState(false);
  const stats = useMemo(() => {
    const s: Record<string, ReturnType<typeof quantiles>> = {};
    for (const ind of data.indicators) if (ind.type === "number") s[ind.id] = quantiles(data.values[ind.id]);
    return s;
  }, [data]);

  const groups = useMemo(() => {
    const g = new Map<string, Indicator[]>();
    const needle = q.toLowerCase();
    for (const ind of data.indicators) {
      if (needle && !`${ind.label} ${ind.description ?? ""} ${ind.group}`.toLowerCase().includes(needle)) continue;
      const f = filters[ind.id];
      if (onlyActive && !(filterActive(f) || (f && f.weight > 0))) continue;
      if (!g.has(ind.group)) g.set(ind.group, []);
      g.get(ind.group)!.push(ind);
    }
    return [...g.entries()];
  }, [data, q, onlyActive, filters]);

  const set = (id: string, patch: Partial<NumFilter>) => {
    const cur = filters[id] ?? { weight: 0 };
    onChange({ ...filters, [id]: { ...cur, ...patch } });
  };
  const nActive = Object.values(filters).filter((f) => filterActive(f) || f.weight > 0).length;

  return (
    <div className="panel">
      <div className="row">
        <label>Départements</label>
        <input
          value={departements}
          placeholder="ex : 74, 73, 01, 38 (vide = toute la France)"
          onChange={(e) => onDepartements(e.target.value)}
        />
      </div>
      <div className="row">
        <input className="grow" placeholder="🔎 Chercher un critère…" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="small nowrap">
          <input type="checkbox" checked={onlyActive} onChange={(e) => setOnlyActive(e.target.checked)} /> actifs ({nActive})
        </label>
        {nActive > 0 && (
          <button className="link small" onClick={() => onChange({})}>
            tout effacer
          </button>
        )}
      </div>
      <Diagnostic data={data} evaluation={evaluation} destinations={destinations} />
      <p className="hint">
        <b>Min / max</b> excluent les communes hors bornes. Les <b>●●●</b> donnent l'importance du critère dans le score (classement
        percentile des communes). Ⓘ = description et source.
      </p>
      {groups.map(([group, inds]) => (
        <details key={group} className="group" open={!!q || onlyActive}>
          <summary>
            {group}{" "}
            <span className="muted small">
              {inds.filter((i) => filterActive(filters[i.id]) || (filters[i.id]?.weight ?? 0) > 0).length || ""}
            </span>
          </summary>
          {inds.map((ind) => {
            const f = filters[ind.id] ?? { weight: 0 };
            const st = stats[ind.id];
            const active = filterActive(f) || f.weight > 0;
            return (
              <div key={ind.id} className={`crit ${active ? "active" : ""}`}>
                <div className="row">
                  <span className="crit-label" title={`${ind.description ?? ""}\nSource : ${ind.source ?? "?"}`}>
                    {ind.label} <span className="info">ⓘ</span>
                  </span>
                  <Weight value={f.weight} onChange={(w) => set(ind.id, { weight: w })} />
                </div>
                {ind.type === "number" && (
                  <div className="row">
                    <input
                      type="number"
                      className="num"
                      placeholder={st ? `min (${fmtShort(st.min)})` : "min"}
                      value={f.min ?? ""}
                      onChange={(e) => set(ind.id, { min: e.target.value === "" ? null : +e.target.value })}
                    />
                    <span className="muted">–</span>
                    <input
                      type="number"
                      className="num"
                      placeholder={st ? `max (${fmtShort(st.max)})` : "max"}
                      value={f.max ?? ""}
                      onChange={(e) => set(ind.id, { max: e.target.value === "" ? null : +e.target.value })}
                    />
                    <span className="muted small unit">{ind.unit}</span>
                    {!ind.better && f.weight > 0 && (
                      <select
                        className="small"
                        value={f.prefer ?? ""}
                        onChange={(e) => set(ind.id, { prefer: (e.target.value || null) as NumFilter["prefer"] })}
                      >
                        <option value="">préférer…</option>
                        <option value="higher">+ élevé</option>
                        <option value="lower">+ faible</option>
                      </select>
                    )}
                  </div>
                )}
                {ind.type === "boolean" && (
                  <div className="seg small">
                    {(
                      [
                        [null, "Indifférent"],
                        ["yes", "Oui"],
                        ["no", "Non"],
                      ] as [NumFilter["bool"], string][]
                    ).map(([b, l]) => (
                      <button key={l} className={(f.bool ?? null) === b ? "on" : ""} onClick={() => set(ind.id, { bool: b })}>
                        {l}
                      </button>
                    ))}
                  </div>
                )}
                {ind.type === "category" && ind.categories && (
                  <div className="cats">
                    {Object.entries(ind.categories).map(([k, l]) => (
                      <label key={k} className="small">
                        <input
                          type="checkbox"
                          checked={f.cats?.includes(k) ?? false}
                          onChange={(e) => {
                            const cur = new Set(f.cats ?? []);
                            if (e.target.checked) cur.add(k);
                            else cur.delete(k);
                            set(ind.id, { cats: [...cur] });
                          }}
                        />
                        {l}
                      </label>
                    ))}
                  </div>
                )}
                <div className="muted small row">
                  {st && (
                    <span>
                      médiane {fmtValue(ind, st.p50)} · 80 % entre {fmtShort(st.p10)} et {fmtShort(st.p90)}
                    </span>
                  )}
                  {ind.coverage != null && ind.coverage < 0.9 && (
                    <span title="Part des communes ayant une valeur">· données {Math.round(ind.coverage * 100)} %</span>
                  )}
                  {filterActive(f) || f.excludeMissing ? (
                    <label title="Exclure les communes sans donnée pour ce critère">
                      <input type="checkbox" checked={!!f.excludeMissing} onChange={(e) => set(ind.id, { excludeMissing: e.target.checked })} />{" "}
                      exclure si inconnu
                    </label>
                  ) : null}
                  {active && (
                    <button className="link" onClick={() => onChange(Object.fromEntries(Object.entries(filters).filter(([k]) => k !== ind.id)))}>
                      réinitialiser
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </details>
      ))}
    </div>
  );
}

function fmtShort(v: number) {
  return v.toLocaleString("fr-FR", { maximumFractionDigits: Math.abs(v) < 10 ? 1 : 0 });
}
