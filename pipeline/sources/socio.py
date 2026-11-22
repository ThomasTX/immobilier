"""Indicateurs socio-démographiques communaux (INSEE : recensement + Filosofi).

Source : API Melodi de l'INSEE (https://api.insee.fr/melodi, accès libre sans clé),
qui expose les jeux de données du « dossier complet » communal. On interroge
directement l'API de données avec des filtres (quelques centaines de Ko compressés
par requête) plutôt que les fichiers complets (100 Mo à 700 Mo, que le serveur
coupe au bout de ~5 min).

Jeux utilisés (géographie au 01/01/2026) :
  - DS_FILOSOFI_CC          : niveau de vie médian, taux de pauvreté (Filosofi)
  - DS_RP_SERIE_HISTORIQUE  : population (2 recensements), logements par catégorie
  - DS_RP_POPULATION_PRINC  : population par âge
  - DS_RP_EMPLOI_LR_PRINC   : actifs et chômeurs de 15-64 ans
  - DS_RP_LOGEMENT_PRINC    : résidences principales par statut d'occupation

Paris/Lyon/Marseille : l'API fournit la commune entière (niveau COM) ; les
arrondissements (niveau ARM) ne sont pas demandés. Si une commune parente
manquait, les numérateurs/dénominateurs seraient agrégés via normalize_insee.
"""
import gzip
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CACHE, UA, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

API = "https://api.insee.fr/melodi/data/"
RP = "2023"       # dernier recensement communal diffusé (édition 2026)
RP_OLD = "2017"   # recensement de référence pour l'évolution (~5-6 ans avant)
FILOSOFI = "2023"
PAGE = 100_000    # maximum accepté par l'API


def melodi(ds: str, max_age_days: float = 30, **filters) -> pd.DataFrame:
    """Observations d'un jeu Melodi au niveau communal, avec cache JSON gzip.

    filters : dimension -> valeur ou liste de valeurs. Retourne un DataFrame
    (code, dimensions filtrées multi-valeurs, value, conf)."""
    params = [("GEO", "COM")]
    for k, v in filters.items():
        for x in (v if isinstance(v, (list, tuple)) else [v]):
            params.append((k, x))
    label = "_".join(f"{k}-{v}" for k, v in params[1:])
    key = f"melodi_{ds}_{label}.json.gz"
    path = CACHE / key
    if path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400:
        obs = json.loads(gzip.decompress(path.read_bytes()))
    else:
        obs, page = [], 1
        while True:
            q = params + [("maxResult", PAGE), ("page", page)]
            for attempt in range(5):
                try:
                    print(f"  ↓ Melodi {ds} {label} p{page}", file=sys.stderr)
                    r = requests.get(API + ds, params=q, timeout=300, headers={"User-Agent": UA})
                    if r.status_code == 429:  # limite de 30 requêtes/min sans clé
                        time.sleep(30)
                        continue
                    r.raise_for_status()
                    batch = r.json().get("observations", [])
                    break
                except (requests.RequestException, ValueError) as e:
                    print(f"    tentative {attempt + 1} échouée: {e}", file=sys.stderr)
                    time.sleep(2 ** attempt * 3)
            else:
                raise RuntimeError(f"API Melodi indisponible: {ds}")
            obs += batch
            if len(batch) < PAGE:
                break
            page += 1
        if not obs:
            raise RuntimeError(f"aucune observation: {ds} {filters}")
        tmp = path.with_suffix(".part")
        tmp.write_bytes(gzip.compress(json.dumps(obs).encode()))
        tmp.replace(path)
    multi = [k for k, v in filters.items() if isinstance(v, (list, tuple))]
    rows = []
    for o in obs:
        d = o["dimensions"]
        m = o.get("measures", {}).get("OBS_VALUE_NIVEAU", {})
        rows.append({
            "code": d["GEO"].rsplit("-", 1)[-1],
            **{k: d.get(k) for k in multi},
            "value": m.get("value"),
            "conf": (o.get("attributes") or {}).get("CONF_STATUS"),
        })
    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df.loc[df["conf"] == "C", "value"] = float("nan")  # secret statistique
    return df


