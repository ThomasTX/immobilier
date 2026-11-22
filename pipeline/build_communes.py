"""Fusionne toutes les sources (pipeline/out/*.csv + pipeline/indicators/*.json) en un seul
fichier colonnaire pour l'application : public/data/communes.json

Format :
{
  "generated": "AAAA-MM-JJ",
  "codes": [...], "noms": [...], "deps": [...], "cps": [...], "lat": [...], "lon": [...],
  "indicators": [ {id, label, group, unit, type, better, description, source, decimals, scale?, categories?, coverage} ],
  "values": { id: [nombre | null, ...] },     # aligné sur "codes"
  "texts": { id: [str, ...] }                  # colonnes texte (ex. nom de la gare la plus proche)
}
"""
import csv
import json
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import IND, OUT, PUBLIC_DATA  # noqa: E402

GROUP_ORDER = ["Immobilier", "Location courte durée", "Fiscalité", "Transports", "Services", "Cadre de vie", "Risques", "Sécurité",
               "Démographie", "Économie"]
BASE_COLS = {"nom", "dep", "region", "cp", "lat", "lon"}
TEXT_COLS = {"gare_proche", "gare_tgv_proche"}


def num(v: str):
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return int(x) if x.is_integer() and abs(x) < 2**31 else x


def main():
    with open(OUT / "communes.csv", newline="", encoding="utf-8") as f:
        base = list(csv.DictReader(f))
    # tri : métropole puis DOM, par code
    base.sort(key=lambda r: r["code_insee"])
    idx = {r["code_insee"]: i for i, r in enumerate(base)}
    n = len(base)

    meta_by_id: dict[str, dict] = {}
    for p in sorted(IND.glob("*.json")):
        for m in json.loads(p.read_text(encoding="utf-8")):
            meta_by_id[m["id"]] = m

    values: dict[str, list] = {}
    texts: dict[str, list] = {}
    for p in sorted(OUT.glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            cols = [c for c in reader.fieldnames if c != "code_insee"]
            if p.stem == "communes":
                cols = [c for c in cols if c not in BASE_COLS]
            for c in cols:
                if c in TEXT_COLS:
                    texts.setdefault(c, [""] * n)
                elif c in meta_by_id:
                    values.setdefault(c, [None] * n)
            missing = 0
            for r in reader:
                i = idx.get(r["code_insee"])
                if i is None:
                    missing += 1
                    continue
                for c in cols:
                    if c in TEXT_COLS:
                        texts[c][i] = r[c] or ""
                    elif c in values:
                        v = num(r[c])
                        if v is not None:
                            values[c][i] = v
            if missing:
                print(f"  {p.name}: {missing} codes inconnus ignorés", file=sys.stderr)

    indicators = []
    for cid, arr in values.items():
        m = dict(meta_by_id[cid])
        filled = sum(v is not None for v in arr)
        if filled == 0:
            del m
            continue
        m["coverage"] = round(filled / n, 3)
        dec = m.get("decimals", 2)
        if isinstance(dec, int) and dec >= 0:
            values[cid] = [None if v is None else (round(v, dec) if dec else int(round(v))) for v in arr]
        indicators.append(m)
    indicators.sort(key=lambda m: (GROUP_ORDER.index(m["group"]) if m["group"] in GROUP_ORDER else 99))
    values = {m["id"]: values[m["id"]] for m in indicators}

    data = {
        "generated": date.today().isoformat(),
        "codes": [r["code_insee"] for r in base],
        "noms": [r["nom"] for r in base],
        "deps": [r["dep"] for r in base],
        "cps": [r["cp"] for r in base],
        "lat": [float(r["lat"]) for r in base],
        "lon": [float(r["lon"]) for r in base],
        "indicators": indicators,
        "values": values,
        "texts": texts,
    }
    out = PUBLIC_DATA / "communes.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  → {out} : {n} communes, {len(indicators)} indicateurs, {out.stat().st_size / 1e6:.1f} Mo", file=sys.stderr)
    for m in indicators:
        print(f"     {m['group']:<13} {m['id']:<32} {m['coverage'] * 100:5.1f} %", file=sys.stderr)


if __name__ == "__main__":
    main()
