import type { CommunesData, Destination } from "../data/types";
import type { Evaluation } from "../engine/score";

/** Indique quels critères éliminent le plus de communes, pour aider à relâcher les filtres. */
export function Diagnostic({ data, evaluation, destinations }: { data: CommunesData; evaluation: Evaluation; destinations: Destination[] }) {
  const label = (k: string) => {
    if (k === "dep") return "Départements";
    if (k.startsWith("dest:")) return `Trajet → ${destinations.find((d) => d.id === k.slice(5))?.name ?? "?"}`;
    return data.indicators.find((i) => i.id === k)?.label ?? k;
  };
  const keys = Object.keys(evaluation.failing).filter((k) => k !== "dep");
  if (!keys.length) return null;
  const rows = keys
    .map((k) => ({ k, only: evaluation.blockedOnlyBy[k] ?? 0, all: evaluation.failing[k] }))
    .sort((a, b) => b.only - a.only || b.all - a.all);
  return (
    <details className="diag" open={evaluation.nPass < 20}>
      <summary>
        {evaluation.nPass < 20 ? "⚠ Très peu de résultats : quels critères bloquent ?" : "Quels critères éliminent le plus ?"}
      </summary>
      <table>
        <tbody>
          {rows.map((r) => (
            <tr key={r.k}>
              <td>{label(r.k)}</td>
              <td className="nowrap" title="Communes qui passeraient si on retirait uniquement ce critère">
                {r.only ? <b>+{r.only.toLocaleString("fr-FR")}</b> : <span className="muted">+0</span>} si retiré
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