def wide(df: pd.DataFrame, dim: str, names: dict) -> pd.DataFrame:
    """Pivot code x modalité -> colonnes renommées."""
    w = df.pivot_table(index="code", columns=dim, values="value", aggfunc="first")
    return w.rename(columns=names)[list(names.values())]


def main():
    fi = wide(melodi("DS_FILOSOFI_CC", TIME_PERIOD=FILOSOFI, FILOSOFI_MEASURE=["MED_SL", "PR_MD60"]),
              "FILOSOFI_MEASURE", {"MED_SL": "med", "PR_MD60": "tp60"})
    pop = wide(melodi("DS_RP_SERIE_HISTORIQUE", RP_MEASURE="POP", TIME_PERIOD=[RP_OLD, RP]),
               "TIME_PERIOD", {RP_OLD: "pop_old", RP: "pop"})
    log = wide(melodi("DS_RP_SERIE_HISTORIQUE", RP_MEASURE="DWELLINGS", TIME_PERIOD=RP,
                      OCS=["_T", "DW_SEC_DW_OCC", "DW_VAC"]),
               "OCS", {"_T": "log", "DW_SEC_DW_OCC": "rsec", "DW_VAC": "vac"})
    age = wide(melodi("DS_RP_POPULATION_PRINC", RP_MEASURE="POP", TIME_PERIOD=RP, SEX="_T",
                      AGE=["_T", "Y_GE65"]),
               "AGE", {"_T": "pop_age", "Y_GE65": "pop65"})
    emp = wide(melodi("DS_RP_EMPLOI_LR_PRINC", RP_MEASURE="POP", TIME_PERIOD=RP, SEX="_T", AGE="Y15T64",
                      EDUC="_T", EMPSTA_ENQ=["1T2", "2"]),
               "EMPSTA_ENQ", {"1T2": "act", "2": "chom"})
    stat = wide(melodi("DS_RP_LOGEMENT_PRINC", RP_MEASURE="DWELLINGS", TIME_PERIOD=RP, OCS="DW_MAIN",
                       TSH=["_T", "100"], CARS="_T", BUILD_END="_T", NRG_SRC="_T", TDW="_T",
                       CARPARK="_T", NOR="_T", L_STAY="_T"),
                "TSH", {"_T": "rp", "100": "prop"})

    df = pop.join([log, age, emp, stat], how="outer").join(fi, how="outer")
    df.index = df.index.map(normalize_insee)
    # Filosofi : médiane / taux non additifs -> moyenne pondérée par la population
    df["med_x"] = df["med"] * df["pop"].fillna(1)
    df["tp_x"] = df["tp60"] * df["pop"].fillna(1)
    df["pop_fi"] = df["pop"].fillna(1).where(df["med"].notna())
    df["pop_tp"] = df["pop"].fillna(1).where(df["tp60"].notna())
    g = df.drop(columns=["med", "tp60"]).groupby(level=0).sum(min_count=1)

    def pct(num, den):
        return (g[num] / g[den] * 100).where(g[den] > 0)

    n_years = int(RP) - int(RP_OLD)
    out = pd.DataFrame(index=g.index)
    out["revenu_median"] = (g["med_x"] / g["pop_fi"]).where(g["pop_fi"] > 0)
    out["taux_pauvrete"] = (g["tp_x"] / g["pop_tp"]).where(g["pop_tp"] > 0)
    out["taux_chomage"] = pct("chom", "act")
    out["part_residences_secondaires"] = pct("rsec", "log")
    out["part_logements_vacants"] = pct("vac", "log")
    out["part_proprietaires"] = pct("prop", "rp")
    out["part_plus_65"] = pct("pop65", "pop_age")
    out["evolution_population"] = (((g["pop"] / g["pop_old"]) ** (1 / n_years) - 1) * 100).where(
        (g["pop_old"] > 0) & (g["pop"] > 0))

    cols = list(out.columns)
    rnd = {"revenu_median": 0, "evolution_population": 3}
    rows = []
    for c in load_communes():
        code = c["code_insee"]
        r = {"code_insee": code}
        if code in out.index:
            for col in cols:
                v = out.at[code, col]
                r[col] = None if pd.isna(v) else round(float(v), rnd.get(col, 2))
        rows.append(r)
    write_csv("socio", rows, cols)

    src_rp = (f"INSEE, recensement de la population {RP} (exploitation principale, géographie au "
              f"01/01/2026), via l'API Melodi")
    src_fi = (f"INSEE, Filosofi {FILOSOFI} (dispositif Fichier localisé social et fiscal, géographie au "
              f"01/01/2026), via l'API Melodi (DS_FILOSOFI_CC)")
    write_indicators("socio", [
        {"id": "revenu_median", "label": "Revenu médian", "group": "Économie", "unit": "€/an/UC",
         "type": "number", "better": None, "decimals": 0,
         "description": f"Médiane du revenu disponible par unité de consommation (niveau de vie annuel, "
                        f"après impôts et prestations sociales) en {FILOSOFI}. Vide pour les communes sous "
                        f"secret statistique (moins de 50 ménages fiscaux environ) et hors champ Filosofi "
                        f"(Mayotte, une partie des DOM).", "source": src_fi},
        {"id": "taux_pauvrete", "label": "Taux de pauvreté", "group": "Économie", "unit": "%",
         "type": "number", "better": "lower", "decimals": 1,
         "description": f"Part de la population vivant sous le seuil de pauvreté monétaire (60 % du niveau "
                        f"de vie médian national) en {FILOSOFI}. Diffusé par l'INSEE seulement pour les "
                        f"communes d'au moins 1 000 ménages fiscaux environ : vide pour la plupart des "
                        f"petites communes.", "source": src_fi},
        {"id": "taux_chomage", "label": "Taux de chômage", "group": "Économie", "unit": "%",
         "type": "number", "better": "lower", "decimals": 1,
         "description": f"Chômeurs de 15-64 ans / actifs de 15-64 ans, au sens du recensement {RP} "
                        f"(déclaratif, généralement plus élevé que le taux de chômage BIT).",
         "source": src_rp + " (DS_RP_EMPLOI_LR_PRINC)"},
        {"id": "part_residences_secondaires", "label": "Résidences secondaires", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des résidences secondaires et logements occasionnels dans l'ensemble des "
                        f"logements (recensement {RP}).", "source": src_rp + " (DS_RP_SERIE_HISTORIQUE)"},
        {"id": "part_logements_vacants", "label": "Logements vacants", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des logements vacants dans l'ensemble des logements (recensement {RP}).",
         "source": src_rp + " (DS_RP_SERIE_HISTORIQUE)"},
        {"id": "part_proprietaires", "label": "Propriétaires occupants", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des résidences principales occupées par leur propriétaire (recensement {RP}).",
         "source": src_rp + " (DS_RP_LOGEMENT_PRINC)"},
        {"id": "part_plus_65", "label": "Part des 65 ans et plus", "group": "Démographie",
         "unit": "%", "type": "number", "better": None, "decimals": 1,
         "description": f"Part des personnes de 65 ans ou plus dans la population municipale "
                        f"(recensement {RP}).", "source": src_rp + " (DS_RP_POPULATION_PRINC)"},
        {"id": "evolution_population", "label": "Évolution de la population", "group": "Démographie",
         "unit": "%/an", "type": "number", "better": None, "decimals": 2,
         "description": f"Taux de variation annuel moyen de la population municipale entre les "
                        f"recensements {RP_OLD} et {RP} ({n_years} ans), à géographie communale constante "
                        f"(01/01/2026).",
         "source": f"INSEE, recensements de la population {RP_OLD} et {RP}, série historique "
                   f"(DS_RP_SERIE_HISTORIQUE), via l'API Melodi"},
    ])

    n = len(rows)
    for col in cols:
        k = sum(1 for r in rows if r.get(col) is not None)
        print(f"  {col}: {k}/{n} ({100 * k / n:.1f} %)", file=sys.stderr)


if __name__ == "__main__":
    main()
