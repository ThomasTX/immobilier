"""Délinquance enregistrée par commune (SSMSI, ministère de l'Intérieur).

Source : « Bases statistiques communale, départementale et régionale de la
délinquance enregistrée par la police et la gendarmerie nationales » (data.gouv.fr).
On retient la dernière année disponible de la base communale (CSV compressé).

Règles :
  - taux pour 1 000 habitants (1 000 logements pour les cambriolages), recalculés
    à partir des nombres et des dénominateurs INSEE fournis dans la base ;
  - communes « ndiff » (moins de 5 faits sur 3 années successives, secret
    statistique) → valeur vide ;
  - Paris/Lyon/Marseille : la base fournit déjà la ligne de la commune entière
    (75056, 69123, 13055) en plus des arrondissements ; on l'utilise en priorité,
    sinon on agrège les arrondissements (somme des nombres / somme des dénominateurs).
"""
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ARRONDISSEMENTS, UA, download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

DATASET_API = ("https://www.data.gouv.fr/api/1/datasets/"
               "bases-statistiques-communale-departementale-et-regionale-de-la-delinquance-"
               "enregistree-par-la-police-et-la-gendarmerie-nationales/")
# Repli si l'API data.gouv.fr est indisponible (édition juillet 2026, données 2025)
FALLBACK_URL = ("https://static.data.gouv.fr/resources/bases-statistiques-communale-departementale-et-regionale-"
                "de-la-delinquance-enregistree-par-la-police-et-la-gendarmerie-nationales/20260709-115942/"
                "donnee-data.gouv-2025-geographie2026-produit-le2026-06-25.csv.gz")

# id de colonne -> (libellé SSMSI, dénominateur 'pop' ou 'log')
INDICATEURS = {
    "cambriolages_1000": ("Cambriolages de logement", "log"),
    "violences_physiques_1000": ("Violences physiques hors cadre familial", "pop"),
    "vols_vehicules_1000": ("Vols de véhicule", "pop"),
    "destructions_degradations_1000": ("Destructions et dégradations volontaires", "pop"),
}


