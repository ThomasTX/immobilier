"""Marché immobilier par commune.

- Prix médians au m² et volumes de ventes (maisons / appartements) :
  « Statistiques DVF » d'Etalab (DVF DGFiP, ventes sur les 10 derniers semestres).
- Évolution du prix au m² (12 premiers mois vs 12 derniers mois de la période,
  à composition maisons/appartements constante) : statistiques DVF mensuelles.
- Loyers d'annonce au m² (appartement, maison) : « Carte des loyers » ANIL/DHUP
  (millésime le plus récent trouvé sur data.gouv.fr).
- Rendement locatif brut estimé des appartements = loyer × 12 / prix.
- Taux de taxe foncière sur le bâti : « Fiscalité locale des particuliers » (DGFiP).

Pas de prix des terrains à bâtir : les statistiques DVF d'Etalab ne couvrent que
maisons, appartements et locaux.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import ARRONDISSEMENTS, download, load_communes, normalize_insee, write_csv, write_indicators  # noqa: E402

DVF_WHOLE = "https://data-pipeline-open.s3.sbg.io.cloud.ovh.net/dvf/stats_whole_period.csv"
DVF_MONTHLY = "https://data-pipeline-open.s3.sbg.io.cloud.ovh.net/dvf/stats_dvf.csv"
GEO_DELEGUEES = "https://geo.api.gouv.fr/communes_associees_deleguees?format=json"
DATAGOUV_SEARCH = "https://www.data.gouv.fr/api/1/datasets/?q=carte%20des%20loyers&page_size=50"
# Repli si la découverte via l'API data.gouv échoue (millésime 2025)
LOYERS_FALLBACK = {
    "year": 2025,
    "app": "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145010/pred-app-mef-dhup.csv",
    "mai": "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145039/pred-mai-mef-dhup.csv",
}
FISCA_API = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/fiscalite-locale-des-particuliers"

# Départements sans DVF (Alsace-Moselle : livre foncier ; Mayotte)
DVF_EXCLUDED_DEPS = ("57", "67", "68", "976")

PRIX_MIN, PRIX_MAX = 100, 30000
MIN_VENTES = 5            # ventes minimum pour publier un prix médian
MIN_VENTES_RENDEMENT = 10  # ventes d'appartements minimum pour le rendement
MIN_VENTES_EVOL = 20      # ventes minimum par type et par fenêtre de 12 mois pour l'évolution
LOYER_MIN, LOYER_MAX = 3, 60
MOIS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def fmt_month(ym: str) -> str:
    y, m = ym.split("-")
    return f"{MOIS_FR[int(m) - 1]} {y}"


def build_code_mapper(base_codes: set[str]):
    """Code commune (éventuellement ancien) -> code COG actuel de la liste de base, ou None."""
    try:
        data = json.loads(download(GEO_DELEGUEES, "geoapi_communes_associees_deleguees.json").read_text(encoding="utf-8"))
        parent = {d["code"]: d["chefLieu"] for d in data if d.get("chefLieu")}
    except Exception as e:  # noqa: BLE001
        print(f"  ! table des communes déléguées indisponible ({e})", file=sys.stderr)
        parent = {}

    cache: dict[str, str | None] = {}

    def mapper(code: str) -> str | None:
        if code in cache:
            return cache[code]
        c = normalize_insee(code)
        seen = 0
        while c not in base_codes and c in parent and seen < 5:
            c = normalize_insee(parent[c])
            seen += 1
        cache[code] = c if c in base_codes else None
        return cache[code]

    return mapper


def weighted_agg(df: pd.DataFrame, key: str, n_col: str, v_col: str) -> pd.DataFrame:
    """Somme des effectifs et moyenne des valeurs pondérée par les effectifs, par `key`."""
    d = df[[key, n_col, v_col]].copy()
    d[n_col] = d[n_col].fillna(0)
    ok = d[v_col].notna() & (d[v_col] > 0) & (d[n_col] > 0)
    d["w"] = np.where(ok, d[n_col], 0.0)
    d["wv"] = np.where(ok, d[v_col] * d[n_col], 0.0)
    g = d.groupby(key).agg(n=(n_col, "sum"), w=("w", "sum"), wv=("wv", "sum"))
    g["v"] = np.where(g["w"] > 0, g["wv"] / g["w"].where(g["w"] > 0, 1), np.nan)
    return g[["n", "v"]]


# ---------------------------------------------------------------- DVF
def drop_arrondissements(d: pd.DataFrame) -> pd.DataFrame:
    """Les stats DVF contiennent à la fois Paris/Lyon/Marseille et leurs arrondissements :
    on garde la ligne de la commune (vraie médiane) et on écarte les arrondissements
    pour ne pas compter deux fois les ventes."""
    parents = set(ARRONDISSEMENTS.values()) & set(d["code_geo"])
    arr = [a for a, par in ARRONDISSEMENTS.items() if par in parents]
    return d[~d["code_geo"].isin(arr)]


def dvf_whole(mapper) -> tuple[pd.DataFrame, set[str]]:
    p = download(DVF_WHOLE, "dvf_stats_whole_period.csv")
    d = pd.read_csv(p, dtype={"code_geo": str, "echelle_geo": str}, low_memory=False)
    d = drop_arrondissements(d[d["echelle_geo"] == "commune"]).copy()
    d["code"] = d["code_geo"].map(mapper)
    d = d[d["code"].notna()]
    covered = set(d["code"])
    out = pd.DataFrame(index=sorted(covered))
    for typ, src in (("maison", "maison"), ("appart", "appartement")):
        g = weighted_agg(d, "code", f"nb_ventes_whole_{src}", f"med_prix_m2_whole_{src}")
        out[f"nb_ventes_{typ}"] = g["n"]
        prix = g["v"].where((g["n"] >= MIN_VENTES) & g["v"].between(PRIX_MIN, PRIX_MAX))
        out[f"prix_m2_{typ}"] = prix.round(0)
    return out, covered


def dvf_monthly(mapper) -> tuple[pd.Series, str, str]:
    """Évolution (%) du prix au m² entre les 12 premiers et les 12 derniers mois."""
    p = download(DVF_MONTHLY, "dvf_stats_mensuelles.csv")
    cols = ["code_geo", "echelle_geo", "annee_mois",
            "nb_ventes_maison", "med_prix_m2_maison", "nb_ventes_appartement", "med_prix_m2_appartement"]
    parts = []
    for ch in pd.read_csv(p, usecols=cols, dtype={"code_geo": str, "echelle_geo": str, "annee_mois": str},
                          chunksize=500_000):
        parts.append(ch[ch["echelle_geo"] == "commune"])
    m = drop_arrondissements(pd.concat(parts, ignore_index=True))
    months = sorted(m["annee_mois"].dropna().unique())
    first, last = months[:12], months[-12:]
    m["code"] = m["code_geo"].map(mapper)
    m = m[m["code"].notna()]

    evols, weights = [], []
    for src in ("maison", "appartement"):
        res = {}
        for name, win in (("a", first), ("b", last)):
            sub = m[m["annee_mois"].isin(win)]
            res[name] = weighted_agg(sub, "code", f"nb_ventes_{src}", f"med_prix_m2_{src}")
        j = res["a"].join(res["b"], lsuffix="_a", rsuffix="_b", how="inner")
        ok = (j["n_a"] >= MIN_VENTES_EVOL) & (j["n_b"] >= MIN_VENTES_EVOL) & j["v_a"].between(PRIX_MIN, PRIX_MAX) \
            & j["v_b"].between(PRIX_MIN, PRIX_MAX)
        j = j[ok]
        evols.append((j["v_b"] / j["v_a"] - 1) * 100)
        weights.append(j["n_a"] + j["n_b"])
    e = pd.concat(evols, axis=1).fillna(0)
    w = pd.concat(weights, axis=1).fillna(0)
    evol = (e * w).sum(axis=1) / w.sum(axis=1)
    evol = evol.where(evol.between(-60, 150)).round(1)
    return evol, months[0], months[-1]


# ---------------------------------------------------------------- Loyers
def loyers_sources() -> dict:
    try:
        data = json.loads(download(DATAGOUV_SEARCH, "datagouv_search_carte_loyers.json", max_age_days=7)
                          .read_text(encoding="utf-8"))
        best = None
        for ds in data.get("data", []):
            mt = re.search(r"carte des loyers.*par commune en (\d{4})", ds.get("title", ""), re.I)
            if not mt:
                continue
            res = {}
            for r in ds.get("resources", []):
                url = r.get("url", "")
                if url.endswith("pred-app-mef-dhup.csv"):
                    res["app"] = url
                elif url.endswith("pred-mai-mef-dhup.csv"):
                    res["mai"] = url
            if "app" in res and "mai" in res and (best is None or int(mt.group(1)) > best["year"]):
                best = {"year": int(mt.group(1)), **res}
        if best:
            return best
    except Exception as e:  # noqa: BLE001
        print(f"  ! découverte Carte des loyers impossible ({e}), repli sur 2025", file=sys.stderr)
    return LOYERS_FALLBACK


def read_loyers(url: str, year: int, kind: str, mapper) -> pd.Series:
    p = download(url, f"carte_loyers_{year}_{kind}.csv")
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    from io import StringIO
    d = pd.read_csv(StringIO(text), sep=";", decimal=",", dtype={"INSEE_C": str})
    d["code"] = d["INSEE_C"].str.strip().map(mapper)
    d = d[d["code"].notna()].copy()
    # Arrondissements (Paris/Lyon/Marseille) : moyenne pondérée par le nb d'annonces observées
    d["nbobs_com"] = pd.to_numeric(d["nbobs_com"], errors="coerce").fillna(0) + 1
    g = weighted_agg(d, "code", "nbobs_com", "loypredm2")["v"]
    return g.where(g.between(LOYER_MIN, LOYER_MAX)).round(2)


# ---------------------------------------------------------------- Taxe foncière
def taxe_fonciere(mapper) -> tuple[pd.DataFrame, str | None]:
    try:
        q = urllib.parse.urlencode({"select": "exercice,count(*) as n", "group_by": "exercice", "limit": 50})
        years = json.loads(download(f"{FISCA_API}/records?{q}", "fiscalite_locale_exercices.json", max_age_days=7)
                           .read_text(encoding="utf-8"))
        cand = [r["exercice"] for r in years["results"] if r.get("n", 0) > 30000]
        year = max(cand)
        q = urllib.parse.urlencode({"where": f'exercice="{year}"', "select": "insee_com,e12vote,taux_global_tfb",
                                    "delimiter": ";"})
        p = download(f"{FISCA_API}/exports/csv?{q}", f"fiscalite_locale_{year}.csv")
        d = pd.read_csv(p, sep=";", dtype={"insee_com": str})
    except Exception as e:  # noqa: BLE001
        print(f"  ! fiscalité locale indisponible ({e})", file=sys.stderr)
        return pd.DataFrame(columns=["taux_tfb", "taux_tfb_commune"]), None
    d["code"] = d["insee_com"].map(mapper)
    d = d[d["code"].notna()]
    d = d.groupby("code").agg(taux_tfb=("taux_global_tfb", "mean"), taux_tfb_commune=("e12vote", "mean"))
    d["taux_tfb"] = d["taux_tfb"].where(d["taux_tfb"].between(0.5, 100))
    d["taux_tfb_commune"] = d["taux_tfb_commune"].where(d["taux_tfb_commune"].between(0, 100))
    return d.round(2), year


# ---------------------------------------------------------------- main
def main():
    base = [c["code_insee"] for c in load_communes()]
    base_set = set(base)
    mapper = build_code_mapper(base_set)

    whole, dvf_covered = dvf_whole(mapper)
    evol, m0, m1 = dvf_monthly(mapper)
    periode = f"{fmt_month(m0)} – {fmt_month(m1)}"
    dvf_src = f"DVF (DGFiP) via « Statistiques DVF » Etalab, ventes {periode}"

    ls = loyers_sources()
    loyer_app = read_loyers(ls["app"], ls["year"], "app", mapper)
    loyer_mai = read_loyers(ls["mai"], ls["year"], "mai", mapper)
    loyer_src = f"Carte des loyers {ls['year']} (ANIL/DHUP), estimations ANIL à partir des données SeLoger et leboncoin"

    tfb, tfb_year = taxe_fonciere(mapper)

    df = pd.DataFrame(index=base)
    df = df.join(whole)
    # 0 vente quand la commune est couverte par DVF ; vide hors champ DVF
    dep_excl = pd.Series(df.index, index=df.index).str.startswith(DVF_EXCLUDED_DEPS)
    in_dvf = pd.Series(df.index.isin(list(dvf_covered)), index=df.index) & ~dep_excl
    for c in ("nb_ventes_maison", "nb_ventes_appart"):
        df[c] = df[c].fillna(0).where(in_dvf)
    for c in ("prix_m2_maison", "prix_m2_appart"):
        df[c] = df[c].where(in_dvf)
    df["evol_prix_m2"] = evol.reindex(df.index).where(in_dvf)
    df["loyer_m2_appart"] = loyer_app.reindex(df.index)
    df["loyer_m2_maison"] = loyer_mai.reindex(df.index)
    rend = df["loyer_m2_appart"] * 12 / df["prix_m2_appart"] * 100
    rend = rend.where(df["nb_ventes_appart"] >= MIN_VENTES_RENDEMENT)
    df["rendement_brut_appart"] = rend.where(rend.between(1, 25)).round(2)
    df["taux_tfb"] = tfb["taux_tfb"].reindex(df.index) if len(tfb) else np.nan
    df["taux_tfb_commune"] = tfb["taux_tfb_commune"].reindex(df.index) if len(tfb) else np.nan

    columns = ["prix_m2_maison", "prix_m2_appart", "nb_ventes_maison", "nb_ventes_appart", "evol_prix_m2",
               "loyer_m2_appart", "loyer_m2_maison", "rendement_brut_appart", "taux_tfb", "taux_tfb_commune"]
    rows = []
    for code, r in df[columns].iterrows():
        row = {"code_insee": code}
        for c in columns:
            v = r[c]
            if pd.isna(v):
                row[c] = None
            elif c.startswith(("nb_ventes", "prix_m2")):
                row[c] = int(round(v))
            else:
                row[c] = float(v)
        rows.append(row)
    write_csv("immobilier", rows, columns)

    first_end = (pd.Period(m0, "M") + 11).strftime("%Y-%m")
    last_start = (pd.Period(m1, "M") - 11).strftime("%Y-%m")
    win_a = f"{fmt_month(m0)} – {fmt_month(first_end)}"
    win_b = f"{fmt_month(last_start)} – {fmt_month(m1)}"

    G = "Immobilier"
    ind = [
        {"id": "prix_m2_maison", "label": "Prix médian au m² (maisons)", "group": G, "unit": "€/m²", "type": "number",
         "better": "lower", "decimals": 0, "scale": "log", "source": dvf_src,
         "description": f"Prix de vente médian au m² des maisons ({periode}). Vide si moins de {MIN_VENTES} ventes."},
        {"id": "prix_m2_appart", "label": "Prix médian au m² (appartements)", "group": G, "unit": "€/m²",
         "type": "number", "better": "lower", "decimals": 0, "scale": "log", "source": dvf_src,
         "description": f"Prix de vente médian au m² des appartements ({periode}). Vide si moins de {MIN_VENTES} ventes."},
        {"id": "nb_ventes_maison", "label": "Ventes de maisons (5 ans)", "group": G, "unit": "ventes",
         "type": "number", "better": None, "decimals": 0, "scale": "log", "source": dvf_src,
         "description": f"Nombre de ventes de maisons enregistrées dans DVF ({periode}). Hors Alsace-Moselle et Mayotte."},
        {"id": "nb_ventes_appart", "label": "Ventes d'appartements (5 ans)", "group": G, "unit": "ventes",
         "type": "number", "better": None, "decimals": 0, "scale": "log", "source": dvf_src,
         "description": f"Nombre de ventes d'appartements enregistrées dans DVF ({periode}). Hors Alsace-Moselle et Mayotte."},
        {"id": "evol_prix_m2", "label": "Évolution du prix au m² (4 ans)", "group": G, "unit": "%", "type": "number",
         "better": None, "decimals": 1, "source": f"DVF (DGFiP) via « Statistiques DVF » mensuelles Etalab ({periode})",
         "description": (f"Variation du prix au m² entre {win_a} et {win_b} (médianes mensuelles pondérées), "
                         f"maisons et appartements combinés à composition constante. "
                         f"Exige au moins {MIN_VENTES_EVOL} ventes par type et par période.")},
        {"id": "loyer_m2_appart", "label": "Loyer d'annonce au m² (appartement)", "group": G, "unit": "€/m²",
         "type": "number", "better": "lower", "decimals": 1, "source": loyer_src,
         "description": ("Loyer d'annonce estimé, charges comprises, d'un appartement non meublé type de 52 m² "
                         f"(T3 {ls['year']}). Estimé à l'échelle d'un regroupement de communes quand les annonces "
                         "sont trop peu nombreuses.")},
        {"id": "loyer_m2_maison", "label": "Loyer d'annonce au m² (maison)", "group": G, "unit": "€/m²",
         "type": "number", "better": "lower", "decimals": 1, "source": loyer_src,
         "description": ("Loyer d'annonce estimé, charges comprises, d'une maison non meublée type de 92 m² "
                         f"(T3 {ls['year']}). Estimé à l'échelle d'un regroupement de communes quand les annonces "
                         "sont trop peu nombreuses.")},
        {"id": "rendement_brut_appart", "label": "Rendement locatif brut (appartement)", "group": G, "unit": "%",
         "type": "number", "better": "higher", "decimals": 1,
         "source": f"Calcul : Carte des loyers {ls['year']} et DVF ({periode})",
         "description": ("Estimation : loyer d'annonce au m² × 12 / prix médian de vente au m² des appartements. "
                         f"Charges comprises, avant taxe foncière et vacance. Vide si moins de {MIN_VENTES_RENDEMENT} ventes.")},
    ]
    if tfb_year:
        fsrc = f"DGFiP, « Fiscalité locale des particuliers », exercice {tfb_year}"
        ind += [
            {"id": "taux_tfb", "label": "Taux de taxe foncière (bâti)", "group": "Fiscalité", "unit": "%",
             "type": "number", "better": "lower", "decimals": 2, "source": fsrc,
             "description": ("Taux global de taxe foncière sur les propriétés bâties : commune + intercommunalité "
                             "+ taxes spéciales et GEMAPI. À appliquer à la valeur locative cadastrale.")},
            {"id": "taux_tfb_commune", "label": "Taux communal de taxe foncière (bâti)", "group": "Fiscalité",
             "unit": "%", "type": "number", "better": "lower", "decimals": 2, "source": fsrc,
             "description": "Taux voté par la commune pour la taxe foncière sur les propriétés bâties (part communale seule)."},
        ]
    write_indicators("immobilier", [i for i in ind if i["id"] in columns])


if __name__ == "__main__":
    main()