def find_csv_url() -> str:
    """URL du CSV communal le plus récent publié sur data.gouv.fr."""
    try:
        req = urllib.request.Request(DATASET_API, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            ds = json.load(r)
        cands = [res for res in ds["resources"]
                 if res["title"].startswith("COM - ") and res["url"].endswith(".csv.gz")]
        if cands:
            cands.sort(key=lambda res: res.get("last_modified") or "", reverse=True)
            return cands[0]["url"]
    except Exception as e:  # noqa: BLE001
        print(f"  API data.gouv.fr indisponible ({e}), URL de repli", file=sys.stderr)
    return FALLBACK_URL


def main():
    url = find_csv_url()
    path = download(url, "ssmsi_" + url.rsplit("/", 1)[-1])
    df = pd.read_csv(path, sep=";", dtype=str, compression="gzip")
    df = df.rename(columns={c: "codgeo" for c in df.columns if c.upper().startswith("CODGEO")})
    annee = int(df["annee"].astype(int).max())
    df = df[df["annee"].astype(int) == annee].copy()
    pop_mil = df["insee_pop_millesime"].dropna().iloc[0]
    log_mil = df["insee_log_millesime"].dropna().iloc[0]
    print(f"  année {annee}, pop RP {pop_mil}, logements RP {log_mil}", file=sys.stderr)

    df["codgeo"] = df["codgeo"].str.zfill(5)
    for c in ("nombre", "insee_pop", "insee_log"):
        df[c] = pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
    df["diff"] = df["est_diffuse"].eq("diff")

    # Paris/Lyon/Marseille : garder la ligne communale si présente, sinon agréger les arrondissements
    parents_presents = set(df["codgeo"]) & set(ARRONDISSEMENTS.values())
    df = df[~df["codgeo"].map(lambda c: ARRONDISSEMENTS.get(c) in parents_presents)]
    df["code_insee"] = df["codgeo"].map(normalize_insee)

    wanted = {lib: col for col, (lib, _) in INDICATEURS.items()}
    df = df[df["indicateur"].isin(wanted)].copy()
    df["col"] = df["indicateur"].map(wanted)
    df.loc[~df["diff"], "nombre"] = float("nan")

    # Dénominateurs par commune (dédoublonnés par code source avant agrégation)
    den = (df.drop_duplicates("codgeo").groupby("code_insee")[["insee_pop", "insee_log"]].sum(min_count=1))
    # Numérateurs : NaN si au moins une composante non diffusée
    num = df.groupby(["code_insee", "col"]).agg(nombre=("nombre", lambda s: s.sum() if s.notna().all() else float("nan")))
    num = num["nombre"].unstack("col")

    res = num.join(den, how="left")
    out = pd.DataFrame(index=res.index)
    for col, (_, d) in INDICATEURS.items():
        denom = res["insee_log"] if d == "log" else res["insee_pop"]
        out[col] = (res[col] / denom * 1000).where(denom > 0)
    # Indice agrégé : faits des 4 catégories pour 1 000 habitants (si les 4 sont diffusées)
    tot = res[list(INDICATEURS)].sum(axis=1, min_count=len(INDICATEURS))
    tot = tot.where(res[list(INDICATEURS)].notna().all(axis=1))
    out["indice_delinquance"] = (tot / res["insee_pop"] * 1000).where(res["insee_pop"] > 0)

    cols = [*INDICATEURS, "indice_delinquance"]
    rows = []
    for c in load_communes():
        code = c["code_insee"]
        r = {"code_insee": code}
        if code in out.index:
            for col in cols:
                v = out.at[code, col]
                r[col] = None if pd.isna(v) else round(float(v), 3)
        rows.append(r)
    write_csv("securite", rows, cols)

    src = (f"SSMSI (ministère de l'Intérieur), base statistique communale de la délinquance enregistrée "
           f"par la police et la gendarmerie nationales, année {annee} (data.gouv.fr) ; "
           f"populations RP {pop_mil}, logements RP {log_mil}")
    secret = (" Valeur vide si la commune est sous secret statistique (données non diffusées par le SSMSI "
              "lorsque 5 faits ou moins ont été enregistrés 3 années successives), ce qui concerne la plupart "
              "des petites communes. Lieu de commission des faits.")
    meta = [
        {"id": "cambriolages_1000", "label": "Cambriolages de logement", "group": "Sécurité",
         "unit": "‰ logements", "type": "number", "better": "lower", "decimals": 1,
         "description": f"Cambriolages de logement (résidences principales et secondaires) enregistrés en {annee}, "
                        f"pour 1 000 logements." + secret, "source": src},
        {"id": "violences_physiques_1000", "label": "Violences physiques (hors famille)", "group": "Sécurité",
         "unit": "‰ hab.", "type": "number", "better": "lower", "decimals": 1,
         "description": f"Victimes de violences physiques hors cadre familial enregistrées en {annee} "
                        f"(coups et blessures volontaires, etc.), pour 1 000 habitants." + secret, "source": src},
        {"id": "vols_vehicules_1000", "label": "Vols de véhicules", "group": "Sécurité",
         "unit": "‰ hab.", "type": "number", "better": "lower", "decimals": 1,
         "description": f"Véhicules volés (automobiles, deux-roues motorisés) enregistrés en {annee}, "
                        f"pour 1 000 habitants (hors vols dans ou sur les véhicules)." + secret, "source": src},
        {"id": "destructions_degradations_1000", "label": "Destructions et dégradations", "group": "Sécurité",
         "unit": "‰ hab.", "type": "number", "better": "lower", "decimals": 1,
         "description": f"Destructions et dégradations volontaires enregistrées en {annee}, "
                        f"pour 1 000 habitants." + secret, "source": src},
        {"id": "indice_delinquance", "label": "Indice de délinquance", "group": "Sécurité",
         "unit": "‰ hab.", "type": "number", "better": "lower", "decimals": 1, "scale": "log",
         "description": f"Indice synthétique simple : nombre total de cambriolages de logement, violences "
                        f"physiques hors cadre familial, vols de véhicules et destructions-dégradations "
                        f"enregistrés en {annee}, pour 1 000 habitants. Renseigné seulement si les 4 "
                        f"catégories sont diffusées." + secret, "source": src},
    ]
    write_indicators("securite", meta)

    n = len(rows)
    for col in cols:
        k = sum(1 for r in rows if r.get(col) is not None)
        print(f"  {col}: {k}/{n} ({100 * k / n:.1f} %)", file=sys.stderr)


if __name__ == "__main__":
    main()
